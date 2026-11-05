"""Collection jobs: per-source fetch runs, body extraction before analysis, the schedule that queues due sources, the daily pace adaptation and the sweep
of articles waiting for processing. Port of jobs/sources.ts and the scheduling half of sources/collect.ts, jobs/content.ts."""

from __future__ import annotations

import math

from newsroom.engine.extract import extract_article_body
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.sources.collect import SHUTDOWN, collect_source, queue_processing
from newsroom.engine.sources.icons import refresh_source_icons

MAX_EXTRACT_FAILURES = 3  # extraction gives up after this many errors and the article is judged on what it has
QUEUED_STALE = "30 minutes"  # an article queued this long ago is queued again, in case its job was lost
PAID_LISTING_FLOOR = 60  # minutes: listings read through Jina are not looked at more often
X_KINDS = "('rss', 'web_list', 'json_list', 'x_search')"

# The longest a source may wait between reads for a use's sake, by use name, as the installed modules declare them (none are installed here).
USE_PACES: dict[str, float] = {}


def use_pace(uses) -> float:
    """The longest a source may wait between reads for its uses' sake; infinity when none asks."""
    return min([math.inf, *[USE_PACES.get(u, math.inf) for u in uses]])


_PAID_LISTING = "coalesce(config->>'url', '') LIKE 'https://r.jina.ai/%'"


def schedule_due_sources(pool) -> dict:
    """Every minute: enqueue due sources (enabled, not WeChat/external), oldest due first. X accounts read by shard are not scheduled here yet."""
    enqueued = 0
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT id FROM sources WHERE enabled AND kind IN ('rss', 'web_list', 'json_list') AND (next_fetch_at IS NULL OR next_fetch_at <= now())
               ORDER BY next_fetch_at NULLS FIRST LIMIT 40"""
        ).fetchall()
        for r in rows:
            if enqueue(conn, QUEUES["fetchSource"], {"sourceId": r["id"]}, singleton_key=r["id"]) is not None:
                enqueued += 1
            conn.execute("UPDATE sources SET next_fetch_at = now() + interval '10 minutes' WHERE id = %s", (r["id"],))
    return {"enqueued": enqueued, "shards": 0}


def adapt_intervals(pool) -> dict:
    """Daily: adapt each source's interval to its recent output (active 15 min … quiet 120 min). hot_signal sources are allowed to be slower; the
    source's other uses may ask for it sooner."""
    updated = 0
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT s.id, s.participation_mode, s.uses, s.kind, s.config, s.cursor, {_PAID_LISTING} AS paid_listing,
                  (SELECT count(*) FROM articles a WHERE a.source_id = s.id AND a.discovered_at > now() - interval '7 days' AND NOT a.backfill) / 7.0 AS per_day
                FROM sources s WHERE s.enabled AND s.kind IN {X_KINDS}"""
        ).fetchall()
        for r in rows:
            per_day = float(r["per_day"])
            # Editorial sites and feeds are looked at hourly at least; editorial X and listings read through Jina stop at two hours; hot signals may wait longer.
            top = 180 if r["participation_mode"] == "hot_signal" else 120 if r["kind"] == "x_search" or r["paid_listing"] else 60
            low = PAID_LISTING_FLOOR if r["paid_listing"] else 15
            own = top if per_day <= 0.15 else math.floor(min(top, max(low, (24 * 60) / (per_day * 3))) + 0.5)
            # Another use may want the source sooner, never later, and a paid listing still waits its hour.
            target = int(max(low, min(own, use_pace(r["uses"]))))
            updated += conn.execute("UPDATE sources SET interval_minutes = %s WHERE id = %s AND interval_minutes <> %s", (target, r["id"], target)).rowcount
    return {"updated": updated}


def apply_use_pace(pool, source_id: str) -> None:
    """A source's uses just changed: from now it waits no longer than they allow, and no longer than that for its next read."""
    with pool.connection() as conn:
        s = conn.execute(
            f"SELECT uses, interval_minutes, {_PAID_LISTING} AS paid_listing FROM sources WHERE id = %s AND kind IN {X_KINDS}", (source_id,)
        ).fetchone()
        if not s:
            return
        capped = min(s["interval_minutes"], use_pace(s["uses"]))
        target = int(max(PAID_LISTING_FLOOR, capped) if s["paid_listing"] else capped)
        conn.execute(
            "UPDATE sources SET interval_minutes = %(t)s, next_fetch_at = LEAST(coalesce(next_fetch_at, now()), now() + make_interval(mins => %(t)s)) WHERE id = %(id)s",
            {"t": target, "id": source_id},
        )


def sweep_unprocessed(pool) -> dict:
    """The retry timer (content.sweep): articles waiting for processing whose retry time has come, whose job was never created, or whose job has waited
    half an hour. Returns the jobs actually created."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT id FROM articles WHERE processing_state = 'new' AND created_at < now() - interval '3 minutes'
                  AND (processing_retry_at IS NULL OR processing_retry_at <= now())
                  AND (processing_queued_at IS NULL OR processing_queued_at < now() - interval '{QUEUED_STALE}')
                ORDER BY discovered_at DESC LIMIT 500"""
        ).fetchall()
    enqueued = 0
    for r in rows:
        if queue_processing(pool, r["id"]):
            enqueued += 1
    return {"enqueued": enqueued}


def run_extraction(pool, article_id: str) -> dict:
    """The body extraction job. Failures are retried a few times; after that the article is judged on the excerpt it has ("unconfirmed" body, never a
    wrong one)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT revision FROM articles WHERE id = %s", (article_id,)).fetchone()
    if not row:
        return {"state": "missing"}
    revision = row["revision"]
    try:
        state = extract_article_body(pool, article_id)
        # Extraction's failures do not count against the analysis that follows (a new revision starts over anyway).
        if state != "skipped":
            with pool.connection() as conn:
                conn.execute("UPDATE articles SET processing_attempts = 0, processing_retry_at = NULL WHERE id = %s AND revision = %s", (article_id, revision))
        queue_processing(pool, article_id)  # a newer revision may still need a body after this task's result was discarded
        return {"state": state}
    except NotImplementedError:
        raise
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        message = str(error)[:500]
        with pool.connection() as conn:
            a = conn.execute(
                """UPDATE articles SET processing_attempts = processing_attempts + 1, processing_error = %s, processing_queued_at = NULL,
                     processing_retry_at = now() + interval '10 minutes' WHERE id = %s AND revision = %s AND body_status <> 'ok' RETURNING processing_attempts""",
                (f"extract: {message}", article_id, revision),
            ).fetchone()
            if not a:
                queue_processing(conn, article_id)
                return {"state": "skipped"}
            if a["processing_attempts"] < MAX_EXTRACT_FAILURES:
                return {"state": "retrying"}
            conn.execute(
                """UPDATE articles SET body_status = 'unconfirmed', processing_attempts = 0, processing_retry_at = NULL
                   WHERE id = %s AND revision = %s AND body_status = 'pending'""",
                (article_id, revision),
            )
            queue_processing(conn, article_id)
            return {"state": "unconfirmed"}


def register_source_jobs(worker) -> None:
    """The handlers of the collection queues on a Worker, and the timers that keep them fed (source timers only with COLLECT_ENABLED=true, as in the original)."""
    import os

    pool = worker.pool
    worker.work(QUEUES["fetchSource"], lambda data: collect_source(pool, data["sourceId"], bool(data.get("force"))), local_concurrency=8)
    worker.work(QUEUES["extractBody"], lambda data: run_extraction(pool, data["articleId"]), local_concurrency=4)
    if os.environ.get("COLLECT_ENABLED") == "true":
        worker.schedule("sources.schedule", "* * * * *", lambda: schedule_due_sources(pool))
        worker.schedule("sources.adapt-intervals", "20 4 * * *", lambda: adapt_intervals(pool))
        # Icons are read from the sources' own sites, so they stop with collection.
        worker.schedule("sources.icons", "40 4 * * *", lambda: refresh_source_icons(pool), missed="once")


def register_processing_jobs(worker) -> None:
    """The analysis queue, the sweep that queues articles waiting for their turn, and the hot-ranking timers."""
    from newsroom.engine.events.consolidate import link_related_stories
    from newsroom.engine.events.hot import compute_hot_ranking, snapshot_heat
    from newsroom.engine.jobs.content import process_article
    from newsroom.engine.jobs.events import register_event_jobs
    from newsroom.engine.jobs.publication import run_republish
    from newsroom.engine.notify_selected import run_notify_selected
    from newsroom.engine.operations import daily_retention, forward_pending_feedback, recover_stale_work
    from newsroom.engine.operations_alerts import check_alerts, send_digest
    from newsroom.engine.operations_backup import backup_configured, run_backup
    from newsroom.engine.operations_reports import source_health_weekly
    from newsroom.engine.reports_compose import compose_due_reports
    from newsroom.engine.translate import translate_pending

    pool = worker.pool

    worker.work(QUEUES["analyze"], lambda data: process_article(pool, data["articleId"], data.get("attemptTag")), local_concurrency=6)
    register_event_jobs(worker)
    worker.work(QUEUES["republishSource"], lambda data: run_republish(pool, data["sourceId"]), local_concurrency=1)
    worker.work(QUEUES["prepareMedia"], lambda data: run_prepare_media(pool, data), local_concurrency=1)
    worker.schedule("content.sweep", "*/5 * * * *", lambda: sweep_unprocessed(pool))
    # Full-text translations of newly selected items (model calls; nothing happens while they are switched off).
    worker.schedule("content.translate", "*/5 * * * *", lambda: translate_pending(pool))
    # Every issue that is due and not written yet, each from its edition time at the next half hour; a missed or failed one at the next run.
    worker.schedule("reports.compose", "0,30 * * * *", lambda: compose_due_reports(pool), missed="once")
    # The deletions the privacy notice promises, once their retention periods are over; work a stopped process left half way becomes visible, and unknown paid
    # requests get their one automatic release.
    worker.schedule("ops.retention", "30 3 * * *", lambda: daily_retention(pool), missed="once")
    # Operations: alerts every ten minutes, the 09:00 follow-up list, the nightly backup where an object store is set up, and Monday's source-health report.
    worker.schedule("ops.alerts", "*/10 * * * *", lambda: check_alerts(pool))
    worker.schedule("ops.digest", "0 9 * * *", lambda: send_digest(pool), missed="once")
    if backup_configured():
        worker.schedule("ops.backup", "10 4 * * *", lambda: run_backup(pool), missed="once")
    worker.schedule("reports.source-health", "0 9 * * 1", lambda: source_health_weekly(pool), missed="once")
    worker.schedule("feedback.forward", "*/10 * * * *", lambda: forward_pending_feedback(pool))
    worker.work(QUEUES["notifySelected"], lambda data: run_notify_selected(pool, data), local_concurrency=1, polling_interval=5)
    worker.schedule("ops.recover", "*/10 * * * *", lambda: recover_stale_work(pool))
    worker.schedule("stories.links", "12 * * * *", lambda: link_related_stories(pool))
    worker.schedule("hot.rank", "*/5 * * * *", lambda: compute_hot_ranking(pool))
    worker.schedule("hot.snapshot", "2 * * * *", lambda: snapshot_heat(pool))


def run_prepare_media(pool, data: dict) -> dict:
    """The media queue: an article's images, or one rendition's animation or AVIF conversion (jobs/notify.ts)."""
    from newsroom.engine.media.images import convert_animated, convert_avif
    from newsroom.engine.media.prepare import prepare_article_media

    if "articleId" in data:
        return prepare_article_media(pool, data["articleId"])
    if data.get("format") == "avif":
        return {"avifSaved": convert_avif(data["url"], data["mode"])}
    return {"animatedSaved": convert_animated(data["url"], data["mode"])}
