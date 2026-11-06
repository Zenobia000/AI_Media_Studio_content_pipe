"""Content diagnostics and corrections. Find any item by id, URL or title and see its whole chain: source → discoveries → revisions → model receipts → decisions →
publication and sync ledger → grouping → deliveries. Visibility changes and manual corrections go through editorial overrides with a version check, are
re-projected to every public exit, and are audited. Port of admin/content.ts, including the publication-date correction."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import cast

from psycopg.types.json import Jsonb

from newsroom.core.urls import identity_key_for_url, normalize_url
from newsroom.engine import taxonomy as T
from newsroom.engine.admin import validation as V
from newsroom.engine.audit import Conflict, audit, audit_history
from newsroom.engine.errors import BadRequest
from newsroom.engine.events.hot import compute_hot_ranking, stored_hot_ranking
from newsroom.engine.jobs.content import needs_grouping
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.ids import sha256, stable_json
from newsroom.engine.materials import decide_timeline, explicit_backfill, is_historical
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
            return read(
                conn,
                "a.url IN (%s, %s) OR a.identity_key IN (%s, %s)",
                (term, url, identity_key_for_url(term), identity_key_for_url(term, keep_fragment=True)),
            )
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
        revisions = one(
            "SELECT revision, title, content_hash, created_at FROM article_revisions WHERE article_id = %s ORDER BY revision DESC LIMIT 10"
        )
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
        decisions = one(
            "SELECT verdict, fact_id, story_id, receipt_id, candidates, created_at FROM grouping_decisions WHERE article_id = %s ORDER BY created_at DESC LIMIT 5"
        )
        deliveries = conn.execute(
            "SELECT target_key, dedupe_key, status, attempts, response, created_at, sent_at FROM deliveries WHERE subject_id = %s ORDER BY created_at DESC",
            (article_id,),
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
                "SELECT DISTINCT f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE fa.article_id = %s AND f.story_id IS NOT NULL",
                (article_id,),
            ).fetchall()
            for s in stories:
                enqueue(conn, QUEUES["digest"], {"storyId": s["story_id"]}, singleton_key=f"story:{s['story_id']}")
        audit(
            conn,
            actor,
            "content.visibility",
            f"content:{article_id}",
            inp["reason"],
            {"visibility": before["visibility"]},
            {"visibility": inp["visibility"]},
        )
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
        return refresh_presentation(pool, article_id, options or {}, request_id, actor)
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
        if step == "group":
            from newsroom.engine.events.corrections import request_regroup

            job = request_regroup(conn, article_id, request_id)
        else:
            conn.execute(
                """UPDATE articles SET processing_state = 'new', processing_error = NULL, processing_attempts = 0, processing_retry_at = NULL,
                     body_status = CASE WHEN %s THEN 'pending' ELSE body_status END WHERE id = %s""",
                (step == "extract", article_id),
            )
            job = queue_processing(conn, article_id, step=step, attempt_tag=f"admin:{request_id}" if step == "analyze" else None)
        audit(
            conn, actor, f"content.rerun.{step}", f"content:{article_id}", None, None, {"jobId": job, "requestId": request_id}, request_id=request_id
        )
        return {"jobId": job}


def _presentation_snapshot(conn, article_id: str, lock: bool = False) -> dict | None:
    return conn.execute(
        f"""SELECT a.revision, a.title, a.body_text, a.excerpt, a.body_html, a.media, a.source_id, a.identity_key, a.url, s.kind, s.config,
              tr.body_html AS tr_html, tr.origin AS tr_origin
            FROM articles a JOIN sources s ON s.id = a.source_id
            LEFT JOIN translations tr ON tr.article_id = a.id AND tr.lang = 'zh' AND tr.revision = a.revision
            WHERE a.id = %s {"FOR UPDATE OF a" if lock else ""}""",
        (article_id,),
    ).fetchone()


def refresh_presentation(pool, article_id: str, options: dict, request_id: str, actor: str) -> dict | None:
    """Preview and apply through the existing audited command, without analysis, date changes or paid extraction."""
    from newsroom.engine.extract import extract_from_url, page_fetchable
    from newsroom.engine.lib.ids import sha256, stable_json
    from newsroom.engine.materials import content_hash, update_presentation
    from newsroom.engine.page_reader import page_rules
    from newsroom.engine.presentation import PresentationMismatch, presentation_counts, rebase_translation
    from newsroom.engine.sources.collect import listing_entries, load_source
    from newsroom.engine.sources.rss import fetch_rss

    preview, version = options.get("preview"), options.get("version")
    issues = V.Issues()
    if preview is not None and not isinstance(preview, bool):
        issues.add("preview", "expected boolean")
    if version is not None and not (isinstance(version, str) and re.fullmatch(r"[a-f0-9]{64}", version)):
        issues.add("version", "invalid")
    issues.raise_if_any()
    if not preview and not version:
        raise Conflict("請先預覽正文展示變化")
    if not preview:
        with pool.connection() as conn:
            prior = conn.execute(
                "SELECT after FROM audit_log WHERE subject = %s AND action = 'content.rerun.presentation' AND actor = %s AND request_id = %s ORDER BY id DESC LIMIT 1",
                (f"content:{article_id}", actor, request_id),
            ).fetchone()
        if prior:
            return prior["after"]["result"]
    with pool.connection() as conn:
        a = _presentation_snapshot(conn, article_id)
    if not a:
        return None
    # A feed can publish different wording from its web page. Refresh the same source material first.
    got = None
    if a["kind"] == "rss" and a["config"].get("feedUrl"):
        source = cast(dict, load_source(pool, a["source_id"]))
        with pool.connection() as conn:
            entries = listing_entries(conn, source, fetch_rss(source, True)["candidates"])
        entry = next((c for c in entries if c["identity_key"] == a["identity_key"]), None)
        if entry and entry.get("body_html") and entry.get("body_text"):
            got = {"html": entry["body_html"], "text": entry["body_text"], "images": entry.get("media") or []}
    if not got and page_fetchable(a["url"], a["kind"]):
        page = extract_from_url(a["url"], page_rules(a["config"]), paid_fallback=False, pool=pool, subject=f"article:{article_id}")
        # Match normal extraction: the publisher's existing cover has precedence over body images.
        if page:
            got = {"html": page.html, "text": page.text, "images": a["media"] if a["media"] else page.images}
    before, after = presentation_counts(a["body_html"]), presentation_counts(got["html"] if got else a["body_html"])
    if not got:
        reason = "當前原文未能直接抽取，請使用正常正文抽取處理"
    elif content_hash(a["title"], a["body_text"], a["excerpt"]) != content_hash(a["title"], got["text"], a["excerpt"]):
        reason = "正文文字已經變化，需要正常抽取和評估，不能只重新整理展示"
    else:
        reason = None
    translation = "source" if a["tr_origin"] == "source" else "reused" if a["tr_html"] else "absent"
    if not reason and got and got["html"] != a["body_html"] and a["tr_html"] and a["tr_origin"] == "model":
        try:
            rebase_translation(a["body_html"] or "", got["html"], a["tr_html"])
        except PresentationMismatch as error:
            reason = str(error)
            translation = "blocked"
    snapshot_key = {"before": a, "html": got["html"] if got else None, "media": got["images"] if got else None}
    version_now = sha256(stable_json({k: v for k, v in snapshot_key.items() if v is not None}))
    result = {
        "revision": a["revision"], "version": version_now, "ready": reason is None, "applied": False,
        "changed": got is not None and (got["html"] != a["body_html"] or stable_json(got["images"]) != stable_json(a["media"])), "reason": reason,
        "images": {"before": before["images"], "after": after["images"]}, "links": {"before": before["links"], "after": after["links"]}, "translation": translation,
    }  # fmt: skip
    if preview:
        return result
    if version != version_now:
        raise Conflict("正文或原網頁在預覽後變化，請重新預覽")
    if reason or not got:
        raise Conflict(reason or "正文未能抽取")
    with pool.connection() as conn, conn.transaction():
        current = _presentation_snapshot(conn, article_id, True)
        prior = conn.execute(
            "SELECT after FROM audit_log WHERE subject = %s AND action = 'content.rerun.presentation' AND actor = %s AND request_id = %s ORDER BY id DESC LIMIT 1",
            (f"content:{article_id}", actor, request_id),
        ).fetchone()
        if prior:
            return prior["after"]["result"]
        if stable_json(current) != stable_json(a):
            raise Conflict("材料在預覽後變化，請重新預覽")
        update_presentation(conn, article_id, got["html"], got["images"])
        applied = {**result, "applied": True}
        audit(
            conn, actor, "content.rerun.presentation", f"content:{article_id}", "僅重新整理正文展示",
            {"revision": a["revision"], "bodyHtml": a["body_html"], "media": a["media"], "translationHtml": a["tr_html"]}, {"result": applied, "bodyHtml": got["html"], "media": got["images"]},
            request_id=request_id,
        )  # fmt: skip
        return applied


# --- Publication-date correction -----------------------------------------------------------------------------------------------------------------------------

RECENT = timedelta(days=7)  # dates within this window are recent; older ones are history's to correct
_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def _iso(value: datetime | None) -> str | None:
    """What JSON.stringify makes of a Date (ISO, milliseconds, Z)."""
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _ms(value: datetime) -> int:
    """Whole milliseconds since the epoch: Node's Date holds no finer time than that."""
    return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _historical_date(article: dict, published: datetime, date: datetime, now: datetime) -> dict:
    """An old date replaced by an older verified one keeps every decision of the material, history or not: its discovery time may be an import's estimate, so
    the date is not judged against it. The sort follows only where it was that date."""
    if date >= now - RECENT:
        raise ValueError("歷史日期校正的真實發布時間須早於七天視窗")
    iso = _iso(date)
    timeline_at = iso if _ms(article["timeline_at"]) == _ms(published) else _iso(article["timeline_at"])
    return {
        "publishedAt": iso,
        "publishedAtClaim": iso,
        "timelineAt": timeline_at,
        "backfill": article["backfill"],
        "backfillReason": article["backfill_reason"],
    }


def _live_date(article: dict, date: datetime | None) -> tuple[dict, bool]:
    """News gets its verified date, or loses an untrusted one, through the timeline rule against its first discovery; a first import or reported backfill keeps
    that nature. `archive`: the new date makes it history, so it leaves automatic events and heat."""
    if is_historical(article["published_at"], article["discovered_at"], article["backfill"]):
        raise ValueError("近期日期只校正剛收錄的非歷史內容" if date else "歷史內容只校正有明確原文證據的日期")
    t = decide_timeline(date, article["discovered_at"], explicit_backfill(article["backfill_reason"]))
    if date and not t.published_at:
        raise ValueError("發布時間超過首次收錄的一小時容差，不能據此校正")
    iso = _iso(t.published_at)
    after = {
        "publishedAt": iso,
        "publishedAtClaim": iso,
        "timelineAt": _iso(t.timeline_at),
        "backfill": t.backfill,
        "backfillReason": t.backfill_reason,
    }
    return after, is_historical(t.published_at, article["discovered_at"], t.backfill)


def publication_date_correction_plan(article: dict, published_at: str | None, now: datetime | None = None) -> dict:
    """Correct a verified date, or retract an untrusted live one (None). Replacing a date from before the recent window with another is history's correction;
    anything else is news."""
    now = now or datetime.now(UTC)
    date = None
    if published_at is not None:
        try:
            date = _parse_iso(published_at) if _ISO.match(published_at) else None
        except ValueError:
            date = None
        if date is None:
            raise ValueError("發布時間必須是完整、有效的 UTC ISO 日期")
    if date and date > now:
        raise ValueError("發布時間不能是未來日期")
    published = article["published_at"]
    if not published:
        raise ValueError("只校正有既有日期的內容")
    before = {
        "publishedAt": _iso(published), "publishedAtClaim": _iso(article["published_at_claim"]), "timelineAt": _iso(article["timeline_at"]),
        "backfill": article["backfill"], "backfillReason": article["backfill_reason"],
    }  # fmt: skip
    if date and published < now - RECENT:
        mode, after, archive = "history", _historical_date(article, published, date, now), False
    else:
        mode = "live"
        after, archive = _live_date(article, date)
    digest = sha256(stable_json({
        "id": article["id"], "identity": article["identity_key"], "source": article["source_id"], "url": article["url"], "version": article["revision"],
        "contentHash": article["content_hash"], "updatedAt": _iso(article["updated_at"]), "discoveredAt": _iso(article["discovered_at"]),
        "backfill": article["backfill"], "backfillReason": article["backfill_reason"], "before": before, "requested": published_at,
    }))  # fmt: skip
    return {
        "articleId": article["id"], "version": article["revision"], "hash": digest, "mode": mode, "before": before, "after": after, "archive": archive,
        "changed": before["publishedAt"] != published_at,
    }  # fmt: skip


_DATED_MATERIAL = "SELECT a.*, s.participation_mode FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s"


def preview_publication_date_correction(pool, article_id: str, published_at: str | None) -> dict | None:
    """Read-only plan for the operator's evidence and exact version/hash approval."""
    with pool.connection() as conn:
        article = conn.execute(_DATED_MATERIAL, (article_id,)).fetchone()
    return publication_date_correction_plan(article, published_at) if article else None


def _processing_settled(conn, a: dict) -> bool:
    """Processing has ended: judged or skipped, and grouped where the pipeline groups such material. A running processor's result is still bound to this
    revision, so news waits for it."""
    if a["processing_state"] not in ("analyzed", "blocked", "skipped"):
        return False
    if a["grouping_status"] == "complete":
        return True
    analysis = conn.execute(
        "SELECT relevance FROM analyses WHERE article_id = %s AND input_revision = %s ORDER BY id DESC LIMIT 1", (a["id"], a["revision"])
    ).fetchone()
    return not needs_grouping(
        a["participation_mode"], is_historical(a["published_at"], a["discovered_at"], a["backfill"]), analysis["relevance"] if analysis else None
    )


_DECISION = (
    "SELECT to_jsonb(p) - ARRAY['published_at','timeline_at','sort_at','revision','updated_at'] AS decision FROM publications p WHERE article_id = %s"
)


def correct_publication_date(pool, article_id: str, inp: dict, actor: str) -> dict:
    """Correct or retract dates without revising material or buying analysis; archived news leaves automatic events."""
    issues = V.Issues()
    version, digest, published_at, request_id, reason = (inp.get(k) for k in ("version", "hash", "publishedAt", "requestId", "reason"))
    if not (isinstance(version, int) and not isinstance(version, bool) and version > 0):
        issues.add("version", "expected positive integer")
    if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
        issues.add("hash", "invalid")
    if published_at is not None and not isinstance(published_at, str):
        issues.add("publishedAt", "expected string")
    if not (isinstance(request_id, str) and REQUEST_ID.search(request_id)):
        issues.add("requestId", "invalid")
    if not (isinstance(reason, str) and reason.strip()):
        issues.add("reason", "too small")
    issues.raise_if_any()
    with pool.connection() as conn, conn.transaction():
        article = conn.execute(_DATED_MATERIAL + " FOR UPDATE OF a", (article_id,)).fetchone()
        if not article:
            raise Conflict("內容不存在")
        prior = conn.execute(
            """SELECT after FROM audit_log WHERE subject = %s AND action = 'content.correct-publication-date' AND actor = %s AND request_id = %s
               ORDER BY id LIMIT 1""",
            (f"content:{article_id}", actor, request_id),
        ).fetchone()
        if prior:
            if prior["after"]["publishedAt"] != published_at:
                raise Conflict("同一個請求不能批准不同的發布時間")
            return prior["after"]["result"]
        plan = publication_date_correction_plan(article, published_at)
        if article["revision"] != version or plan["hash"] != digest:
            raise Conflict("材料或日期已被修改，請重新核對日期校正預覽")
        if not plan["changed"]:
            return {"articleId": article_id, "revision": article["revision"], "status": "unchanged", "publishedAt": published_at}
        if plan["mode"] == "live" and not _processing_settled(conn, article):
            raise Conflict("內容仍在處理中，請處理結束後重新核對日期")
        previous_story_ids: list[int] = []
        if plan["archive"]:
            # An editor's grouping is never discarded by date repair.
            if conn.execute(
                "SELECT 1 FROM grouping_overrides WHERE article_id = %s UNION ALL SELECT 1 FROM fact_articles WHERE article_id = %s AND manual LIMIT 1",
                (article_id, article_id),
            ).fetchone():
                raise Conflict("這條內容有人工歸組，請先核對其歸屬")
            from newsroom.engine.events.group import reset_automatic

            previous_story_ids = reset_automatic(conn, article_id)
            for story_id in previous_story_ids:
                enqueue(conn, QUEUES["digest"], {"storyId": story_id}, singleton_key=f"story:{story_id}")
        # Every editorial decision is kept; archiving also changes freshness and the automatic event projection.
        previous = conn.execute(_DECISION, (article_id,)).fetchone()
        after = plan["after"]
        conn.execute(
            """UPDATE articles SET publication_time = %s, published_at = %s, published_at_claim = %s, timeline_at = %s, backfill = %s, backfill_reason = %s,
                 updated_at = now() WHERE id = %s""",
            (
                after["publishedAt"], _parse_iso(after["publishedAt"]) if after["publishedAt"] else None,
                _parse_iso(after["publishedAtClaim"]) if after["publishedAtClaim"] else None, _parse_iso(after["timelineAt"]), after["backfill"],
                after["backfillReason"], article_id,
            ),
        )  # fmt: skip
        if previous:
            publish_article_tx(conn, article_id)
            nxt = conn.execute(_DECISION, (article_id,)).fetchone()
            if not plan["archive"] and stable_json(previous["decision"]) != stable_json(nxt["decision"] if nxt else None):
                old, fresh = previous["decision"], (nxt["decision"] if nxt else None) or {}
                changed = [k for k in old if stable_json(old[k]) != stable_json(fresh.get(k))]
                raise Conflict(f"公開決定已變化，日期校正不能改變選稿、範圍、內容或歸組：{'、'.join(changed)}")
        # (emit("articleChanged") has no listener here: the engine runs without site modules)
        result = {"articleId": article_id, "revision": article["revision"], "status": "corrected", "publishedAt": published_at}
        audit(
            conn, actor, "content.correct-publication-date", f"content:{article_id}", reason, plan["before"],
            {**after, "previousStoryIds": previous_story_ids, "result": result}, request_id=request_id,
        )  # fmt: skip
        return result
