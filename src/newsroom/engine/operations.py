"""Housekeeping and recovery. Port of operations/retention.ts and operations/recover.ts.

Retention (daily): expired leases, old run history, feedback screenshots left behind and derived caches. Paid receipts and their raw responses are kept: they stop a
request being paid for twice and are the record for reviewing a result.

Recovery: a stopped process leaves "pending" receipts and deliveries: they become "unknown" and are never re-sent on their own. An unknown receipt is released once
automatically after half an hour without checking the provider's bill, so a lost answer costs at most one repeat; one that turns unknown again waits for an authorized
manual release with the actual billing state and a note. A released receipt lets its request call again, and an article that stopped on it goes back to processing."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from newsroom.engine.audit import Conflict, audit
from newsroom.engine.config import config, credential
from newsroom.engine.jobs.events import sweep_ungrouped
from newsroom.engine.notify_deliver import mark_stale_deliveries
from newsroom.engine.operations_feedback import _expire_bans
from newsroom.engine.receipts import mark_stale_pending_receipts, release_unknown_receipt
from newsroom.engine.sources.collect import queue_processing

AUTO_RELEASE_AFTER = timedelta(minutes=30)
AUTO_RELEASE_NOTE = "自動放行：結果未知超過 30 分鐘，未核對是否計費"
# The analysis steps whose receipts name an article (jobs/content.ts).
ARTICLE_STEPS = {"prefilter_article", "score_article", "structure_article", "understand_article", "summarize_article", "mend_wording"}


def feishu_internal_enabled() -> bool:
    return os.environ.get("FEISHU_INTERNAL_ENABLED") == "true"


def screenshots_forwarded() -> bool:
    """Feedback screenshots only wait to be forwarded when the internal chat exists; without it they are the screenshots themselves and stay."""
    return feishu_internal_enabled() and credential("integrations", "FEISHU_INTERNAL_CHAT_ID") is not None


def _remove_older_than(directory: Path, max_age_seconds: float, now: float) -> int:
    """Removes files under `directory` last written more than `max_age_seconds` ago."""
    removed = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = Path(root) / name
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def daily_retention(pool, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        leases = conn.execute("DELETE FROM delivery_leases WHERE expires_at < %s", (now,)).rowcount
    with pool.connection() as conn, conn.transaction():
        bans = _expire_bans(conn, now)
    with pool.connection() as conn:
        state = conn.execute("DELETE FROM feedback_submission_state WHERE expires_at <= %s", (now,)).rowcount
        # Scheduled-task history: 30 days (failures 90) is enough for the runs view.
        runs = conn.execute(
            "DELETE FROM job_runs WHERE started_at < %s AND (status IS DISTINCT FROM 'failed' OR started_at < %s)",
            (now - timedelta(days=30), now - timedelta(days=90)),
        ).rowcount
    month = 30 * 86400
    data = config().data_dir
    # Feedback screenshots that only wait to be forwarded (the database then keeps just the Feishu image key). The forwarding sweep gives up after a week; anything
    # older is removed, whether or not forwarding ever ran for it.
    screenshots = _remove_older_than(data / "feedback-screenshots", 8 * 86400, now.timestamp()) if screenshots_forwarded() else 0
    # Derived caches (proxied images, share cards and posters) are rebuilt on demand.
    cache = _remove_older_than(data / "imgcache", month, now.timestamp()) + _remove_older_than(data / "ogcache", month, now.timestamp())
    return {
        "deletedLeases": leases,
        "expiredFeedbackBans": bans,
        "expiredFeedbackState": state,
        "deletedJobRuns": runs,
        "deletedScreenshots": screenshots,
        "prunedCache": cache,
    }


def resume_after_release(conn, receipt: dict) -> bool:
    """After a receipt is released, the article that stopped on it goes straight back to processing: one action, not two. Returns whether it was queued."""
    m = re.match(r"article:([^@:#]+)", receipt.get("subject") or "") if receipt["purpose"] in ARTICLE_STEPS else None
    if not m:
        return False
    row = conn.execute(
        """UPDATE articles SET processing_state = 'new', processing_attempts = 0, processing_retry_at = NULL, processing_error = NULL
           WHERE id = %s AND processing_state = 'failed' RETURNING id""",
        (m.group(1),),
    ).fetchone()
    return bool(row) and bool(queue_processing(conn, m.group(1), step="analyze"))


def _release(pool, receipt_id: int, error: str, actor: str, note: str, billed: bool | None) -> dict | None:
    with pool.connection() as conn, conn.transaction():
        receipt = release_unknown_receipt(conn, receipt_id, error)
        if not receipt:
            return None
        requeued = resume_after_release(conn, receipt)
        audit(
            conn,
            actor,
            "receipt.release",
            f"receipt:{receipt_id}",
            note,
            {"status": "unknown"},
            {"status": "failed", "billed": billed, "requeued": requeued},
        )
        return {"id": receipt_id, "status": "failed", "subject": receipt["subject"], "purpose": receipt["purpose"], "requeued": requeued}


def release_receipt(pool, receipt_id: int, inp: dict, actor: str) -> dict | None:
    """Record verified billing, or explicitly unknown billing for an authorized manual recovery."""
    note = inp.get("note")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("note is required")
    billed = inp.get("billed")
    if billed is not None and not isinstance(billed, bool):
        raise ValueError("billed must be true, false or null")
    with pool.connection() as conn:
        row = conn.execute("SELECT status FROM receipts WHERE id = %s", (receipt_id,)).fetchone()
    if not row:
        return None
    if row["status"] != "unknown":
        raise Conflict("只有結果未知的回執需要人工核對")
    if billed is None:
        error = f"人工放行：計費未知，按授權恢復。{note}"
    else:
        error = f"人工核對：{'供應商已計費但結果未取回' if billed else '供應商未計費'}。{note}"
    return _release(pool, receipt_id, error, actor, note, billed)


def auto_release_unknown_receipts(pool, now: datetime | None = None) -> dict:
    """Unknown receipts older than half an hour, released without checking the provider's bill. A request released this way once and unknown again stays for the
    admin. Primaries continued by a backup are not awaiting release: that would revive their old request."""
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT r.id FROM receipts r
               WHERE (r.status = 'unknown' AND r.request->>'fallbackReceiptId' IS NULL) AND r.updated_at < %s
                 AND NOT EXISTS (SELECT 1 FROM receipt_attempts a WHERE a.receipt_id = r.id AND a.error LIKE %s)
               ORDER BY r.id LIMIT 200""",
            (now - AUTO_RELEASE_AFTER, AUTO_RELEASE_NOTE + "%"),
        ).fetchall()
    released = requeued = 0
    for r in rows:
        done = _release(pool, r["id"], AUTO_RELEASE_NOTE, "ops.recover", "結果未知，自動放行一次", None)
        if done:
            released += 1
        if done and done["requeued"]:
            requeued += 1
    return {"released": released, "requeued": requeued}


def retry_released_receipt_jobs(pool) -> int:
    """Jobs that failed on a receipt that has since been released go back to the queue. Reading the durable audit also recovers a crash between the release and
    this sweep."""
    with pool.connection() as conn, conn.transaction():
        jobs = conn.execute(
            """SELECT j.id FROM jobs j
               WHERE j.state = 'failed'
                 AND EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'receipt.release'
                             AND a.subject = 'receipt:' || (j.output->>'receiptId') AND a.created_at > j.started_on)
               ORDER BY j.completed_on LIMIT 200 FOR UPDATE OF j SKIP LOCKED"""
        ).fetchall()
        for job in jobs:
            conn.execute("UPDATE jobs SET state = 'retry', start_after = now(), completed_on = NULL WHERE id = %s AND state = 'failed'", (job["id"],))
        return len(jobs)


def recover_stale_work(pool) -> dict:
    """ops.recover, every 10 minutes."""
    return {
        "receipts": mark_stale_pending_receipts(pool), "released": auto_release_unknown_receipts(pool), "jobs": retry_released_receipt_jobs(pool),
        "grouping": sweep_ungrouped(pool), "deliveries": mark_stale_deliveries(pool),
    }  # fmt: skip


def forward_pending_feedback(pool) -> dict:
    """Every few minutes: feedback that did not reach the internal chat (Feishu down, a screenshot upload failing) is tried again for a week. Newer than a few minutes is
    still being sent by its submission."""
    from newsroom.engine.notify_feishu import forward_feedback_to_feishu

    if not feishu_internal_enabled():
        return {"sent": 0, "failed": 0}
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT id FROM feedback WHERE forwarded_at IS NULL AND forward_error IS NOT NULL
               AND created_at < now() - interval '5 minutes' AND created_at > now() - interval '7 days' ORDER BY id LIMIT 20"""
        ).fetchall()
    sent = failed = 0
    for r in rows:
        try:
            if forward_feedback_to_feishu(pool, r["id"]) == "sent":
                sent += 1
        except Exception:
            failed += 1
    return {"sent": sent, "failed": failed}
