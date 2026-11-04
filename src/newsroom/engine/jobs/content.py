"""Content processing: body extraction when the source needs it → analysis → publish → event grouping. A failed step records when to try again, and the sweep is the
timer that queues it then. A provider outage makes an article wait and retry with backoff; only a permanent refusal or exhausted retries end in "failed", which the
admin re-queues in bulk. Port of the analysis job of jobs/content.ts (the extraction job and the sweep are in jobs/sources.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from newsroom.engine.analyze import analyze_article
from newsroom.engine.budget import BudgetExceededError
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.materials import is_historical
from newsroom.engine.publication.publish import publish_article
from newsroom.engine.receipts import failed_receipt_id, failure_kind
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.sources.collect import grouping_priority, queue_processing

RETRY_MINUTES = [5, 10, 20, 40, 60, 120, 240, 360]  # minutes to wait after the n-th failed attempt; one more failure after the last ends in "failed"
MAX_OUTPUT_FAILURES = 3  # unusable model output is retried less: each retry is a paid call


def needs_grouping(participation_mode: str, historical: bool, relevance: str | None = None) -> bool:
    """Whether processed material goes on to event grouping: an editorial report its analysis passed (history too), or a discussion post while it is news."""
    return relevance == "pass" if participation_mode == "editorial" else participation_mode == "hot_signal" and not historical


def settle_non_editorial(pool, article_id: str) -> dict:
    """A post of a non-editorial source: recorded (hot_signal material only feeds heat; isolated material never reaches public surfaces). Returns whether it is
    discussion evidence to group."""
    with pool.connection() as conn:
        row = conn.execute(
            """UPDATE articles a SET processing_state = 'skipped', processing_attempts = 0, processing_retry_at = NULL, processing_queued_at = NULL
               FROM sources s WHERE s.id = a.source_id AND a.id = %s AND s.participation_mode <> 'editorial'
               RETURNING s.participation_mode, a.backfill, a.published_at, a.discovered_at""",
            (article_id,),
        ).fetchone()
    if not row:
        return {"group": False}
    publish_article(pool, article_id)
    return {"group": needs_grouping(row["participation_mode"], is_historical(row["published_at"], row["discovered_at"], row["backfill"]))}


def _processing_input(pool, article_id: str) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT s.participation_mode, a.revision, a.backfill, a.published_at, a.discovered_at FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s""",
            (article_id,),
        ).fetchone()
    return {**row, "historical": is_historical(row["published_at"], row["discovered_at"], row["backfill"])} if row else None


def process_article(pool, article_id: str, attempt_tag: str | None = None) -> dict:
    """The analysis job: the article's current revision through analysis and publication; failures wait, retry or end in "failed". `attempt_tag` makes an explicit
    re-evaluation a new (paid) request; the same tag reuses its receipt."""
    row = _processing_input(pool, article_id)
    if not row:
        return {"state": "missing"}
    try:
        return _process_revision(pool, article_id, row, attempt_tag)
    except Exception as error:
        return _after_failure(pool, article_id, row["revision"], error)


def _process_revision(pool, article_id: str, row: dict, attempt_tag: str | None) -> dict:
    if row["participation_mode"] != "editorial":
        # Normally queued straight for grouping (queue_processing); an explicit re-evaluation lands here.
        if settle_non_editorial(pool, article_id)["group"]:
            with pool.connection() as conn:
                enqueue(conn, QUEUES["group"], {"articleId": article_id, "signalOnly": True}, singleton_key=article_id, priority=grouping_priority(True, row["historical"]))
        return {"state": "skipped"}
    try:
        result = analyze_article(pool, article_id, {"attemptTag": attempt_tag})
        if not result:
            return {"state": "missing"}
        # Only a title or a feed summary: the article page first; extraction queues the analysis again.
        if result.get("needsBody") or not result["output"]:
            queue_processing(pool, article_id, step="extract")
            return {"state": "fetching-body"}
        if result["stale"]:
            return {"state": "stale"}  # the newer revision has its own job
        publish_article(pool, article_id)
        if needs_grouping(row["participation_mode"], row["historical"], result["output"]["relevance"]):
            with pool.connection() as conn:
                enqueue(conn, QUEUES["group"], {"articleId": article_id}, singleton_key=article_id, priority=grouping_priority(False, row["historical"]))
        with pool.connection() as conn:
            conn.execute(
                "UPDATE articles SET processing_attempts = 0, processing_retry_at = NULL, processing_queued_at = NULL WHERE id = %s AND revision = %s", (article_id, row["revision"])
            )
        return {"state": result["output"]["relevance"]}
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        if failure_kind(error) == "unknown":
            # The provider may have billed this request: stop; ops.recover releases it once and requeues the article.
            with pool.connection() as conn:
                conn.execute(
                    "UPDATE articles SET processing_state = 'failed', processing_error = %s WHERE id = %s AND revision = %s",
                    (f"receipt {failed_receipt_id(error)} outcome unknown", article_id, row["revision"]),
                )
            return {"state": "unknown-receipt"}
        raise


def _after_failure(pool, article_id: str, revision: int, error: Exception) -> dict:
    """Waits and retries for passing trouble; marks "failed" for refusals and exhausted retries."""
    # Let the queue retry this job after restart, reusing settled receipts. A deploy is not an article failure.
    if SHUTDOWN.is_set():
        raise error
    message = str(error)[:500]
    kind = failure_kind(error)
    with pool.connection() as conn:
        if kind in ("busy", "budget"):
            # Not the article's fault: the same request is in flight, or the budget window is full.
            seconds = error.retry_after_seconds if isinstance(error, BudgetExceededError) else 60
            retry_at = datetime.now(UTC) + timedelta(seconds=seconds)
            conn.execute(
                "UPDATE articles SET processing_state = 'new', processing_error = %s, processing_retry_at = %s, processing_queued_at = NULL WHERE id = %s AND revision = %s",
                (message, retry_at, article_id, revision),
            )
            return {"state": "waiting", "retryAt": retry_at}
        a = conn.execute("SELECT processing_attempts FROM articles WHERE id = %s AND revision = %s", (article_id, revision)).fetchone()
        if not a:
            return {"state": "stale"}
        attempts = a["processing_attempts"] + 1
        refused = kind == "refused"
        exhausted = attempts > len(RETRY_MINUTES) or (kind == "unusable" and attempts >= MAX_OUTPUT_FAILURES)
        if refused or exhausted:
            conn.execute(
                """UPDATE articles SET processing_state = 'failed', processing_error = %s, processing_attempts = %s, processing_retry_at = NULL, processing_queued_at = NULL
                   WHERE id = %s AND revision = %s""",
                (message, attempts, article_id, revision),
            )
            return {"state": "failed"}
        retry_at = datetime.now(UTC) + timedelta(minutes=RETRY_MINUTES[attempts - 1])
        conn.execute(
            """UPDATE articles SET processing_state = 'new', processing_error = %s, processing_attempts = %s, processing_retry_at = %s, processing_queued_at = NULL
               WHERE id = %s AND revision = %s""",
            (message, attempts, retry_at, article_id, revision),
        )
        return {"state": "retrying", "retryAt": retry_at}
