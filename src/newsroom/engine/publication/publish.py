"""Publishing: derive the public projection of one article from its material, the latest judgement, manual overrides and grouping, then record
selected-set changes in the sync ledger. Rebuilding only re-reads stored results; it never calls a model. Port of publication/publish.ts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

from psycopg.types.json import Jsonb

from newsroom.engine import pack
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.ids import stable_json
from newsroom.engine.lib.text import collapse_whitespace, is_chinese_text
from newsroom.engine.publication.items import display_tags, is_empty_or_link_only
from newsroom.engine.publication.scope import foldable_selection_condition, own_fact_evidence_condition, scope_params
from newsroom.engine.publication.stories import REPRESENTATIVE_COLUMNS, latest_composite, pick_representative, public_source_name
from newsroom.engine.publication.v1 import iso, item_url
from newsroom.engine.taxonomy import to_public_api_category


def independent_selected_sources() -> list[str]:
    """Presentation exceptions never alter scoring, selection eligibility or fact membership. This site has none."""
    return []


def is_independent_selected_source(source_id: str) -> bool:
    return source_id in independent_selected_sources()


def channel_of(kind: str, has_x_post: bool) -> str:
    return "x" if kind == "x_search" or has_x_post else "news"


def is_pool_eligible(participation_mode: str, relevance: str | None, title: str | None, summary: str | None, original_post: bool = False) -> bool:
    """Public pool (/all): editorial sources, AI relevant, with usable copy or an original post."""
    return participation_mode == "editorial" and relevance == "pass" and bool(title) and (bool(summary) or original_post)


def has_item_page(visibility: str, source_mode: str) -> bool:
    return visibility != "withdrawn" and source_mode == "editorial"


def is_selectable(eligible: bool, judged_selected: bool | None, tier: str) -> bool:
    """Selected: pool eligible, judged selected, and the source tier may enter the selection."""
    return eligible and judged_selected is True and tier != "EXCLUDE_MP"


def body_mode_of(source: dict, body_status: str, has_body: bool) -> str:
    """Site full text: the source licence allows showing it and we have confirmed body text."""
    return "full" if source["site_fulltext"] and body_status == "ok" and has_body else "summary"


def may_redistribute(source: dict, body_mode: str) -> bool:
    """Full-RSS redistribution whitelist: only sources that explicitly allow it."""
    return bool(source["syndicate_fulltext"]) and body_mode == "full"


def is_indexable(visibility: str, source_mode: str, has_summary: bool, selected: bool, seo_indexed_at, seo_excluded_at) -> bool:
    return (
        visibility == "public"
        and has_item_page(visibility, source_mode)
        and has_summary
        and seo_excluded_at is None
        and (selected or seo_indexed_at is not None)
    )


def original_post_copy(post: dict | None, url: str, article: dict | None = None) -> dict | None:
    """A fetched long article supplies real material; otherwise there is nothing here to translate."""
    if not post or (article and (article.get("text") or "").strip()):
        return None
    text = str(post.get("text") if post.get("text") is not None else "")
    if not is_empty_or_link_only(text):
        return None
    return {"title": collapse_whitespace(text) or url, "summary": text if text.strip() else None}


def _pick_string(override, fallback: str | None) -> str | None:
    return override.strip() if isinstance(override, str) and override.strip() != "" else fallback


def js_round(n: float) -> int:
    return math.floor(n + 0.5)


def _round1(n) -> float | None:
    return None if n is None else js_round(float(n) * 10) / 10


def v1_payload(
    article_id: str, title, original_title, summary, source_name, url, published_at, discovered_at, category, score, selected, reason
) -> dict:
    aihot = item_url(article_id)
    return {
        "id": article_id, "title": title, "originalTitle": original_title, "summary": summary, "source": {"name": source_name},
        "links": {"aihot": aihot, "original": url}, "publishedAt": iso(published_at) if published_at else None, "discoveredAt": iso(discovered_at),
        "category": to_public_api_category(category), "score": None if score is None else js_round(score), "selected": selected,
        "reason": reason if selected else None, "attribution": {"name": pack.site()["SITE"]["name"], "url": aihot},
    }  # fmt: skip


def _norm(value):
    """What JSON.stringify would see: instants as ISO strings, numbers without a trailing .0, tag-like lists as they are."""
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, (Decimal, float)):
        f = float(value)
        return int(f) if f.is_integer() else f
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _append_ledger(conn, article_id: str, op: str, payload: dict | None, now: datetime) -> int:
    """Allocates the next ledger sequence under a transaction lock so sequence order equals commit order."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('selected_ledger'))")
    nxt = conn.execute("SELECT coalesce(max(seq), 0) + 1 AS next FROM selected_ledger").fetchone()["next"]
    conn.execute(
        "INSERT INTO selected_ledger (seq, article_id, op, changed_at, visible_at, payload) VALUES (%s, %s, %s, %s, %s, %s)",
        (nxt, article_id, op, now, now, Jsonb(payload) if payload else None),
    )
    return nxt


def _sha256(value: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(value.encode()).hexdigest()


def _sync_ledger(conn, article_id: str, now: datetime) -> str | None:
    """Brings one article's place in the selected sync ledger in line with its publication."""
    p = conn.execute(
        """SELECT p.selected, p.visibility, p.seat, p.title, p.original_title, p.summary, p.url, p.published_at, p.discovered_at, p.category,
                  p.score, p.reason, s.name AS source_name
           FROM publications p JOIN sources s ON s.id = p.source_id WHERE p.article_id = %s""",
        (article_id,),
    ).fetchone()
    if not p:
        return None
    state = conn.execute("SELECT in_set, payload_hash FROM selected_state WHERE article_id = %s", (article_id,)).fetchone()
    if p["selected"] and p["visibility"] == "public" and p["seat"]:
        payload = v1_payload(article_id, p["title"], p["original_title"], p["summary"], p["source_name"], p["url"], p["published_at"], p["discovered_at"],
                             p["category"], None if p["score"] is None else float(p["score"]), True, p["reason"])  # fmt: skip
        payload_hash = _sha256(stable_json(payload))
        if state and state["in_set"] and state["payload_hash"] == payload_hash:
            return None
        seq = _append_ledger(conn, article_id, "upsert", payload, now)
        conn.execute(
            """INSERT INTO selected_state (article_id, in_set, payload_hash, last_seq) VALUES (%s, true, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET in_set = true, payload_hash = EXCLUDED.payload_hash, last_seq = EXCLUDED.last_seq""",
            (article_id, payload_hash, seq),
        )
        return "upsert"
    if not state or not state["in_set"]:
        return None
    seq = _append_ledger(conn, article_id, "remove", None, now)
    conn.execute("UPDATE selected_state SET in_set = false, payload_hash = NULL, last_seq = %s WHERE article_id = %s", (seq, article_id))
    return "remove"


def _settle_seats(conn, fact_id: int | None, self_id: str | None, now: datetime) -> str | None:
    """Seats are per fact, never per story: a launch, a later evaluation and a fix are separate news. Ordinary reports share one selected seat
    per fact: among the fact's selected public reports, the representative takes it; a change of representative removes the old one and adds
    the new one. An article outside any fact keeps its own seat, as does each selected article from an independent source."""
    if fact_id is None:
        if self_id:
            conn.execute("UPDATE publications SET seat = true WHERE article_id = %s AND NOT seat", (self_id,))
        return None
    members = conn.execute(
        f"""SELECT p.article_id, p.source_id, p.body_mode, p.score, p.timeline_at, p.seat, {REPRESENTATIVE_COLUMNS},
              (p.selected AND p.visibility = 'public' AND {own_fact_evidence_condition()}) AS holds
            FROM publications p JOIN sources s ON s.id = p.source_id JOIN facts f ON f.id = p.fact_id WHERE p.fact_id = %(fact)s""",
        {**scope_params(now), "fact": fact_id},
    ).fetchall()
    candidates = [
        {**m, "score": None if m["score"] is None else float(m["score"])}
        for m in members
        if m["holds"] and not is_independent_selected_source(m["source_id"])
    ]
    rep = pick_representative(candidates) if candidates else None
    own = None
    for m in members:
        seat = (not m["holds"]) or is_independent_selected_source(m["source_id"]) or m["article_id"] == (rep or {}).get("article_id")
        if seat == m["seat"]:
            continue
        conn.execute("UPDATE publications SET seat = %s WHERE article_id = %s", (seat, m["article_id"]))
        change = _sync_ledger(conn, m["article_id"], now)
        if m["article_id"] == self_id:
            own = change
    return own


COLUMNS_TO_COMPARE = ["source_id", "first_party", "visibility", "eligible", "selected", "selection_candidate", "title", "original_title", "summary", "reason",
                      "category", "tags", "score", "body_mode", "story_id", "fact_id", "indexable", "url", "channel", "syndicate", "published_at",
                      "discovered_at", "timeline_at", "backfill", "sort_at", "visible_after"]  # fmt: skip
ADMISSION = {"selected", "reason", "indexable", "story_id", "fact_id", "sort_at", "visible_after"}
FIRST_ANALYSIS = {*ADMISSION, "eligible", "selection_candidate", "title", "original_title", "summary", "category", "tags", "score"}


def publish_article(pool, article_id: str, now: datetime | None = None, released_at: datetime | None = None) -> dict | None:
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT 1 FROM articles WHERE id = %s FOR UPDATE", (article_id,))
        return publish_article_tx(conn, article_id, now, released_at)


def publish_article_tx(conn, article_id: str, now: datetime | None = None, released_at: datetime | None = None) -> dict | None:
    """Rebuilds the publication row of one article in the caller's transaction. Returns what changed, or None for an unknown article or source."""
    article = conn.execute(
        """SELECT id, source_id, url, title, language, published_at, discovered_at, timeline_at, backfill, body_status, body_text, x_post, x_article,
                  grouped_at, grouping_status, selection_adds_value FROM articles WHERE id = %s FOR UPDATE""",
        (article_id,),
    ).fetchone()
    if not article:
        return None
    # Explicit imports carry an already-public editorial decision, not a new pending judgement.
    if released_at and article["grouping_status"] == "pending":
        conn.execute(
            "UPDATE articles SET grouping_status = 'complete', grouped_at = coalesce(grouped_at, %s) WHERE id = %s", (released_at, article_id)
        )
        article["grouping_status"] = "complete"
    source = conn.execute(
        "SELECT id, name, kind, tier, participation_mode, first_party, site_fulltext, syndicate_fulltext FROM sources WHERE id = %s",
        (article["source_id"],),
    ).fetchone()
    if not source:
        return None
    analysis = conn.execute(
        """SELECT id, relevance, category, tags, subjects, title_zh, summary_zh, reason_zh, score, selected FROM analyses
           WHERE article_id = %s ORDER BY input_revision DESC, id DESC LIMIT 1""",
        (article_id,),
    ).fetchone()
    override = conn.execute("SELECT fields, visibility FROM editorial_overrides WHERE article_id = %s", (article_id,)).fetchone()
    membership = conn.execute(
        f"""SELECT fa.fact_id, f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id LEFT JOIN stories s ON s.id = f.story_id
            WHERE fa.article_id = %(id)s AND NOT {latest_composite("%(id)s")} AND fa.role IN ('primary', 'report') AND (s.id IS NULL OR s.merged_into IS NULL)
            ORDER BY (fa.role = 'primary') DESC, fa.created_at LIMIT 1""",
        {"id": article_id},
    ).fetchone()
    previous = conn.execute("SELECT * FROM publications WHERE article_id = %s", (article_id,)).fetchone()
    # The seats of this article's facts (before and after) are settled in this transaction: take the facts' locks first, in a fixed order.
    seat_facts = sorted({x for x in [previous["fact_id"] if previous else None, membership["fact_id"] if membership else None] if isinstance(x, int)})
    for fact in seat_facts:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"seat:{fact}",))
    # Reports wait for in-flight publication transactions before taking their candidate snapshot.
    conn.execute("SELECT pg_advisory_xact_lock_shared(hashtext('report_candidates'))")
    now = now or datetime.now(UTC)

    f = (override or {}).get("fields") or {}
    is_chinese_title = is_chinese_text(article["title"], article["language"])
    # An X post carries its Chinese in the summary and translation; without a Chinese title its own text is the title.
    zh_title = analysis["title_zh"] if analysis and (analysis["title_zh"] or "").strip() else None
    original = original_post_copy(article["x_post"], article["url"], article["x_article"])
    own_title = collapse_whitespace(article["title"]) if (is_chinese_title or article["x_post"]) else None
    title = _pick_string(f.get("title"), original["title"] if original else (zh_title if zh_title is not None else own_title))
    summary = _pick_string(f.get("summary"), original["summary"] if original else (analysis["summary_zh"] if analysis else None))
    category = _pick_string(f.get("category"), analysis["category"] if analysis else None)
    if isinstance(f.get("tags"), list):
        tags = f["tags"]
    else:
        tags = list(dict.fromkeys([*(analysis["tags"] if analysis else []), *[f"entity:{s}" for s in (analysis["subjects"] if analysis else [])]]))
    if "score" in f and (f["score"] is None or (isinstance(f["score"], (int, float)) and not isinstance(f["score"], bool))):
        score = f["score"]
    else:
        score = analysis["score"] if analysis else None
    relevance = f["relevance"] if isinstance(f.get("relevance"), str) else (analysis["relevance"] if analysis else None)
    judged_selected = f["selected"] if isinstance(f.get("selected"), bool) else (analysis["selected"] if analysis else None)
    # Material from an isolated source reaches no public surface at all: not even a detail page.
    visibility = "withdrawn" if source["participation_mode"] == "isolated" else ((override or {}).get("visibility") or "public")

    # An undated archive has a readable detail page, but its discovery is not a news timestamp.
    eligible = is_pool_eligible(source["participation_mode"], relevance, title, summary, bool(original)) and (
        not article["backfill"] or article["published_at"] is not None or bool(released_at)
    )
    selection_candidate = is_selectable(eligible, judged_selected, source["tier"])
    # Scoring nominates a report; a completed identity/value decision admits it to selection. A historical import already has its public decision.
    selected = (
        selection_candidate
        and article["grouping_status"] == "complete"
        and (f.get("selected") is True or article["selection_adds_value"] is not False)
    )
    reason = _pick_string(f.get("reason"), None if original else (analysis["reason_zh"] if analysis else None)) if selected else None
    x_post = article["x_post"]
    channel = channel_of(source["kind"], bool(x_post))
    has_body = bool(article["body_text"]) or bool(x_post and (x_post.get("text") or x_post.get("media") or (x_post.get("quoted") or {}).get("text")))
    body_mode = body_mode_of(source, article["body_status"], has_body)
    syndicate = may_redistribute(source, body_mode)
    original_title = None if is_chinese_title and title == collapse_whitespace(article["title"]) else collapse_whitespace(article["title"])

    # Waiting candidates are readable in the pool, with no selected seat, sync entry or push.
    selected_ready_at = (
        previous["selected_ready_at"]
        if previous and previous["selected_ready_at"] is not None
        else ((released_at or now) if selection_candidate else None)
    )
    if selected:
        visible_after = previous["visible_after"] if previous and previous["selected"] and previous["visible_after"] else (released_at or now)
    else:
        visible_after = None

    indexable = is_indexable(
        visibility,
        source["participation_mode"],
        bool(summary),
        selected,
        previous["seo_indexed_at"] if previous else None,
        previous["seo_excluded_at"] if previous else None,
    )
    search_text = collapse_whitespace(
        " ".join(
            x
            for x in [
                title,
                original_title,
                summary,
                public_source_name(source["name"]),
                *display_tags(tags),
                *(analysis["subjects"] if analysis else []),
            ]
            if x
        )
    ).lower()

    # Ordinary selected reports share their reading group's first arrival; independent articles keep their own arrival.
    sort_at = article["timeline_at"]
    if selected and membership and membership["fact_id"] and not is_independent_selected_source(source["id"]):
        anchor = conn.execute(
            f"""SELECT min(p.timeline_at) AS t FROM publications p
                WHERE p.fact_id = %(fact)s AND p.eligible AND p.visibility = 'public' AND p.article_id <> %(id)s AND {foldable_selection_condition()}""",
            {"fact": membership["fact_id"], "id": article_id, **scope_params(now)},
        ).fetchone()
        if anchor and anchor["t"] and anchor["t"] < sort_at:
            sort_at = anchor["t"]

    next_row = {
        "source_id": source["id"], "first_party": source["tier"] == "T1", "visibility": visibility, "eligible": eligible, "selected": selected,
        "selection_candidate": selection_candidate, "title": title if title is not None else collapse_whitespace(article["title"]), "original_title": original_title,
        "summary": summary, "reason": reason, "category": category, "tags": tags, "score": _round1(score), "body_mode": body_mode,
        "story_id": membership["story_id"] if membership else None, "fact_id": membership["fact_id"] if membership else None, "indexable": indexable,
        "url": article["url"], "channel": channel, "syndicate": syndicate, "published_at": article["published_at"], "discovered_at": article["discovered_at"],
        "timeline_at": article["timeline_at"], "backfill": article["backfill"], "sort_at": sort_at, "visible_after": visible_after,
    }  # fmt: skip

    def snapshot(row: dict) -> dict:
        return {k: (sorted(row[k]) if k == "tags" else _norm(row[k])) for k in COLUMNS_TO_COMPARE}

    after = snapshot(next_row)
    before = snapshot(previous) if previous else None
    changed_fields = [k for k in COLUMNS_TO_COMPARE if before is None or stable_json(after[k]) != stable_json(before[k])]
    changed = bool(changed_fields)
    same_story = previous is not None and (previous["story_id"] is None or previous["story_id"] == next_row["story_id"])
    same_fact = previous is not None and (previous["fact_id"] is None or previous["fact_id"] == next_row["fact_id"])
    admission = bool(previous and not previous["selected"] and selected and same_story and same_fact and all(k in ADMISSION for k in changed_fields))
    first_analysis = bool(
        previous and not previous["eligible"] and not previous["summary"] and previous["visibility"] == "public" and visibility == "public" and eligible
        and same_story and same_fact and all(k in FIRST_ANALYSIS for k in changed_fields)
    )  # fmt: skip
    # A first event link is new metadata, like the first judgement or selection: a detail change.
    first_identity = bool(
        previous and not previous["selected"] and previous["fact_id"] is None and previous["story_id"] is None and previous["visibility"] == "public"
        and visibility == "public" and eligible and next_row["story_id"] is not None and all(k in ADMISSION for k in changed_fields)
    )  # fmt: skip
    in_state = conn.execute("SELECT 1 FROM selected_state WHERE article_id = %s", (article_id,)).fetchone() is not None
    initial = (admission or first_analysis or first_identity) and not in_state
    change_kind = "detail" if initial else "content"
    revision = (previous["revision"] + (1 if changed else 0)) if previous else 1

    conn.execute(
        """INSERT INTO publications (article_id, analysis_id, revision, visibility, eligible, selected, selection_candidate, title, original_title, summary,
             reason, category, tags, score, source_id, channel, first_party, url, published_at, discovered_at, timeline_at, backfill,
             selected_ready_at, visible_after, body_mode, syndicate, indexable, story_id, fact_id, search_text, sort_at, updated_at)
           VALUES (%(article_id)s, %(analysis_id)s, %(revision)s, %(visibility)s, %(eligible)s, %(selected)s, %(selection_candidate)s, %(title)s,
             %(original_title)s, %(summary)s, %(reason)s, %(category)s, %(tags)s, %(score)s, %(source_id)s, %(channel)s, %(first_party)s,
             %(url)s, %(published_at)s, %(discovered_at)s, %(timeline_at)s, %(backfill)s,
             %(selected_ready_at)s, %(visible_after)s, %(body_mode)s, %(syndicate)s, %(indexable)s, %(story_id)s, %(fact_id)s, %(search_text)s, %(sort_at)s, now())
           ON CONFLICT (article_id) DO UPDATE SET
             analysis_id = EXCLUDED.analysis_id, revision = EXCLUDED.revision, visibility = EXCLUDED.visibility,
             eligible = EXCLUDED.eligible, selected = EXCLUDED.selected, selection_candidate = EXCLUDED.selection_candidate, title = EXCLUDED.title, original_title = EXCLUDED.original_title,
             summary = EXCLUDED.summary, reason = EXCLUDED.reason, category = EXCLUDED.category, tags = EXCLUDED.tags,
             score = EXCLUDED.score, source_id = EXCLUDED.source_id, channel = EXCLUDED.channel, first_party = EXCLUDED.first_party,
             url = EXCLUDED.url, published_at = EXCLUDED.published_at, discovered_at = EXCLUDED.discovered_at,
             timeline_at = EXCLUDED.timeline_at, backfill = EXCLUDED.backfill, selected_ready_at = EXCLUDED.selected_ready_at,
             visible_after = EXCLUDED.visible_after, body_mode = EXCLUDED.body_mode, syndicate = EXCLUDED.syndicate,
             indexable = EXCLUDED.indexable, story_id = EXCLUDED.story_id, fact_id = EXCLUDED.fact_id,
             search_text = EXCLUDED.search_text, sort_at = EXCLUDED.sort_at, updated_at = now()
           WHERE (publications.analysis_id, publications.revision, publications.visibility, publications.eligible,
               publications.selected, publications.selection_candidate, publications.title, publications.original_title, publications.summary,
               publications.reason, publications.category, publications.tags, publications.score,
               publications.source_id, publications.channel, publications.first_party, publications.url,
               publications.published_at, publications.discovered_at, publications.timeline_at, publications.backfill,
               publications.selected_ready_at, publications.visible_after, publications.body_mode, publications.syndicate,
               publications.indexable, publications.story_id, publications.fact_id, publications.search_text,
               publications.sort_at)
             IS DISTINCT FROM (EXCLUDED.analysis_id, EXCLUDED.revision, EXCLUDED.visibility, EXCLUDED.eligible,
               EXCLUDED.selected, EXCLUDED.selection_candidate, EXCLUDED.title, EXCLUDED.original_title, EXCLUDED.summary,
               EXCLUDED.reason, EXCLUDED.category, EXCLUDED.tags, EXCLUDED.score,
               EXCLUDED.source_id, EXCLUDED.channel, EXCLUDED.first_party, EXCLUDED.url,
               EXCLUDED.published_at, EXCLUDED.discovered_at, EXCLUDED.timeline_at, EXCLUDED.backfill,
               EXCLUDED.selected_ready_at, EXCLUDED.visible_after, EXCLUDED.body_mode, EXCLUDED.syndicate,
               EXCLUDED.indexable, EXCLUDED.story_id, EXCLUDED.fact_id, EXCLUDED.search_text,
               EXCLUDED.sort_at)""",
        {**next_row, "article_id": article_id, "analysis_id": analysis["id"] if analysis else None, "revision": revision,
         "selected_ready_at": selected_ready_at, "search_text": search_text},
    )  # fmt: skip

    # The pool search row follows eligibility; its body part only covers full text the site may show.
    if eligible:
        body = (article["body_text"] or "")[:12000].lower() if body_mode == "full" else ""
        conn.execute(
            """INSERT INTO pool_search (article_id, direct, body) VALUES (%s, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET direct = EXCLUDED.direct, body = EXCLUDED.body
               WHERE pool_search.direct IS DISTINCT FROM EXCLUDED.direct OR pool_search.body IS DISTINCT FROM EXCLUDED.body""",
            (article_id, search_text, body),
        )
    else:
        conn.execute("DELETE FROM pool_search WHERE article_id = %s", (article_id,))

    # Content-group push: once, for an item that arrives live and becomes selected (never for imports, backfill or stale-on-discovery material).
    if selected and not (previous and previous["selected"]) and not released_at and not article["backfill"] and visibility == "public":
        enqueue(conn, QUEUES["notifySelected"], {"articleId": article_id}, singleton_key=f"selected:{article_id}", start_after=5)
        # Prepare its images alongside the newly confirmed selection.
        enqueue(conn, QUEUES["prepareMedia"], {"articleId": article_id}, singleton_key=f"media:{article_id}")

    # Seats of the fact this article reports (and of the one it left), then its place in the sync ledger.
    ledger = _settle_seats(conn, membership["fact_id"] if membership else None, article_id, now)
    if previous and previous["fact_id"] and previous["fact_id"] != (membership["fact_id"] if membership else None):
        _settle_seats(conn, previous["fact_id"], None, now)
    ledger = _sync_ledger(conn, article_id, now) or ledger

    was_public = previous is not None and previous["visibility"] != "withdrawn" and previous["eligible"]
    reduced = bool(
        was_public
        and (
            visibility == "withdrawn" or not eligible or (previous["visibility"] == "public" and visibility != "public")
            or (previous["selected"] and not selected) or (previous["body_mode"] == "full" and body_mode != "full")
        )
    )  # fmt: skip
    return {
        "articleId": article_id,
        "changed": changed,
        "changeKind": change_kind,
        "selected": selected,
        "visibility": visibility,
        "ledger": ledger,
        "reduced": reduced,
    }


def republish_source(pool, source_id: str, on_progress=None, shutdown=None) -> dict:
    """Re-derives every published article of one source (after its participation, licences, tier or name changed) without calling models. Runs in the worker;
    progress goes to the callback."""
    with pool.connection() as conn:
        total = conn.execute("SELECT count(*)::int AS total FROM publications WHERE source_id = %s", (source_id,)).fetchone()["total"]
    after, done, changed, reduced = "", 0, 0, 0
    while True:
        with pool.connection() as conn:
            batch = conn.execute(
                "SELECT article_id FROM publications WHERE source_id = %s AND article_id > %s ORDER BY article_id LIMIT 500", (source_id, after)
            ).fetchall()
        if not batch:
            break
        for row in batch:
            # Stopping mid-way is safe: the job is retried after the restart and re-derives from the start.
            if shutdown is not None and shutdown.is_set():
                raise InterruptedError("worker is stopping; republish resumes after restart")
            r = publish_article(pool, row["article_id"])
            changed += 1 if r and r.get("changed") else 0
            reduced += 1 if r and r.get("reduced") else 0
        done += len(batch)
        after = batch[-1]["article_id"]
        if on_progress:
            on_progress(done, total)
    return {"total": total, "changed": changed, "reduced": reduced}
