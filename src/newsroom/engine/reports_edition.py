"""What an issue carries. A period carries its selected reports: public, not backfill, attributed by the later of arrival and release, and not released long after
they happened. The daily is computed by rule, without a model judging anything: one entry per event, its most authoritative report with the event's other developments
listed under it; an event an earlier issue covered comes back only with a new fact; an event with an official post and three or more independent participants is
carried even when none of its reports was selected; entries rank by importance (score, the day's independent participants, first-hand), and the first one leads.
Sections are the industry pack's. Port of reports/edition.ts."""

from __future__ import annotations

import math

from newsroom.engine import taxonomy as T
from newsroom.engine.lib.text import collate_key
from newsroom.engine.publication.scope import evidence_condition, listed_condition, own_fact_evidence_condition, scope_params
from newsroom.engine.publication.site_story import current_signals, signal_params
from newsroom.engine.publication.stories import REPRESENTATIVE_COLUMNS, pick_representative, representative_priority
from newsroom.engine.publication.v1 import iso
from newsroom.engine.time import add_days

MAIN_ENTRIES = 12  # a daily's size: the entries readers get in full, and the one-line flashes after them
FLASH_ENTRIES = 10
PER_SOURCE = 2  # no source fills a daily: at most this many main entries lead with the same source
FOLLOW_UP_SOURCES = 4  # a follow-up of a covered event takes a full entry when this many sources carry its new facts
MEMORY_DAYS = 7  # earlier issues a daily remembers


def section_order() -> list[str]:
    return list(dict.fromkeys(c["section"] for c in T.categories()))


def section_of(category: str | None) -> str:
    """An item without a category goes into the section of the industry category, else into the last one."""
    by_key = {c["key"]: c["section"] for c in T.categories()}
    default = by_key.get("industry") or section_order()[-1]
    return by_key.get(category or "", default)


def _commentary() -> set[str]:
    """The pack's `commentary` categories (how-tos, opinions): a follow-up of theirs is a flash, whoever wrote it."""
    return {c["key"] for c in T.categories() if "commentary" in c}


REPORT_FIELDS = f"""p.article_id AS id, p.title, p.summary, p.url, p.category, p.tags, p.score, (s.tier = 'T1') AS first_party, p.body_mode, p.timeline_at,
  {REPRESENTATIVE_COLUMNS}, s.id AS source_id, s.name AS source_name, s.kind AS source_kind,
  f.id AS fact_id, f.public_id AS fact_public_id, st.id AS story_id, st.public_id::text AS story_public_id"""


def _num(v):
    """A number as JavaScript writes it: 91 for 91.0."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def period_reports(pool, start, end) -> list[dict]:
    """The selected reports a period [start, end) carries. Each counts once, in the period readers first saw it: by arrival, or by release when that came later
    (a release after the cutoff belongs to the next issue, never to one already out). A report that arrived more than a day before the period began and was released
    only within it is old news there."""
    with pool.connection() as conn, conn.transaction():
        # Wait for in-flight releases and keep later ones outside this snapshot. The following SELECT gets a fresh READ COMMITTED snapshot; model calls and report
        # writes happen after the lock ends.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('report_candidates'))")
        return conn.execute(
            f"""SELECT {REPORT_FIELDS}
                FROM publications p JOIN sources s ON s.id = p.source_id
                LEFT JOIN facts f ON f.id = p.fact_id AND {own_fact_evidence_condition()}
                LEFT JOIN stories st ON st.id = f.story_id
                WHERE p.visibility = 'public' AND p.selected AND NOT p.backfill
                  AND (
                    (p.visible_after <= p.timeline_at AND p.timeline_at >= %(start)s AND p.timeline_at < %(end)s)
                    OR (p.visible_after > p.timeline_at AND p.visible_after >= %(start)s AND p.visible_after < %(end)s)
                  )
                  AND p.timeline_at >= %(start)s::timestamptz - interval '24 hours'""",
            {"start": start, "end": end},
        ).fetchall()


def _role_of(kind: str, official: bool) -> str:
    if official:
        return "X·官方" if kind == "x_search" else "官方"
    if kind == "x_search":
        return "X·KOL"
    if kind == "mp_account":
        return "公眾號"
    return "媒體"


def report_entry(r: dict) -> dict:
    return {
        "itemId": r["id"], "factId": r["fact_public_id"], "storyPublicId": r["story_public_id"], "title": r["title"], "summary": r["summary"] or "",
        "sourceName": r["source_name"], "sourceUrl": r["url"], "sourceId": r["source_id"], "firstParty": r["first_party"],
        "role": _role_of(r["source_kind"], representative_priority(r) < 3), "score": _num(r["score"]), "publishedAt": iso(r["timeline_at"]),
    }  # fmt: skip


def as_related(e: dict) -> dict:
    """Another development of an entry's event, or a report of the launch it was merged with."""
    return {k: e[k] for k in ("itemId", "factId", "storyPublicId", "title", "sourceName", "sourceUrl", "sourceId")}


def fact_key_of(r: dict) -> str:
    return r["fact_public_id"] or f"a:{r['id']}"


def group_by(rows: list, key) -> dict:
    out: dict = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


def candidates(pool, start, end) -> list[dict]:
    """A weekly's or monthly's candidates: each fact once, by its representative, best scored first."""
    out = []
    for fact_key, members in group_by(period_reports(pool, start, end), fact_key_of).items():
        r = pick_representative(members)
        out.append({**report_entry(r), "category": r["category"], "factKey": fact_key})
    return sorted(out, key=lambda c: -(c["score"] or 0))


def period_entries(pool, start_date: str, end_date: str) -> dict:
    """A weekly's or monthly's candidates, compiled from the dailies dated startDate…endDate: each event once, by its most reported entry (then the first day's, the
    original news, then the most authoritative) in the reports' current public wording, ranked by how the dailies treated it (lead, highlight, the days it was
    carried), its coverage and its best score. Reports withdrawn since drop out."""
    with pool.connection() as conn:
        issues = conn.execute(
            "SELECT key, content FROM reports WHERE kind = 'daily' AND key >= %s AND key <= %s ORDER BY key", (start_date, end_date)
        ).fetchall()
    carried = []
    for issue in issues:
        c = issue["content"]
        for s in c.get("sections") or []:
            for it in s.get("items") or []:
                if it.get("itemId"):
                    try:
                        sources = float(it.get("sources") or 0) or 1
                    except (TypeError, ValueError):
                        sources = 1
                    carried.append({
                        "id": str(it["itemId"]), "key": issue["key"], "sources": int(sources),
                        "lead": c.get("leadItemId") == it["itemId"], "highlight": it["itemId"] in (c.get("highlights") or []),
                    })  # fmt: skip
    if not carried:
        return {"entries": [], "issues": len(issues), "hasEntries": False}
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT {REPORT_FIELDS}
                FROM publications p JOIN sources s ON s.id = p.source_id
                LEFT JOIN facts f ON f.id = p.fact_id AND {own_fact_evidence_condition()}
                LEFT JOIN stories st ON st.id = f.story_id
                WHERE p.article_id = ANY(%s::text[]) AND p.visibility = 'public' AND p.eligible""",
            (list(dict.fromkeys(c["id"] for c in carried)),),
        ).fetchall()
    by_id = {r["id"]: r for r in rows}
    events = group_by(
        [c for c in carried if c["id"] in by_id],
        lambda c: f"a:{by_id[c['id']]['id']}" if by_id[c["id"]]["story_id"] is None else f"s:{by_id[c['id']]['story_id']}",
    )
    ranked = []
    for cs in events.values():
        best = sorted(cs, key=lambda c: (-c["sources"], collate_key(c["key"]), representative_priority(by_id[c["id"]])))[0]
        main = by_id[best["id"]]
        rank = (
            max(by_id[c["id"]]["score"] or 0 for c in cs)
            + 5 * math.log2(1 + max(c["sources"] for c in cs))
            + (6 if any(c["lead"] for c in cs) else 0)
            + (3 if any(c["highlight"] for c in cs) else 0)
            + 4 * (len({c["key"] for c in cs}) - 1)
        )
        ranked.append({"candidate": {**report_entry(main), "category": main["category"], "factKey": fact_key_of(main)}, "rank": float(rank)})
    ranked.sort(key=lambda r: (-r["rank"], collate_key(r["candidate"]["itemId"])))
    return {"entries": [r["candidate"] for r in ranked], "issues": len(issues), "hasEntries": True}


def cited_in(content: dict) -> list[dict]:
    """Every report an issue cites: its entries, what is listed under them, its flashes."""
    entries = [*[it for s in content.get("sections") or [] for it in s.get("items") or []], *(content.get("flashes") or [])]
    return [*entries, *[r for e in entries for r in e.get("related") or []]]


def daily_memory(pool, date: str) -> dict:
    """What the issues of the week before `date` carried: their facts and reports, which are not repeated, and their events with the latest issue and headline that
    covered each. Facts and events are also read from the cited reports' current grouping, so a later merge or regrouping still counts."""
    with pool.connection() as conn:
        issues = conn.execute(
            "SELECT key, content FROM reports WHERE kind = 'daily' AND key < %s AND key >= %s ORDER BY key DESC", (date, add_days(date, -MEMORY_DAYS))
        ).fetchall()
    keys: set[str] = set()
    cited: dict[str, dict] = {}
    for issue in issues:
        for it in cited_in(issue["content"]):
            if it.get("factId"):
                keys.add(str(it["factId"]))
            if not it.get("itemId"):
                continue
            keys.add(f"a:{it['itemId']}")
            if it["itemId"] not in cited:
                cited[it["itemId"]] = {"key": issue["key"], "title": str(it.get("title") or "")}
    stories: dict[int, dict] = {}
    if not cited:
        return {"keys": keys, "stories": stories}
    with pool.connection() as conn:
        current = conn.execute(
            f"""SELECT p.article_id AS id, f.public_id AS fact_public_id, f.story_id FROM publications p
                LEFT JOIN facts f ON f.id = p.fact_id AND {own_fact_evidence_condition()}
                WHERE p.article_id = ANY(%s::text[])""",
            (list(cited),),
        ).fetchall()
    for c in current:
        if c["fact_public_id"]:
            keys.add(c["fact_public_id"])
        if c["story_id"] is None:
            continue
        at = cited[c["id"]]
        seen = stories.get(c["story_id"])
        if not seen or seen["key"] < at["key"]:
            stories[c["story_id"]] = at
    return {"keys": keys, "stories": stories}


def _missed_facts(pool, start, end) -> list[dict]:
    """Facts the selection missed although the event is clearly news: no selected report, three or more independent participants by the cutoff (as the heat counts
    them), an official post among its reports, active in the window and begun at most a day before it. Each comes with its listed editorial reports, from which the
    issue takes the most authoritative one."""
    params = {**scope_params(end), **signal_params(), "start": start, "end": end}
    with pool.connection() as conn:
        found = conn.execute(
            f"""WITH active AS (
                  SELECT DISTINCT fa.fact_id FROM fact_articles fa
                  JOIN publications p ON p.article_id = fa.article_id JOIN sources s ON s.id = p.source_id
                  WHERE {evidence_condition()} AND {listed_condition()} AND s.participation_mode = 'editorial' AND NOT p.backfill
                    AND p.timeline_at >= %(start)s AND p.timeline_at < %(end)s
                )
                SELECT fa.fact_id
                FROM active JOIN fact_articles fa ON fa.fact_id = active.fact_id
                JOIN publications p ON p.article_id = fa.article_id
                JOIN {current_signals()} cs ON cs.article_id = fa.article_id
                WHERE {evidence_condition()} AND cs.observed_at < %(end)s
                  AND NOT EXISTS (SELECT 1 FROM fact_articles sfa JOIN publications sp ON sp.article_id = sfa.article_id
                                  WHERE sfa.fact_id = fa.fact_id AND sfa.role <> 'mention' AND sp.selected AND sp.visibility = 'public')
                GROUP BY fa.fact_id
                HAVING count(DISTINCT cs.participant_key) >= 3 AND min(cs.observed_at) >= %(start)s::timestamptz - interval '24 hours'""",
            params,
        ).fetchall()
        if not found:
            return []
        rows = conn.execute(
            f"""SELECT {REPORT_FIELDS}
                FROM fact_articles fa JOIN publications p ON p.article_id = fa.article_id JOIN sources s ON s.id = p.source_id
                JOIN facts f ON f.id = fa.fact_id LEFT JOIN stories st ON st.id = f.story_id
                WHERE fa.fact_id = ANY(%(facts)s::bigint[]) AND {evidence_condition()} AND {listed_condition()}
                  AND s.participation_mode = 'editorial' AND s.tier <> 'EXCLUDE_MP' AND NOT p.backfill AND p.timeline_at < %(end)s""",
            {**params, "facts": [f["fact_id"] for f in found]},
        ).fetchall()
    return [
        {"key": key, "rows": members} for key, members in group_by(rows, fact_key_of).items() if any(representative_priority(r) < 3 for r in members)
    ]


def _judged_reports(pool, start, end) -> int:
    """The window's public reports the editors scored: none means judging did not run there, not a quiet day."""
    with pool.connection() as conn:
        row = conn.execute(
            f"""SELECT count(*)::int AS n FROM publications p
                WHERE {listed_condition()} AND p.score IS NOT NULL AND NOT p.backfill AND p.timeline_at >= %(start)s AND p.timeline_at < %(end)s""",
            {**scope_params(end), "start": start, "end": end},
        ).fetchone()
    return row["n"]


def fact_sources(pool, fact_ids: list[int], end) -> dict[int, list[str]]:
    """The editorial sources that reported each fact by the cutoff (publication/coverage.ts)."""
    if not fact_ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT fa.fact_id, array_agg(DISTINCT p.source_id) AS sources
                FROM fact_articles fa JOIN publications p ON p.article_id = fa.article_id JOIN sources s ON s.id = p.source_id
                WHERE fa.fact_id = ANY(%(facts)s::bigint[]) AND {evidence_condition()} AND {listed_condition()}
                  AND s.participation_mode = 'editorial' AND p.timeline_at < %(end)s
                GROUP BY fa.fact_id""",
            {**scope_params(end), "facts": fact_ids, "end": end},
        ).fetchall()
    return {r["fact_id"]: r["sources"] for r in rows}


def _importance(score, participants: int, official: bool, follow_up: bool) -> float:
    """How much an event matters to a reader of the day: the best score among its selected reports, its heat as the independent participants who discussed it within
    the issue's window (1 → 5, 3 → 10, 7 → 15, 15 → 20), a first-hand post, and less for an event an earlier issue already covered."""
    return (50 if score is None else score) + 5 * math.log2(1 + participants) + (5 if official else 0) - (6 if follow_up else 0)


def daily_edition(pool, date: str, start, end) -> dict:
    """The daily's events for the window [start, end), most important first, before the editors' pass: selected reports and missed facts, minus what the week's issues
    already carried, one entry per event."""
    memory, selected, missed, judged = (
        daily_memory(pool, date),
        period_reports(pool, start, end),
        _missed_facts(pool, start, end),
        _judged_reports(pool, start, end),
    )
    raw = [
        *[{"key": k, "rows": rows, "selected": True} for k, rows in group_by(selected, fact_key_of).items()],
        *[{**m, "selected": False} for m in missed],
    ]
    kept = [f for f in raw if f["key"] not in memory["keys"] and not any(f"a:{r['id']}" in memory["keys"] for r in f["rows"])]
    fact_ids = list(dict.fromkeys(f["rows"][0]["fact_id"] for f in kept if f["rows"][0]["fact_id"] is not None))
    sources_of = fact_sources(pool, fact_ids, end)
    facts = []
    for f in kept:
        fid = f["rows"][0]["fact_id"]
        facts.append({
            **f, "sources": list(dict.fromkeys([*([] if fid is None else sources_of.get(fid, [])), *[r["source_id"] for r in f["rows"]]])),
            "at": min(r["timeline_at"].timestamp() * 1000 for r in f["rows"]),
        })  # fmt: skip

    # One entry per event: its most reported fact leads (then the earliest, as the hot list chooses), and the event's other facts are listed under it.
    events = list(group_by(facts, lambda f: f"f:{f['key']}" if f["rows"][0]["story_id"] is None else f"s:{f['rows'][0]['story_id']}").values())
    story_ids = list(dict.fromkeys(fs[0]["rows"][0]["story_id"] for fs in events if fs[0]["rows"][0]["story_id"] is not None))
    loose = [r["id"] for fs in events if fs[0]["rows"][0]["story_id"] is None for f in fs for r in f["rows"]]
    talk, named = [], []
    with pool.connection() as conn:
        if story_ids:
            talk = conn.execute(
                f"""SELECT cs.story_id, count(DISTINCT cs.participant_key)::int AS participants FROM {current_signals()} cs
                    WHERE cs.story_id = ANY(%(stories)s::bigint[]) AND cs.observed_at >= %(start)s AND cs.observed_at < %(end)s
                    GROUP BY cs.story_id""",
                {**signal_params(), "stories": story_ids, "start": start, "end": end},
            ).fetchall()
        # A report without an event of its own is a roundup when the event graph linked it to the facts it mentions.
        if loose:
            named = conn.execute(
                """SELECT DISTINCT fa.article_id, f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id
                   WHERE fa.article_id = ANY(%s::text[]) AND f.story_id IS NOT NULL""",
                (loose,),
            ).fetchall()
    participants = {t["story_id"]: t["participants"] for t in talk}

    entries = []
    for fs in events:
        ordered = sorted(fs, key=lambda f: (-len(f["sources"]), f["at"], collate_key(f["key"])))
        reps = [pick_representative(f["rows"]) for f in ordered]
        rep = reps[0]
        source_ids = {s for f in fs for s in f["sources"]}
        story_id = rep["story_id"]
        previous = None if story_id is None else memory["stories"].get(story_id)
        score = max(rep["score"] or 0, *[r["score"] or 0 for f in fs if f["selected"] for r in f["rows"]])
        entry = {
            **report_entry(rep), "sources": len(source_ids),
            **({"related": [as_related(report_entry(r)) for r in reps[1:]]} if len(reps) > 1 else {}),
            **({"followUp": previous["key"]} if previous else {}),
            **({"fillIn": True} if all(not f["selected"] for f in fs) else {}),
        }  # fmt: skip
        ids = {r["id"] for f in fs for r in f["rows"]}
        entries.append({
            "entry": entry, "category": rep["category"], "tags": rep["tags"], "storyId": story_id, "sourceIds": source_ids, "authority": representative_priority(rep),
            "previous": previous, "mentions": set() if story_id is not None else {n["story_id"] for n in named if n["article_id"] in ids},
            "importance": _importance(
                _num(float(score)) if score is not None else None, len(source_ids) if story_id is None else participants.get(story_id, len(source_ids)),
                any(representative_priority(r) < 3 for f in fs for r in f["rows"]), previous is not None,
            ),
        })  # fmt: skip
    entries.sort(key=lambda e: (-e["importance"], collate_key(e["entry"]["itemId"])))
    return {
        "entries": entries,
        "stats": {"judgedReports": judged, "selectedReports": len(selected), "facts": len(raw), "repeatsSuppressed": len(raw) - len(kept), "fillIns": sum(1 for f in kept if not f["selected"]), "events": len(entries)},
    }  # fmt: skip


def arrange_daily(entries: list[dict]) -> dict:
    """The issue from the ranked entries, by rule: a roundup that mentions events of this issue is listed under the most important of them; a follow-up of a covered
    event takes a full entry only when the party itself acted (not as commentary) or four or more sources carry its new facts, else it is a flash; then the most
    important entries in full, at most two per source, and the next ones as flashes. The first entry leads the issue and the next three are its highlights."""
    by_story = {e["storyId"]: e for e in entries if e["storyId"] is not None}
    under: dict[int, list[dict]] = {}
    host_of: dict[int, dict] = {}
    for e in entries:
        named = [by_story[i] for i in e["mentions"] if i in by_story]
        if not named:
            continue
        host = named[0]
        for h in named[1:]:
            if h["importance"] > host["importance"]:
                host = h
        under.setdefault(id(host), []).append(e)
        host_of[id(host)] = host
    folded = {id(e) for roundups in under.values() for e in roundups}
    live = []
    for e in entries:
        if id(e) in folded:
            continue
        roundups = under.get(id(e))
        if not roundups:
            live.append(e)
            continue
        source_ids = {*e["sourceIds"], *[s for r in roundups for s in r["sourceIds"]]}
        related = [*(e["entry"].get("related") or []), *[x for r in roundups for x in [as_related(r["entry"]), *(r["entry"].get("related") or [])]]]
        live.append({**e, "sourceIds": source_ids, "entry": {**e["entry"], "sources": len(source_ids), "related": related}})
    commentary = _commentary()

    def earned(e: dict) -> bool:
        return not e["previous"] or (e["authority"] < 3 and (e["category"] or "") not in commentary) or e["entry"]["sources"] >= FOLLOW_UP_SOURCES

    full = earned if any(earned(e) for e in live) else (lambda _e: True)  # a day of nothing but follow-ups still has entries in full
    main, rest = [], []
    per_source: dict[str, int] = {}
    for e in live:
        n = per_source.get(e["entry"]["sourceId"], 0)
        if len(main) < MAIN_ENTRIES and n < PER_SOURCE and full(e):
            main.append(e)
            per_source[e["entry"]["sourceId"]] = n + 1
        else:
            rest.append(e)
    flashes = rest[:FLASH_ENTRIES]
    return {
        "main": main,
        "flashes": flashes,
        "stats": {"roundupsFolded": len(folded), "followUpsAsFlashes": sum(1 for e in live if not full(e)), "left": len(rest) - len(flashes)},
    }
