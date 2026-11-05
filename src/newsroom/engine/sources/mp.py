"""WeChat official accounts. Dajiala (a paid service) supplies each account's latest posts and article bodies; every enabled account is checked once per source interval.
Port of sources/mp.ts."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from newsroom.core.urls import identity_key_for_url
from newsroom.engine.dajiala import dajiala_configured, mp_article, mp_history
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.text import js_slice, strip_tags
from newsroom.engine.materials import Material, upsert_material
from newsroom.engine.receipts import failure_kind
from newsroom.engine.sanitize import sanitize_body
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.sources.collect import queue_processing

MAX_NEW_PER_CHECK = 8
FIRST_CHECK_WINDOW_MS = 7 * 86400_000  # posts older than this on the first check of an account are history, not news
BODY_RETRIES = 3  # a body missing for a passing reason is fetched again on later checks: this often, while the post is this recent
BODY_RETRY_WINDOW_MS = 3 * 86400_000


def _iso(d: datetime) -> str:
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _fetch_body(pool, url: str, source_id: str, identity: str) -> tuple[dict | None, str | None]:
    """The article body, and when it is missing for a reason that may pass (rate limit, server error, lost answer), that reason."""
    try:
        return mp_article(pool, url, source_id, identity), None
    except Exception as error:
        kind = failure_kind(error)
        if kind == "budget":
            raise
        return None, None if kind == "refused" else js_slice(str(error), 0, 200)


def check_mp_account(pool, source_id: str, reason: str) -> dict:
    with pool.connection() as conn:
        source = conn.execute(
            "SELECT id, name, config, cursor, enabled, participation_mode FROM sources WHERE id = %s AND kind = 'mp_account'", (source_id,)
        ).fetchone()
    if not source:
        return {"sourceId": source_id, "status": "missing"}
    if not source["enabled"] and reason != "manual":
        return {"sourceId": source_id, "status": "paused"}
    ghid = source["config"].get("ghid") or source["config"].get("wxid")
    if not ghid:
        return {"sourceId": source_id, "status": "unconfigured"}
    with pool.connection() as conn:
        run_id = conn.execute(
            "INSERT INTO fetch_runs (source_id, detail) VALUES (%s, %s) RETURNING id", (source_id, Jsonb({"reason": reason}))
        ).fetchone()["id"]
    cursor = source["cursor"] or {}
    first_check = not cursor.get("lastCheckedAt")
    created = 0
    try:
        # One paid list call per account per 10-minute window, whoever asks.
        window = f"{'s' if reason == 'schedule' else 'm'}:{int(time.time() * 1000 // 600_000)}"
        history = mp_history(pool, ghid, source_id, window)
        posts = sorted(history["posts"], key=lambda p: -(p.get("post_time") or 0))
        fetched = 0
        for p in posts:
            if not p.get("url") or not p.get("title") or fetched >= MAX_NEW_PER_CHECK:
                continue
            published_at = datetime.fromtimestamp(p["post_time"], UTC) if p.get("post_time") else None
            if first_check and published_at and time.time() * 1000 - published_at.timestamp() * 1000 > FIRST_CHECK_WINDOW_MS:
                continue
            # Same identity rule as every entrance: the long link without tracking parameters. A known post is skipped, unless its body failed for a passing reason: then
            # it is tried again a few times.
            key = identity_key_for_url(p["url"])
            known = None
            if key:
                with pool.connection() as conn:
                    known = conn.execute(
                        "SELECT id, body_status, discovered_at, raw->'dajiala'->'bodyRetry' AS retry FROM articles WHERE identity_key = %s LIMIT 1",
                        (key,),
                    ).fetchone()
            retry_body = (
                bool(known and known["retry"])
                and known["body_status"] == "none"
                and known["retry"]["attempts"] < BODY_RETRIES
                and time.time() * 1000 - known["discovered_at"].timestamp() * 1000 < BODY_RETRY_WINDOW_MS
            )
            if known and not retry_body:
                continue
            fetched += 1
            # Without a body the post is listed anyway; analysis works from title and digest.
            body, passing = _fetch_body(pool, p["url"], source_id, p.get("sn") or p["url"])
            if known and not (body and body["content"]):
                with pool.connection() as conn:
                    if passing:
                        conn.execute(
                            "UPDATE articles SET raw = jsonb_set(raw, '{dajiala,bodyRetry}', %s) WHERE id = %s",
                            (Jsonb({"attempts": known["retry"]["attempts"] + 1, "error": passing}), known["id"]),
                        )
                    else:
                        conn.execute("UPDATE articles SET raw = raw #- '{dajiala,bodyRetry}' WHERE id = %s", (known["id"],))
                continue
            # Mode 1 bodies are light HTML (paragraphs and image tags).
            html = sanitize_body(body["content"], p["url"]) if body and body["content"] else None
            text = strip_tags(body["content"]) if body and body["content"] else None
            res = upsert_material(pool, Material(
                source_id=source_id, url=p["url"], title=p["title"], via="fetch", author=body["author"] if body else None, language="zh", published_at=published_at,
                excerpt=p.get("digest") or (body["desc"] if body else None), body_html=html, body_text=text or None, body_status="ok" if text else "none",
                backfill="first-import" if first_check else None,
                raw={"dajiala": {
                    "position": p.get("position"), "sn": p.get("sn"), "original": p.get("original"), "itemShowType": p.get("item_show_type"), "cover": p.get("cover_url"),
                    **({"bodyRetry": {"attempts": 1, "error": passing}} if passing else {}),
                }},
            ))  # fmt: skip
            # A body fetched again arrives as a new revision (analysed again); stop retrying it.
            if known:
                with pool.connection() as conn:
                    conn.execute("UPDATE articles SET raw = raw #- '{dajiala,bodyRetry}' WHERE id = %s", (known["id"],))
            if res.created:
                created += 1
            if res.created or res.revised or res.processing_needed:
                queue_processing(pool, res.article_id)
        new_cursor = {
            **cursor, "lastCheckedAt": _iso(datetime.now(UTC)), "lastPostTime": posts[0].get("post_time") if posts else cursor.get("lastPostTime"), "remainMoney": history["remainMoney"],
        }  # fmt: skip
        with pool.connection() as conn:
            conn.execute(
                """UPDATE sources SET last_fetch_at = now(), last_ok_at = now(), fail_count = 0, last_error = NULL, health = 'ok', cursor = %s,
                     next_fetch_at = now() + make_interval(mins => interval_minutes), updated_at = now() WHERE id = %s""",
                (Jsonb(new_cursor), source_id),
            )
            conn.execute(
                "UPDATE fetch_runs SET status = 'ok', finished_at = now(), found_count = %s, new_count = %s WHERE id = %s",
                (len(posts), created, run_id),
            )
        return {"sourceId": source_id, "status": "ok", "found": len(posts), "created": created, "reused": history["reused"]}
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        message = js_slice(str(error), 0, 500)
        kind = failure_kind(error)
        soft = kind in ("budget", "outage")
        with pool.connection() as conn:
            conn.execute(
                """UPDATE sources SET last_fetch_at = now(), last_error = %(m)s, fail_count = CASE WHEN %(soft)s THEN fail_count ELSE fail_count + 1 END,
                     health = CASE WHEN %(soft)s THEN health WHEN fail_count + 1 >= 3 THEN 'failing' ELSE 'degraded' END, updated_at = now() WHERE id = %(id)s""",
                {"m": message, "soft": soft, "id": source_id},
            )
            conn.execute(
                "UPDATE fetch_runs SET status = 'failed', finished_at = now(), new_count = %s, error = %s WHERE id = %s", (created, message, run_id)
            )
        if soft:
            raise  # retried by the queue
        return {"sourceId": source_id, "status": "failed", "error": message}


def schedule_mp_reconcile(pool, now: datetime | None = None) -> dict:
    """Every enabled account is checked once per its interval (the paid list call is the cost)."""
    # Without Dajiala there is nothing to check the accounts with.
    if not dajiala_configured():
        return {"enqueued": 0}
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, cursor->>'lastCheckedAt' AS last, interval_minutes FROM sources WHERE kind = 'mp_account' AND enabled"
        ).fetchall()
    enqueued = 0
    for s in rows:
        since = now.timestamp() * 1000 - datetime.fromisoformat(s["last"].replace("Z", "+00:00")).timestamp() * 1000 if s["last"] else float("inf")
        if since <= s["interval_minutes"] * 60_000:
            continue
        enqueue(pool, QUEUES["mpCheck"], {"sourceId": s["id"], "reason": "schedule"}, singleton_key=f"mp:{s['id']}")
        enqueued += 1
    return {"enqueued": enqueued}
