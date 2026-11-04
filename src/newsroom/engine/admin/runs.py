"""Runs view: task timeline, queue backlog, source lag, error classes, process heartbeats, and the receipts and deliveries whose outcome needs an operator, plus
the nav counts. Port of admin/runs.ts, admin/navigation.ts and operations/grouping.ts (on this engine's own queue tables)."""

from __future__ import annotations

from datetime import UTC, datetime

from newsroom.engine.audit import audit
from newsroom.engine.sources.collect import queue_processing

STALE_HEARTBEAT_MS = 3 * 60_000
GROUPING_WARN_AFTER_MS = 10 * 60_000
AUTO_RELEASE_NOTE = "自動放行：結果未知超過 30 分鐘，未核對是否計費"
AWAITING_RELEASE = "(receipts.status = 'unknown' AND receipts.request->>'fallbackReceiptId' IS NULL)"
FAILURE_GROUP_SQL = "regexp_replace(left(coalesce(processing_error, '(no message)'), 120), '[0-9a-f]{8,}|[0-9]{4,}', '…', 'g')"


def nav_counts(pool) -> dict:
    """Items waiting for the admin, on the navigation: new feedback, failing sources, receipts and deliveries in doubt."""
    with pool.connection() as conn:
        return conn.execute(
            f"""SELECT (SELECT count(*)::int FROM feedback WHERE status = 'new') AS feedback,
                       (SELECT count(*)::int FROM sources WHERE enabled AND health = 'failing') AS sources,
                       (SELECT count(*)::int FROM receipts WHERE {AWAITING_RELEASE}) + (SELECT count(*)::int FROM deliveries WHERE status = 'unknown') AS runs"""
        ).fetchone()


def waiting_selected_news(conn) -> list[dict]:
    """Selected candidates waiting for a news identity: one reader-impact view for alerts and the admin."""
    rows = conn.execute(
        """SELECT p.article_id AS "articleId", p.title,
                  greatest(coalesce(p.selected_ready_at, p.discovered_at), revision.created_at,
                           (SELECT max(j.created_on) FROM jobs j WHERE j.name = 'events.group' AND j.data->>'articleId' = a.id
                              AND j.singleton_key LIKE 'manual:group:%%')) AS since,
                  a.grouping_status = 'failed' AS failed, a.grouping_receipt_id AS "receiptId", a.grouping_error AS error,
                  r.status AS receipt_status,
                  EXISTS (SELECT 1 FROM jobs j WHERE j.name = 'events.group' AND j.state IN ('created', 'retry', 'active') AND j.data->>'articleId' = a.id) AS running,
                  EXISTS (SELECT 1 FROM receipt_attempts attempt WHERE attempt.receipt_id = r.id AND attempt.error LIKE %s) AS released
           FROM publications p JOIN articles a ON a.id = p.article_id LEFT JOIN receipts r ON r.id = a.grouping_receipt_id
           LEFT JOIN article_revisions revision ON revision.article_id = a.id AND revision.revision = a.revision
           WHERE p.selection_candidate AND p.eligible AND p.visibility = 'public' AND a.grouping_status <> 'complete'
           ORDER BY since, p.article_id""",
        (AUTO_RELEASE_NOTE + "%",),
    ).fetchall()
    out = []
    for row in rows:
        receipt_status, released, running = row.pop("receipt_status"), row.pop("released"), row.pop("running")
        if receipt_status == "unknown":
            recovery = "manual" if released else "receipt"
        else:
            recovery = "automatic" if not row["failed"] or running or receipt_status == "pending" else "manual"
        out.append({**row, "recovery": recovery})
    return out


def grouping_overview(items: list[dict], now_ms: float) -> dict:
    return {
        "waiting": len(items),
        "needsAttention": sum(1 for i in items if now_ms - i["since"].timestamp() * 1000 >= GROUPING_WARN_AFTER_MS),
        "items": items[:30],
    }


def runs_overview(pool) -> dict:
    with pool.connection() as conn:
        heartbeats = conn.execute("SELECT key, value, updated_at FROM settings WHERE key LIKE 'heartbeat.%' ORDER BY key").fetchall()
        latest = conn.execute(
            """WITH current_jobs AS (SELECT DISTINCT substring(name FROM 6) AS job FROM job_schedules)
               SELECT current_jobs.job, latest.*, counts.failed_24h, counts.runs_24h
               FROM current_jobs
               JOIN LATERAL (
                 SELECT started_at, finished_at, status, left(error, 400) AS error FROM job_runs
                 WHERE job = current_jobs.job ORDER BY started_at DESC, id DESC LIMIT 1
               ) latest ON true
               CROSS JOIN LATERAL (
                 SELECT count(*) FILTER (WHERE status = 'failed')::int AS failed_24h, count(*)::int AS runs_24h
                 FROM job_runs WHERE job = current_jobs.job AND started_at > now() - interval '24 hours'
               ) counts ORDER BY current_jobs.job"""
        ).fetchall()
        timeline = conn.execute("SELECT id, job, started_at, finished_at, status, left(error, 300) AS error FROM job_runs ORDER BY started_at DESC, id DESC LIMIT 80").fetchall()
        queues = conn.execute(
            "SELECT name, state, count(*)::int AS n, min(created_on) AS oldest FROM jobs WHERE state IN ('created', 'retry', 'active') GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        failed_jobs = conn.execute(
            """SELECT name, count(*)::int AS failed, max(completed_on) AS last, left((array_agg(output::text ORDER BY completed_on DESC))[1], 300) AS last_output
               FROM jobs WHERE state = 'failed' AND completed_on > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC, 1"""
        ).fetchall()
        lagging = conn.execute(
            """SELECT id, name, kind, health, fail_count, last_ok_at, last_fetch_at, next_fetch_at, interval_minutes, left(last_error, 200) AS last_error
               FROM sources
               WHERE enabled AND kind NOT IN ('mp_account', 'external')
                 AND (health = 'failing' OR next_fetch_at < now() - interval '30 minutes' OR last_ok_at < now() - make_interval(mins => greatest(interval_minutes * 6, 360)))
               ORDER BY health = 'failing' DESC, next_fetch_at, id LIMIT 60"""
        ).fetchall()
        receipts = conn.execute("SELECT status, count(*)::int AS n FROM receipts WHERE created_at > now() - interval '7 days' GROUP BY 1 ORDER BY 1").fetchall()
        receipt_issues = conn.execute(
            f"""SELECT id, service, model, purpose, subject, status, attempts, left(error, 240) AS error, created_at, updated_at FROM receipts
                WHERE {AWAITING_RELEASE} OR (status = 'failed' AND updated_at > now() - interval '3 days') OR (status = 'pending' AND updated_at < now() - interval '15 minutes')
                ORDER BY status = 'unknown' DESC, updated_at DESC, id DESC LIMIT 40"""
        ).fetchall()
        deliveries = conn.execute(
            """SELECT id, target_key, subject_kind, subject_id, status, attempts, left(response, 240) AS response, created_at, updated_at FROM deliveries
               WHERE status IN ('unknown', 'failed') OR (status = 'sending' AND updated_at < now() - interval '15 minutes')
               ORDER BY status = 'unknown' DESC, updated_at DESC, id DESC LIMIT 40"""
        ).fetchall()
        errors = conn.execute(
            f"""SELECT {FAILURE_GROUP_SQL} AS error, count(*)::int AS n, max(discovered_at) AS last, (array_agg(id ORDER BY discovered_at DESC))[1] AS example
                FROM articles WHERE processing_state = 'failed' AND discovered_at > now() - interval '30 days' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20"""
        ).fetchall()
        ingest = conn.execute("SELECT client, kind, status, left(error, 200) AS error, summary, created_at FROM ingest_events ORDER BY created_at DESC, id DESC LIMIT 20").fetchall()
        grouping = waiting_selected_news(conn)
        retrying = conn.execute("SELECT count(*)::int AS n, min(processing_retry_at) AS next FROM articles WHERE processing_state = 'new' AND processing_attempts > 0").fetchone()
    now = datetime.now(UTC)
    now_ms = now.timestamp() * 1000
    return {
        "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        "processes": [
            {"role": h["key"][len("heartbeat.") :], **h["value"], "at": h["updated_at"], "alive": now_ms - h["updated_at"].timestamp() * 1000 < STALE_HEARTBEAT_MS}
            for h in heartbeats
        ],
        "jobs": latest, "timeline": timeline, "queues": queues, "failedJobs": failed_jobs, "grouping": grouping_overview(grouping, now_ms), "lagging": lagging,
        "receipts": {"counts": {r["status"]: r["n"] for r in receipts}, "issues": receipt_issues}, "deliveries": deliveries, "errors": errors,
        "retrying": {"count": retrying["n"] if retrying else 0, "next": retrying["next"] if retrying else None}, "ingest": ingest,
    }  # fmt: skip


def requeue_failed(pool, group: str | None) -> dict:
    """Failed articles of the last 30 days back into processing, all or one failure group."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"""UPDATE articles SET processing_state = 'new', processing_attempts = 0, processing_retry_at = NULL, processing_error = NULL
                WHERE processing_state = 'failed' AND discovered_at > now() - interval '30 days'
                  AND (%(g)s::text IS NULL OR {FAILURE_GROUP_SQL} = %(g)s)
                RETURNING id""",
            {"g": group},
        ).fetchall()
    for r in rows[:500]:
        queue_processing(pool, r["id"])
    return {"requeued": len(rows)}


def requeue_failed_articles(pool, inp: dict, actor: str) -> dict:
    if not (inp.get("reason") or "").strip():
        raise RuntimeError("reason is required")
    result = requeue_failed(pool, inp.get("group"))
    with pool.connection() as conn:
        group = inp.get("group")
        audit(conn, actor, "processing.requeue", f"failure:{group[:80]}" if group else "failure:all", inp["reason"], None, result)
    return result
