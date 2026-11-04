"""Content diagnostics and corrections. Find any item by id, URL or title and see its whole chain: source → discoveries → revisions → model receipts → decisions →
publication and sync ledger → grouping → deliveries. Visibility changes and manual corrections go through editorial overrides with a version check, are
re-projected to every public exit, and are audited. Port of admin/content.ts (without the publication-date correction, the presentation refresh and the
regroup, which need the parts of the pipeline that are not ported yet)."""

from __future__ import annotations

import re

from psycopg.types.json import Jsonb

from newsroom.core.urls import identity_key_for_url, normalize_url
from newsroom.engine import taxonomy as T
from newsroom.engine.admin import validation as V
from newsroom.engine.audit import Conflict, audit, audit_history
from newsroom.engine.errors import BadRequest
from newsroom.engine.events.hot import compute_hot_ranking, stored_hot_ranking
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.publication.publish import publish_article_tx
from newsroom.engine.reports_correct import correct_report_classification
from newsroom.engine.sources.collect import queue_processing

ARTICLE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
STALE = "這條內容的人工設定已被修改，請重新整理後再操作"
VISIBILITIES = ["public", "summary-only", "withdrawn"]
STEPS = ["extract", "analyze", "group", "presentation"]
REQUEST_ID = re.compile(r"^[\w-]{8,80}$", re.A)


def search_content(pool, q: str) -> list[dict]:
    term = q.strip()
    if not term:
        return []

    def read(conn, where: str, params):
        return conn.execute(
            f"""SELECT a.id, coalesce(p.title, a.title) AS title, a.url, s.name AS source, a.discovered_at, a.processing_state, p.visibility, p.selected, p.score
                FROM articles a JOIN sources s ON s.id = a.source_id LEFT JOIN publications p ON p.article_id = a.id
                WHERE {where} ORDER BY a.discovered_at DESC, a.id LIMIT 50""",
            params,
        ).fetchall()

    with pool.connection() as conn:
        # Exact lookups use the identity indexes; a title search must not force a full-table join for an ID.
        if ARTICLE_ID.search(term):
            found = read(conn, "a.id = %s", (term,))
            if found:
                return found
        url = normalize_url(term) if re.match(r"^https?://", term, re.I) else None
        if url:
            return read(conn, "a.url IN (%s, %s) OR a.identity_key IN (%s, %s)", (term, url, identity_key_for_url(term), identity_key_for_url(term, keep_fragment=True)))
        return read(conn, "a.title ILIKE %s OR p.title ILIKE %s", (f"%{term}%", f"%{term}%"))


def content_chain(pool, article_id: str) -> dict | None:
    with pool.connection() as conn:
        article = conn.execute(
            """SELECT a.id, a.source_id, a.url, a.identity_key, a.title, a.author, a.language, a.published_at, a.published_at_claim, a.discovered_at,
                      a.timeline_at, a.backfill, a.body_status, a.revision, a.processing_state, a.processing_error, a.grouped_at, length(a.body_text) AS body_chars,
                      s.name AS source_name, s.kind AS source_kind, s.tier, s.participation_mode, s.site_fulltext, s.syndicate_fulltext
               FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s""",
            (article_id,),
        ).fetchone()
        if not article:
            return None
        one = lambda sql: conn.execute(sql, (article_id,)).fetchall()  # noqa: E731
        discoveries = one("SELECT source_id, via, discovered_at FROM article_discoveries WHERE article_id = %s ORDER BY discovered_at")
        revisions = one("SELECT revision, title, content_hash, created_at FROM article_revisions WHERE article_id = %s ORDER BY revision DESC LIMIT 10")
        analyses = one(
            """SELECT an.id, an.origin, an.model, an.prompt_version, an.input_revision, an.relevance, an.category, an.score, an.selected, an.title_zh, an.reason_zh,
                      an.created_at,
                      (SELECT coalesce(jsonb_agg(jsonb_build_object('id', r.id, 'status', r.status, 'service', r.service, 'model', r.model, 'cost', r.cost, 'at', r.created_at) ORDER BY r.id), '[]'::jsonb)
                         FROM receipts r WHERE r.id = ANY(an.receipt_ids)) AS receipts
               FROM analyses an WHERE an.article_id = %s ORDER BY an.created_at DESC LIMIT 10"""
        )
        publication = one("SELECT * FROM publications WHERE article_id = %s")
        override = one("SELECT fields, visibility, reason, version, updated_by, updated_at FROM editorial_overrides WHERE article_id = %s")
        ledger = one("SELECT seq, op, visible_at, changed_at FROM selected_ledger WHERE article_id = %s ORDER BY seq DESC LIMIT 10")
        membership = one(
            """SELECT fa.fact_id, fa.role, fa.manual, f.public_id AS fact_public_id, f.title AS fact_title, st.id AS story_id, st.public_id AS story_public_id, st.title AS story_title
               FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id LEFT JOIN stories st ON st.id = f.story_id WHERE fa.article_id = %s"""
        )
        decisions = one("SELECT verdict, fact_id, story_id, receipt_id, candidates, created_at FROM grouping_decisions WHERE article_id = %s ORDER BY created_at DESC LIMIT 5")
        deliveries = conn.execute(
            "SELECT target_key, dedupe_key, status, attempts, response, created_at, sent_at FROM deliveries WHERE subject_id = %s ORDER BY created_at DESC", (article_id,)
        ).fetchall()
        history = audit_history(conn, f"content:{article_id}")
    return {
        "article": article, "discoveries": discoveries, "revisions": revisions, "analyses": analyses, "publication": publication[0] if publication else None,
        "override": override[0] if override else None, "ledger": ledger, "membership": membership, "decisions": decisions, "deliveries": deliveries, "history": history,
    }  # fmt: skip


def _in_hot_ranking(conn, article_id: str) -> bool:
    """Whether the current hot ranking shows the article: as an event's representative or among its reports."""
    ranking = stored_hot_ranking(conn)
    if not ranking or not ranking["entries"]:
        return False
    if any(e.get("representativeItemId") == article_id for e in ranking["entries"]):
        return True
    p = conn.execute("SELECT story_id FROM publications WHERE article_id = %s", (article_id,)).fetchone()
    return bool(p and p["story_id"]) and any(e.get("storyId") == int(p["story_id"]) for e in ranking["entries"])


def _override_row(conn, article_id: str) -> dict:
    # Use the same first lock as publication and automatic processing, including the first correction.
    if not conn.execute("SELECT id FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone():
        raise BadRequest("內容不存在")
    o = conn.execute("SELECT fields, visibility, version FROM editorial_overrides WHERE article_id = %s", (article_id,)).fetchone()
    return o or {"fields": {}, "visibility": None, "version": 0}


def _parse_reason_version(raw, issues: V.Issues, extra_visibility: bool = False) -> dict:
    data = V.object_input(raw, issues)
    out: dict = {}
    if data is None:
        return out
    g = lambda k: data.get(k, V.MISSING)  # noqa: E731
    if extra_visibility:
        out["visibility"] = V.enum(g("visibility"), issues, "visibility", VISIBILITIES)
    out["reason"] = V.string(g("reason"), issues, "reason", min_len=1, trim=True)
    out["version"] = V.integer(g("version"), issues, "version", 0, 2**53)
    return out


def set_visibility(pool, article_id: str, inp, actor: str):
    """Public / summary-only / withdrawn. Applies to the site, API, RSS, MCP, the sync ledger and the search index through the one publication projection."""
    issues = V.Issues()
    _parse_reason_version(inp, issues, extra_visibility=True)
    issues.raise_if_any()
    with pool.connection() as conn, conn.transaction():
        before = _override_row(conn, article_id)
        if before["version"] != inp["version"]:
            raise Conflict(STALE)
        conn.execute(
            """INSERT INTO editorial_overrides (article_id, visibility, reason, version, updated_by) VALUES (%s, %s, %s, 1, %s)
               ON CONFLICT (article_id) DO UPDATE SET visibility = EXCLUDED.visibility, reason = EXCLUDED.reason, version = editorial_overrides.version + 1,
                 updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (article_id, inp["visibility"], inp["reason"], actor),
        )
        published = publish_article_tx(conn, article_id)
        hot = False
        if (published or {}).get("reduced") or (before["visibility"] or "public") != inp["visibility"]:
            hot = _in_hot_ranking(conn, article_id)
            stories = conn.execute(
                "SELECT DISTINCT f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE fa.article_id = %s AND f.story_id IS NOT NULL", (article_id,)
            ).fetchall()
            for s in stories:
                enqueue(conn, QUEUES["digest"], {"storyId": s["story_id"]}, singleton_key=f"story:{s['story_id']}")
        audit(conn, actor, "content.visibility", f"content:{article_id}", inp["reason"], {"visibility": before["visibility"]}, {"visibility": inp["visibility"]})
    if hot:  # ranking reads committed publications
        compute_hot_ranking(pool)
    return published


def set_seo_indexed(pool, article_id: str, inp, actor: str):
    """Marks a detail page for search indexing (sitemap, IndexNow, robots) or removes the mark."""
    issues = V.Issues()
    data = V.object_input(inp, issues)
    if data is not None:
        V.boolean(data.get("indexed", V.MISSING), issues, "indexed")
        V.string(data.get("reason", V.MISSING), issues, "reason", min_len=1, trim=True)
    issues.raise_if_any()
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT id FROM articles WHERE id = %s FOR UPDATE", (article_id,))
        before = conn.execute("SELECT indexable FROM publications WHERE article_id = %s", (article_id,)).fetchone()
        if not before:
            return None
        indexed = inp["indexed"]
        conn.execute(
            """UPDATE publications SET seo_indexed_at = CASE WHEN %(i)s THEN coalesce(seo_indexed_at, now()) ELSE NULL END,
                 seo_excluded_at = CASE WHEN %(i)s THEN NULL ELSE now() END WHERE article_id = %(id)s""",
            {"i": indexed, "id": article_id},
        )
        published = publish_article_tx(conn, article_id)
        audit(conn, actor, "content.seo", f"content:{article_id}", inp["reason"], {"indexed": before["indexable"]}, {"indexed": indexed})
        return published


FIELD_KEYS = ["title", "summary", "reason", "category", "tags", "score", "selected", "silent"]


def _parse_fields(raw) -> dict:
    issues = V.Issues()
    data = V.object_input(raw, issues)
    out: dict = {}
    if data is not None:
        for key in FIELD_KEYS:
            if key not in data:
                continue
            v = data[key]
            if key == "title":
                out[key] = V.string(v, issues, key, min_len=1, max_len=300)
            elif key == "summary":
                out[key] = V.string(v, issues, key, max_len=2000)
            elif key == "reason":
                out[key] = V.string(v, issues, key, max_len=1000)
            elif key == "category":
                out[key] = V.enum(v, issues, key, T.category_keys())
            elif key == "tags":
                out[key] = V.string_array(v, issues, key, max_items=20, max_len=60)
            elif key == "score":
                out[key] = None if v is None else V.number(v, issues, key, 0, 100)
            else:
                out[key] = V.boolean(v, issues, key)
        V.strict_keys(data, FIELD_KEYS, issues)
    issues.raise_if_any()
    return out


def override_fields(pool, article_id: str, inp, actor: str):
    """Manual corrections win over model output; `clear` removes a correction."""
    issues = V.Issues()
    head = _parse_reason_version(inp, issues)
    if isinstance(inp, dict) and "clear" in inp:
        V.string_array(inp["clear"], issues, "clear")
    issues.raise_if_any()
    fields = _parse_fields(inp.get("fields") if inp.get("fields") is not None else {})
    clear = inp.get("clear") or []
    with pool.connection() as conn, conn.transaction():
        before = _override_row(conn, article_id)
        if before["version"] != head["version"]:
            raise Conflict(STALE)
        nxt = {**before["fields"], **fields}
        for k in clear:
            nxt.pop(k, None)
        conn.execute(
            """INSERT INTO editorial_overrides (article_id, fields, reason, version, updated_by) VALUES (%s, %s, %s, 1, %s)
               ON CONFLICT (article_id) DO UPDATE SET fields = EXCLUDED.fields, reason = EXCLUDED.reason, version = editorial_overrides.version + 1,
                 updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (article_id, Jsonb(nxt), inp["reason"], actor),
        )
        published = publish_article_tx(conn, article_id)
        if (published or {}).get("changed"):
            changed_fields = {*fields, *clear}
            if "category" in changed_fields or "tags" in changed_fields:
                correct_report_classification(conn, article_id, inp["reason"])
            st = conn.execute("SELECT story_id FROM publications WHERE article_id = %s", (article_id,)).fetchone()
            if st and st["story_id"] and any(k not in ("category", "tags") for k in changed_fields):
                enqueue(conn, QUEUES["digest"], {"storyId": st["story_id"]}, singleton_key=f"story:{st['story_id']}")
        audit(conn, actor, "content.override", f"content:{article_id}", inp["reason"], before["fields"], nxt)
        return published


def rerun(pool, article_id: str, step, request_id: str, actor: str, options: dict | None = None):
    """Re-runs a pipeline step for the current revision. Re-evaluation is a new paid model call bound to the request id, so submitting the same request twice
    neither enqueues nor pays twice."""
    issues = V.Issues()
    V.enum(step, issues, "", STEPS)
    if not REQUEST_ID.search(request_id):
        issues.add("", "a stable request id is required")
    issues.raise_if_any()
    if step == "presentation":
        raise NotImplementedError("refreshing a body's presentation needs the translation rebase, which is not ported yet")
    if step == "group":
        raise NotImplementedError("regrouping needs the event grouping, which is not ported yet")
    with pool.connection() as conn, conn.transaction():
        if not conn.execute("SELECT id FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone():
            return None
        # Queue singleton keys expire when a job completes. The committed command remains in the audit.
        prior = conn.execute(
            """SELECT after FROM audit_log WHERE subject = %s AND action = %s AND actor = %s AND request_id = %s ORDER BY id DESC LIMIT 1""",
            (f"content:{article_id}", f"content.rerun.{step}", actor, request_id),
        ).fetchone()
        if prior:
            return {"jobId": prior["after"]["jobId"]}
        conn.execute(
            """UPDATE articles SET processing_state = 'new', processing_error = NULL, processing_attempts = 0, processing_retry_at = NULL,
                 body_status = CASE WHEN %s THEN 'pending' ELSE body_status END WHERE id = %s""",
            (step == "extract", article_id),
        )
        job = queue_processing(conn, article_id, step=step, attempt_tag=f"admin:{request_id}" if step == "analyze" else None)
        audit(conn, actor, f"content.rerun.{step}", f"content:{article_id}", None, None, {"jobId": job, "requestId": request_id}, request_id=request_id)
        return {"jobId": job}
