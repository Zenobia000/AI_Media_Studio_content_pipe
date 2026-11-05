"""Event jobs: serial grouping, debounced digests. Port of jobs/events.ts."""

from __future__ import annotations

from newsroom.engine.events.digest import compose_story_digest
from newsroom.engine.events.group import group_article
from newsroom.engine.jobs.content import settle_non_editorial
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.materials import NEWS_CONDITION, is_historical
from newsroom.engine.sources.collect import grouping_priority


def run_group(pool, data: dict) -> dict:
    article_id, signal_only, regroup = data["articleId"], data.get("signalOnly"), data.get("regroup")
    # A discussion post comes here straight from collection: record it first (settle_non_editorial).
    if signal_only and not settle_non_editorial(pool, article_id)["group"]:
        return {"verdict": "skipped"}
    result = group_article(pool, article_id, signal_only=bool(signal_only), regroup=bool(regroup))
    if result.get("storyId") and not result["verdict"].startswith("signal"):
        enqueue(pool, QUEUES["digest"], {"storyId": result["storyId"]}, singleton_key=f"story:{result['storyId']}", start_after=60)
    return result


def register_event_jobs(worker) -> None:
    pool = worker.pool
    # Serial on purpose: two reports of the same new fact must not both create it.
    worker.work(QUEUES["group"], lambda data: run_group(pool, data), local_concurrency=1, polling_interval=0.5)
    worker.work(QUEUES["digest"], lambda data: compose_story_digest(pool, data["storyId"]), local_concurrency=3, polling_interval=5)


def sweep_ungrouped(pool) -> dict:
    """Material that needs grouping (a passed report, a discussion post while it is news) left pending without a job: a lost job, or a wait group_article leaves
    pending (budget, provider outage, shutdown) that outlasted the job's retries. Terminal failures need a receipt release or an explicit rerun."""
    enqueued = 0
    with pool.connection() as conn, conn.transaction():
        rows = conn.execute(
            f"""SELECT a.id, s.participation_mode, a.backfill, a.published_at, a.discovered_at FROM articles a JOIN sources s ON s.id = a.source_id
                WHERE a.grouping_status = 'pending' AND a.created_at < now() - interval '3 minutes'
                  AND (s.participation_mode = 'editorial' AND a.processing_state = 'analyzed' AND EXISTS (
                        SELECT 1 FROM analyses an WHERE an.article_id = a.id AND an.input_revision = a.revision AND an.relevance = 'pass')
                    OR s.participation_mode = 'hot_signal' AND a.processing_state = 'skipped' AND {NEWS_CONDITION})
                  AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.name = %s AND j.data->>'articleId' = a.id AND j.state IN ('created', 'active', 'retry'))
                ORDER BY a.created_at LIMIT 100 FOR UPDATE OF a SKIP LOCKED""",
            (QUEUES["group"],),
        ).fetchall()
        for row in rows:
            signal = row["participation_mode"] != "editorial"
            priority = grouping_priority(signal, is_historical(row["published_at"], row["discovered_at"], row["backfill"]))
            data = {"articleId": row["id"], "signalOnly": True} if signal else {"articleId": row["id"]}
            if enqueue(conn, QUEUES["group"], data, singleton_key=row["id"], priority=priority):
                enqueued += 1
    return {"enqueued": enqueued}
