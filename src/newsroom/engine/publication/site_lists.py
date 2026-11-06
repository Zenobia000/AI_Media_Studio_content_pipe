"""The website's lists: the selected timeline, the public pool with search, and the hot ranking cards. Ports of publication/{timeline,pool (site
part),hot (extras and strip)}.ts and loadHot from stories.ts."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from newsroom.engine.lib.cache import cached_by_key, shared_search
from newsroom.engine.lib.cursor import InvalidCursorError, decode_cursor, encode_cursor, query_binding
from newsroom.engine.lib.ids import stable_json
from newsroom.engine.media.imgproxy import proxied_image, proxied_image_set
from newsroom.engine.publication.items import (
    ITEM_COLUMNS,
    ITEM_FROM,
    filter_sql,
    seat_holders,
    to_feed_item_summary,
)  # fmt: skip
from newsroom.engine.publication.pool import search_terms, with_search_capacity
from newsroom.engine.publication.scope import (
    browsed_condition,
    evidence_condition,
    foldable_selection_condition,
    listed_condition,
    own_fact_evidence_condition,
    scope_params,
    selected_condition,
)  # fmt: skip
from newsroom.engine.publication.stories import (
    REPRESENTATIVE_COLUMNS,
    latest_hot_ranking,
    pick_representative,
    public_source_name,
    story_texts,
)  # fmt: skip
from newsroom.engine.publication.topics import query_entity_tag
from newsroom.engine.publication.v1 import iso
from newsroom.engine.time import beijing_date, beijing_midnight

POOL_PAGE_SIZE = 40
POOL_MAX_PAGES = 50
HOT_FACE_LIMIT = 6
TIER_ORDER = ["T1", "T1_5", "T2"]


def tier_rank(tier: str | None) -> int:
    """Participants in the 精選組 order: T1 before T1.5 before T2, then everything else."""
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def _epoch_ms(d: datetime) -> int:
    return (d - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


# ── timeline ────────────────────────────────────────────────────────────────────────────


def _binding(q: dict) -> str:
    return query_binding({"c": q["channel"], "k": q["category"], "t": q["tag"]})


def _query_grouped_anchors(pool, q: dict, now: datetime) -> list[dict]:
    """The selected set grouped into cards (fact or standalone item) with their anchor times, newest first."""
    fsql, fparams = filter_sql(q["channel"], q["category"], q["tag"])
    with pool.connection() as conn:
        rows = conn.execute(
            f"""WITH base AS MATERIALIZED (
                  SELECT p.sort_at, CASE WHEN p.fact_id IS NOT NULL AND {own_fact_evidence_condition()} AND {foldable_selection_condition()}
                    THEN 'f' || p.fact_id::text ELSE 'a' || p.article_id END AS gk
                  FROM publications p WHERE {selected_condition()} {fsql}
                )
                SELECT gk, min(sort_at) AS anchor_at FROM base GROUP BY gk ORDER BY anchor_at DESC, gk COLLATE "C" DESC""",
            {**scope_params(now), **fparams},
        ).fetchall()
    return [{"gk": r["gk"], "anchor": _epoch_ms(r["anchor_at"])} for r in rows]


_anchors: dict[int, object] = {}


def _grouped_anchors(pool, q: dict) -> list[dict]:
    """Every timeline page and its day counts read this list; it is kept for five seconds per filter scope."""
    entry = _anchors.get(id(pool))
    if entry is None:
        entry = _anchors[id(pool)] = cached_by_key(_binding, lambda qq: _query_grouped_anchors(pool, qq, datetime.now(UTC)), 5000, 5000, 50)
    return entry(q)  # type: ignore[operator]


def count_timeline_days(grouped: list[dict], days: list[str]) -> dict[str, int]:
    """Counts requested Beijing days over anchors already sorted newest first."""

    def first_below(bound: int) -> int:
        lo, hi = 0, len(grouped)
        while lo < hi:
            mid = (lo + hi) // 2
            if grouped[mid]["anchor"] >= bound:
                lo = mid + 1
            else:
                hi = mid
        return lo

    counts: dict[str, int] = {}
    for day in days:
        start = _epoch_ms(beijing_midnight(day))
        count = first_below(start) - first_below(start + 86_400_000)
        if count:
            counts[day] = count
    return counts


def load_timeline(pool, q: dict, cursor: str | None, limit: int, now: datetime | None = None) -> dict:
    fixed_clock = now is not None
    now = now or datetime.now(UTC)
    limit = min(max(limit, 1), 40)
    bind = _binding(q)
    after = None
    if cursor:
        c = decode_cursor("tl2", cursor)
        if c.get("b") != bind or not isinstance(c.get("a"), (int, float)) or not isinstance(c.get("g"), str):
            raise InvalidCursorError("cursor does not match this query")
        after = (c["a"], c["g"])
    grouped = _query_grouped_anchors(pool, q, now) if fixed_clock else _grouped_anchors(pool, q)
    if after:
        start = next((i for i, g in enumerate(grouped) if g["anchor"] < after[0] or (g["anchor"] == after[0] and g["gk"] < after[1])), -1)
    else:
        start = 0
    groups = [] if start < 0 else grouped[start : start + limit + 1]
    page = groups[:limit]
    has_more = len(groups) > limit
    fact_ids = [int(g["gk"][1:]) for g in page if g["gk"].startswith("f")]
    fsql, fparams = filter_sql(q["channel"], q["category"], q["tag"])
    members: list[dict] = []
    pool_rows: list[dict] = []
    fact_info: dict[int, dict] = {}
    with pool.connection() as conn:
        if fact_ids:
            # Read only ranking fields for the whole group; bodies, media and translations are hydrated for this page's representatives.
            members = conn.execute(
                f"""SELECT p.fact_id, p.article_id AS id, p.body_mode, p.score, p.timeline_at, {REPRESENTATIVE_COLUMNS}
                    FROM publications p JOIN sources s ON s.id = p.source_id LEFT JOIN facts f ON f.id = p.fact_id
                    WHERE p.fact_id = ANY(%(facts)s) AND {selected_condition()} AND {own_fact_evidence_condition()} AND {foldable_selection_condition()} {fsql}""",
                {**scope_params(now), **fparams, "facts": fact_ids},
            ).fetchall()
            # Public pool reports linked to the given facts, under the same filters: the sets "另外 N 家也報道了" expands.
            pool_rows = conn.execute(
                f"""SELECT DISTINCT f.id AS fact_id, p.article_id, p.source_id FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id
                    JOIN publications p ON p.article_id = fa.article_id
                    WHERE f.id = ANY(%(facts)s) AND {evidence_condition()} AND {listed_condition()} {fsql}""",
                {**scope_params(now), **fparams, "facts": fact_ids},
            ).fetchall()
            fact_info = {f["id"]: f for f in conn.execute("SELECT id, public_id FROM facts WHERE id = ANY(%s)", (fact_ids,)).fetchall()}
    planned = []
    for g in page:
        anchor_at = iso(datetime.fromtimestamp(g["anchor"] / 1000, UTC))
        if g["gk"].startswith("a"):
            planned.append({"key": g["gk"], "anchorAt": anchor_at, "id": g["gk"][1:], "group": None})
            continue
        fact_id = int(g["gk"][1:])
        candidates = [m for m in members if m["fact_id"] == fact_id]
        if not candidates:
            continue
        rep = pick_representative(candidates)
        reports = [r for r in pool_rows if r["fact_id"] == fact_id]
        rep_source = next((r["source_id"] for r in reports if r["article_id"] == rep["id"]), None)
        group: dict[str, Any] = {
            "factId": str(fact_info[fact_id]["public_id"]) if fact_id in fact_info else str(fact_id),
            "additionalSourceCount": len({r["source_id"] for r in reports if r["source_id"] != rep_source}),
            "reportCount": len({r["article_id"] for r in reports}),
            "memberIds": list(dict.fromkeys(m["id"] for m in candidates)),
        }
        planned.append({"key": g["gk"], "anchorAt": anchor_at, "id": rep["id"], "group": group if group["reportCount"] > 1 else None})
    rows: dict[str, dict] = {}
    if planned:
        with pool.connection() as conn:
            # Recheck scope when hydrating: a withdrawal may commit after the narrow representative read.
            for row in conn.execute(
                f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE p.article_id = ANY(%(ids)s) AND {selected_condition()} {fsql}",
                {**scope_params(now), **fparams, "ids": [p["id"] for p in planned]},
            ).fetchall():
                rows[row["id"]] = row
    cards = [
        {"key": p["key"], "anchorAt": p["anchorAt"], "item": to_feed_item_summary(rows[p["id"]]), "group": p["group"]}
        for p in planned
        if p["id"] in rows
    ]
    days = list(dict.fromkeys(beijing_date(datetime.fromtimestamp(g["anchor"] / 1000, UTC)) for g in page))  # insertion order, as a Set keeps it
    day_counts = count_timeline_days(grouped, days)
    last = page[-1] if page else None
    next_cursor = encode_cursor("tl2", {"a": last["anchor"], "g": last["gk"], "b": bind}) if has_more and last else None
    return {
        "filters": {"channel": q["channel"], "category": q["category"], "tag": q["tag"]},
        "cards": cards,
        "nextCursor": next_cursor,
        "dayCounts": day_counts,
    }


# ── the pool and search ─────────────────────────────────────────────────────────────────


def _like(col: str, i: int) -> str:
    return f"{col} LIKE %(like_{i})s"


def _list_count(pool, f: dict, now: datetime) -> int:
    fsql, fparams = filter_sql(f["channel"], f["category"], f["tag"])
    with pool.connection() as conn:
        return int(
            conn.execute(
                f"SELECT count(*) AS n FROM (SELECT 1 FROM publications p WHERE {browsed_condition()} {fsql} LIMIT %(cap)s) t",
                {**scope_params(now), **fparams, "cap": POOL_MAX_PAGES * POOL_PAGE_SIZE},
            ).fetchone()["n"]
        )


_pool_totals: dict[int, Callable] = {}


def _pool_total(pool, f: dict) -> int:
    """Without a search the total only sets the page count: it is reused for 30 seconds per filter."""
    entry = _pool_totals.get(id(pool))
    if entry is None:

        def name(ff: dict) -> str:
            return stable_json([ff["channel"], ff["category"], ff["tag"]])

        def load(ff: dict) -> int:
            return _list_count(pool, ff, datetime.now(UTC))

        entry = _pool_totals[id(pool)] = cached_by_key(name, load, 30_000, 30_000, 200)
    return entry(f)


def query_pool(pool, query: dict, now: datetime, fixed_clock: bool) -> dict:
    page = min(max(query.get("page") or 1, 1), POOL_MAX_PAGES)
    q = (query.get("q") or "").strip() or None
    tab = "relevance" if q and query.get("tab") == "relevance" else "time"
    terms = search_terms(q) if q else []
    entity_tag = query_entity_tag(q) if q else None
    fsql, fparams = filter_sql(query["channel"], query["category"], query["tag"])
    offset = (page - 1) * POOL_PAGE_SIZE
    cap = POOL_MAX_PAGES * POOL_PAGE_SIZE
    params: dict = {
        **scope_params(now),
        **fparams,
        "limit": POOL_PAGE_SIZE,
        "offset": offset,
        "cap": cap,
        "entity_tag": [entity_tag] if entity_tag else None,
    }
    for i, t in enumerate(terms):
        params[f"like_{i}"] = f"%{t}%"
    direct_match = "".join(f" AND {_like('p.search_text', i)}" for i in range(len(terms)))
    match_or_subject = (lambda text: f"AND (p.tags @> %(entity_tag)s::text[] OR (TRUE {text}))") if entity_tag else (lambda text: text)

    def run(conn):
        if not q:
            # Page ids from the timeline index first, then the joins for those rows only.
            rows = conn.execute(
                f"""WITH page AS (SELECT p.article_id FROM publications p WHERE {browsed_condition()} {fsql}
                      ORDER BY p.timeline_at DESC, p.article_id DESC LIMIT %(limit)s OFFSET %(offset)s)
                    SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE p.article_id IN (SELECT article_id FROM page) ORDER BY p.timeline_at DESC, p.article_id DESC""",
                params,
            ).fetchall()
            # A fixed clock (tests, replays) never shares cached totals.
            return rows, _list_count(pool, query, now) if fixed_clock else _pool_total(pool, query)
        if tab == "relevance":
            split_fields = (
                len(terms) == 1 and _three_letters(terms[0]) and query["channel"] in (None, "all") and not query["category"] and not query["tag"]
            )
            part_score = "0" + "".join(
                f" + (CASE WHEN {_like('ps.direct', i)} THEN 3 ELSE 0 END) + (CASE WHEN {_like('ps.body', i)} THEN 1 ELSE 0 END)"
                for i in range(len(terms))
            )
            title_score = "0" + "".join(f" + (CASE WHEN {_like('lower(p.title)', i)} THEN 6 ELSE 0 END)" for i in range(len(terms)))
            any_match = "TRUE" + "".join(f" AND ({_like('ps.direct', i)} OR {_like('ps.body', i)})" for i in range(len(terms)))
            if split_fields:
                matches = """SELECT coalesce(d.article_id, b.article_id) AS article_id,
                    (CASE WHEN d.article_id IS NOT NULL THEN 3 ELSE 0 END) + (CASE WHEN b.article_id IS NOT NULL THEN 1 ELSE 0 END) AS part
                  FROM (SELECT article_id FROM pool_search WHERE direct LIKE %(like_0)s) d
                  FULL JOIN (SELECT article_id FROM pool_search WHERE body LIKE %(like_0)s) b ON b.article_id = d.article_id"""
            else:
                matches = f"SELECT ps.article_id, ({part_score}) AS part FROM pool_search ps WHERE {any_match}"
            tie = "(CASE WHEN p.selected AND p.seat THEN 1000 ELSE 0 END) + coalesce(p.score, 0)"
            if entity_tag:
                scored = f"""SELECT p.article_id, p.timeline_at, max(matches.part) + ({title_score}) + (CASE WHEN p.tags @> %(entity_tag)s::text[] THEN 10 ELSE 0 END) AS rel, {tie} AS tie
                  FROM (SELECT article_id, part FROM matches UNION ALL SELECT article_id, 0 FROM publications WHERE tags @> %(entity_tag)s::text[]) matches
                  JOIN publications p ON p.article_id = matches.article_id
                  WHERE {listed_condition()} {fsql}
                  GROUP BY p.article_id, p.timeline_at, p.title, p.tags, p.selected, p.seat, p.score"""
            else:
                scored = f"""SELECT p.article_id, p.timeline_at, matches.part + ({title_score}) AS rel, {tie} AS tie
                  FROM matches JOIN publications p ON p.article_id = matches.article_id JOIN sources s ON s.id = p.source_id
                  WHERE {listed_condition()} {fsql}"""
            result = conn.execute(
                f"""WITH matches AS {"MATERIALIZED" if split_fields else "NOT MATERIALIZED"} ({matches}), scored AS MATERIALIZED ({scored}
                    ), page AS MATERIALIZED (
                      SELECT article_id, rel, tie FROM scored ORDER BY rel DESC, tie DESC, timeline_at DESC, article_id DESC LIMIT %(limit)s OFFSET %(offset)s
                    ), total AS (SELECT count(*) AS n FROM (SELECT 1 FROM scored LIMIT %(cap)s) capped)
                    SELECT hydrated.*, total.n AS total FROM total LEFT JOIN LATERAL (
                      SELECT {ITEM_COLUMNS}, page.rel, page.tie {ITEM_FROM} JOIN page ON page.article_id = p.article_id
                    ) hydrated ON true ORDER BY hydrated.rel DESC, hydrated.tie DESC, hydrated.timeline_at DESC, hydrated.id DESC""",
                params,
            ).fetchall()
            return [r for r in result if r["id"] is not None], int(result[0]["total"])
        # Default search: newest first straight from the timeline index; the total from the pool's search rows.
        rows = conn.execute(
            f"""WITH page AS (SELECT p.article_id FROM publications p WHERE {listed_condition()} {fsql} {match_or_subject(direct_match)}
                  ORDER BY p.timeline_at DESC, p.article_id DESC LIMIT %(limit)s OFFSET %(offset)s)
                SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE p.article_id IN (SELECT article_id FROM page) ORDER BY p.timeline_at DESC, p.article_id DESC""",
            params,
        ).fetchall()
        direct = "".join(f" AND {_like('ps.direct', i)}" for i in range(len(terms)))
        n = conn.execute(
            f"""SELECT count(*) AS n FROM (SELECT 1 FROM pool_search ps JOIN publications p ON p.article_id = ps.article_id
                  WHERE {listed_condition()} {fsql} {match_or_subject(direct)} LIMIT %(cap)s) t""",
            params,
        ).fetchone()["n"]
        return rows, int(n)

    with pool.connection() as conn:
        rows, total = with_search_capacity(conn, run) if q else run(conn)
    today = beijing_date(now)
    with pool.connection() as conn:
        meta = conn.execute(
            f"""SELECT (SELECT count(*) FROM publications p WHERE {browsed_condition()} AND p.timeline_at >= %(midnight)s {fsql}) AS today_count,
                  (SELECT max(p.updated_at) FROM publications p WHERE p.eligible) AS updated_at""",
            {**scope_params(now), **fparams, "midnight": beijing_midnight(today)},
        ).fetchone()
        holders = seat_holders(conn, rows, now)
    return {
        "filters": {"channel": query["channel"], "category": query["category"], "tag": query["tag"], "q": q, "tab": tab},
        "items": [({**to_feed_item_summary(r), "reason": None, "sameEvent": holders[r["id"]]} if r["id"] in holders else to_feed_item_summary(r)) for r in rows],
        "page": page,
        "pageCount": min(POOL_MAX_PAGES, max(1, math.ceil(total / POOL_PAGE_SIZE))),
        "total": total,
        "todayCount": int(meta["today_count"]),
        "freshness": iso(meta["updated_at"] or now),
    }  # fmt: skip


def _three_letters(term: str) -> bool:
    import regex  # noqa: PLC0415

    return bool(regex.search(r"[\p{L}\p{N}]{3}", term))


_pool_searches: dict[int, Callable] = {}


def load_pool(pool, query: dict, now: datetime | None = None) -> dict:
    """Identical searches share their read before taking search capacity."""
    entry = _pool_searches.get(id(pool))
    if entry is None:

        def name(k: dict) -> str:
            return stable_json([k["channel"], k["category"], k["tag"], k.get("q"), k.get("tab"), k.get("page")])

        def load(k: dict, at: datetime) -> dict:
            return query_pool(pool, k, at, False)

        def is_search(k: dict) -> bool:
            return bool((k.get("q") or "").strip())

        entry = _pool_searches[id(pool)] = shared_search(name, load, is_search)
    if now is not None:
        return query_pool(pool, query, now, True)
    return entry(query)


# ── hot ranking cards ───────────────────────────────────────────────────────────────────


def _hot_extras(pool, ranking: dict) -> dict:
    """What the web adds to a ranking entry: participants with proxied faces, the digest and the latest turn."""
    ids = [e["storyId"] for e in ranking["entries"]]
    with pool.connection() as conn:
        faces = conn.execute(
            """SELECT DISTINCT ON (s.id) s.name, s.icon_url, a.x_post->>'avatarUrl' AS avatar
               FROM story_signals ss JOIN sources s ON s.id = ss.source_id
               LEFT JOIN articles a ON a.id = CASE WHEN s.icon_url IS NULL THEN ss.article_id END AND a.x_post ? 'avatarUrl'
               WHERE ss.story_id = ANY(%s) ORDER BY s.id, (a.id IS NULL), a.discovered_at DESC, a.id DESC""",
            (ids,),
        ).fetchall()
        texts = story_texts(conn, ids, datetime.now(UTC))
    face = {f["name"]: f["icon_url"] or f["avatar"] for f in faces}
    summaries = {
        i: {"summary": t["digest"] if t["digest"] is not None else t["summary"], "latest": t["latest"]["title"] if t["latest"] else None}
        for i, t in texts.items()
    }

    def participants(e: dict) -> list[dict]:
        people = [{"p": p, "i": i, "icon": face.get(p["name"])} for i, p in enumerate(e["participants"])]
        people.sort(key=lambda x: (0 if x["p"]["kind"] == "editorial" else 1, tier_rank(x["p"].get("tier")), 0 if x["icon"] else 1, x["i"]))
        # A publisher's blog and its X account share a public name: listed once, with the first face.
        named: dict[str, dict] = {}
        for person in people:
            named.setdefault(public_source_name(person["p"]["name"]), person)
        out = []
        for i, (name, person) in enumerate(named.items()):
            entry = {"name": name, "kind": person["p"]["kind"]}
            if person["p"]["kind"] == "editorial" and i < HOT_FACE_LIMIT:
                entry["iconUrl"] = proxied_image(person["icon"], "avatar")
                src_set = proxied_image_set(person["icon"], "avatar")
                if src_set:
                    entry["iconSrcSet"] = src_set
            out.append(entry)
        return out

    return {"participants": participants, "text": lambda e: summaries.get(e["storyId"], {"summary": None, "latest": None})}


def load_hot_strip(pool) -> list[dict] | None:
    """Home "current hot" strip: 3–5 entries from the same ranking, hidden when there are fewer than 3."""
    with pool.connection() as conn:
        ranking = latest_hot_ranking(conn)
    if not ranking or len(ranking["entries"]) < 3:
        return None
    extras = _hot_extras(pool, ranking)
    return [
        {"rank": e["rank"], "title": e["title"], "heat": e["heat"], "trend": e["trend"], "storyPublicId": e["storyPublicId"], "itemId": e.get("representativeItemId"),
         "participants": extras["participants"](e), "participantCount": e["participantCount"]}
        for e in ranking["entries"][:5]
    ]  # fmt: skip


def hot_search_links(pool) -> list[dict]:
    """Search links follow the same current public evidence as every hot-ranking surface."""
    with pool.connection() as conn:
        ranking = latest_hot_ranking(conn)
    return [{"rank": e["rank"], "title": e["title"], "to": f"/story/{e['storyPublicId']}"} for e in (ranking["entries"] if ranking else [])[:5]]
