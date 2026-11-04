"""Job queue on PostgreSQL, replacing pg-boss (jobs/queue.ts). Every queue is declared here with its retry policy: business code enqueues by
name, the worker registers one handler per queue, and a handler gets each job's data.

What the engine relied on, kept: a job enqueued with a connection commits with the caller's transaction; the "short" policy queues one job
per queue and singleton key (a second one is dropped, enqueue returns None); a failed attempt waits retry_delay seconds (doubling with
backoff) and is tried again until retry_limit; an attempt that runs past expire_seconds is failed by the maintenance sweep; higher priority
is served first; a graceful stop lets running jobs finish."""

from __future__ import annotations

import logging
import random
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

log = logging.getLogger("newsroom.jobs")

QUEUES = {
    "analyze": "content.analyze", "extractBody": "content.extract-body", "group": "events.group", "digest": "events.digest",
    "fetchSource": "sources.fetch", "fetchXShard": "sources.fetch-x", "mpCheck": "sources.mp", "notifySelected": "notify.selected",
    "republishSource": "publication.republish-source", "prepareMedia": "media.prepare",
}  # fmt: skip

SHORT = "short"
QUEUE_OPTIONS: dict[str, dict] = {
    QUEUES["analyze"]: {"policy": SHORT, "retryLimit": 4, "retryDelay": 30, "retryBackoff": True, "expireInSeconds": 600},
    QUEUES["extractBody"]: {"policy": SHORT, "retryLimit": 2, "retryDelay": 120, "expireInSeconds": 300},
    QUEUES["group"]: {"policy": SHORT, "retryLimit": 4, "retryDelay": 20, "retryBackoff": True, "expireInSeconds": 600},
    QUEUES["digest"]: {"policy": SHORT, "retryLimit": 3, "retryDelay": 60, "retryBackoff": True, "expireInSeconds": 900},
    QUEUES["fetchSource"]: {"policy": SHORT, "retryLimit": 0, "expireInSeconds": 600},
    QUEUES["fetchXShard"]: {"policy": SHORT, "retryLimit": 0, "expireInSeconds": 900},
    QUEUES["mpCheck"]: {"policy": SHORT, "retryLimit": 3, "retryDelay": 60, "retryBackoff": True, "expireInSeconds": 600},
    QUEUES["notifySelected"]: {"policy": SHORT, "retryLimit": 0, "expireInSeconds": 300},
    QUEUES["republishSource"]: {"policy": SHORT, "retryLimit": 2, "retryDelay": 60, "expireInSeconds": 3600},
    QUEUES["prepareMedia"]: {"policy": SHORT, "retryLimit": 1, "retryDelay": 120, "expireInSeconds": 600},
}  # fmt: skip
CRON_OPTIONS = {"policy": "singleton", "retryLimit": 1, "expireInSeconds": 3600}
DEFAULT_OPTIONS = {"policy": SHORT, "retryLimit": 0, "retryDelay": 0, "retryBackoff": False, "expireInSeconds": 900}
COMPLETED_KEPT = timedelta(days=7)
FAILED_KEPT = timedelta(days=14)


def _options(name: str, override: dict | None = None) -> dict:
    return {**DEFAULT_OPTIONS, **(QUEUE_OPTIONS.get(name) or (CRON_OPTIONS if name.startswith("cron.") else {})), **(override or {})}


class _Use:
    """A connection given by the caller (kept open, in its transaction) or one borrowed from a pool for the call."""

    def __init__(self, db) -> None:
        self.db, self.borrowed = db, None

    def __enter__(self):
        if hasattr(self.db, "execute"):
            return self.db
        self.borrowed = self.db.connection()
        return self.borrowed.__enter__()

    def __exit__(self, *exc):
        if self.borrowed:
            return self.borrowed.__exit__(*exc)
        return None


def enqueue(db, name: str, data: dict, *, singleton_key: str | None = None, start_after: float | datetime | None = None, priority: int = 0,
            options: dict | None = None) -> str | None:  # fmt: skip
    """Enqueues a job; with a connection it commits atomically with the caller's business write. Returns its id, or None when the queue's
    policy dropped it because an equal one is already waiting. `start_after` is seconds from now or a time."""
    o = _options(name, options)
    after = datetime.now(UTC) + timedelta(seconds=start_after) if isinstance(start_after, (int, float)) else (start_after or datetime.now(UTC))
    with _Use(db) as conn:
        row = conn.execute(
            """INSERT INTO jobs (name, policy, data, priority, singleton_key, start_after, retry_limit, retry_delay, retry_backoff, expire_seconds)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
            (
                name,
                o["policy"],
                Jsonb(data),
                priority,
                singleton_key,
                after,
                o["retryLimit"],
                o["retryDelay"],
                o["retryBackoff"],
                o["expireInSeconds"],
            ),
        ).fetchone()
    return str(row["id"]) if row else None


def fetch(conn, name: str, batch: int = 1) -> list[dict]:
    """Takes up to `batch` waiting jobs of a queue (highest priority, then oldest) and marks them active; other workers skip the rows taken."""
    rows = conn.execute(
        """WITH next AS (
             SELECT j.id FROM jobs j WHERE j.name = %(name)s AND j.state IN ('created', 'retry') AND j.start_after <= now()
               AND NOT (j.policy = 'singleton' AND EXISTS (SELECT 1 FROM jobs a WHERE a.name = j.name AND a.state = 'active'
                                                            AND coalesce(a.singleton_key, '') = coalesce(j.singleton_key, '')))
             ORDER BY j.priority DESC, j.created_on, j.id LIMIT %(batch)s FOR UPDATE SKIP LOCKED)
           UPDATE jobs SET state = 'active', started_on = now() FROM next WHERE jobs.id = next.id
           RETURNING jobs.id, jobs.name, jobs.data, jobs.retry_count, jobs.retry_limit, jobs.priority, jobs.created_on""",
        {"name": name, "batch": batch},
    ).fetchall()
    # UPDATE ... RETURNING gives no order: serve the ones taken in the queue's own order.
    return sorted(rows, key=lambda r: (-r["priority"], r["created_on"], str(r["id"])))


def complete(conn, job_id, output=None) -> None:
    conn.execute(
        "UPDATE jobs SET state = 'completed', completed_on = now(), output = %s WHERE id = %s AND state = 'active'",
        (Jsonb(output) if output is not None else None, job_id),
    )


def fail(conn, job_id, error: str) -> str:
    """An attempt failed: waiting for another when retries are left (the delay doubles with backoff, with jitter), else failed for good.
    Returns the state it ended in."""
    row = conn.execute(
        """UPDATE jobs SET
             state = CASE WHEN retry_count < retry_limit THEN 'retry' ELSE 'failed' END,
             retry_count = CASE WHEN retry_count < retry_limit THEN retry_count + 1 ELSE retry_count END,
             start_after = CASE WHEN retry_count < retry_limit THEN now() + make_interval(secs =>
               CASE WHEN retry_backoff THEN retry_delay * (power(2, least(16, retry_count)) / 2) * (1 + %(jitter)s) ELSE retry_delay END) ELSE start_after END,
             completed_on = CASE WHEN retry_count < retry_limit THEN NULL ELSE now() END,
             output = CASE WHEN retry_count < retry_limit THEN output ELSE %(output)s END
           WHERE id = %(id)s AND state = 'active' RETURNING state""",
        {"id": job_id, "jitter": random.random(), "output": Jsonb({"message": error[:4000]})},
    ).fetchone()
    return row["state"] if row else "gone"


def retry(conn, job_id) -> bool:
    """A failed job goes back to the queue (a released receipt wakes jobs that used their retries up)."""
    return (
        conn.execute(
            "UPDATE jobs SET state = 'retry', start_after = now(), completed_on = NULL WHERE id = %s AND state = 'failed' RETURNING id", (job_id,)
        ).fetchone()
        is not None
    )


def maintain(conn, now: datetime | None = None) -> dict:
    """An attempt that ran past its expiry fails like any other; finished jobs are removed after their retention."""
    now = now or datetime.now(UTC)
    expired = conn.execute(
        "SELECT id FROM jobs WHERE state = 'active' AND started_on + make_interval(secs => expire_seconds) < %s FOR UPDATE SKIP LOCKED", (now,)
    ).fetchall()
    for row in expired:
        fail(conn, row["id"], "job expired")
    removed = conn.execute(
        "DELETE FROM jobs WHERE (state = 'completed' AND completed_on < %s) OR (state IN ('failed', 'cancelled') AND completed_on < %s)",
        (now - COMPLETED_KEPT, now - FAILED_KEPT),
    ).rowcount
    return {"expired": len(expired), "removed": removed}


def delete_queue(conn, name: str) -> None:
    """A retired timer leaves no queued work behind; job_runs keeps its audit."""
    conn.execute("DELETE FROM jobs WHERE name = %s", (name,))
    conn.execute("DELETE FROM job_schedules WHERE name = %s", (name[5:] if name.startswith("cron.") else name,))


def record_run(pool, job: str, fn: Callable[[], object]):
    """Scheduled task bookkeeping: every run leaves a row, so operators see the latest result."""
    with pool.connection() as conn:
        run_id = conn.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()["id"]
    try:
        result = fn()
    except BaseException as error:
        with pool.connection() as conn:
            conn.execute("UPDATE job_runs SET status = 'failed', finished_at = now(), error = %s WHERE id = %s", (str(error)[:4000], run_id))
        raise
    detail = result if isinstance(result, dict) else {"result": result}
    with pool.connection() as conn:
        conn.execute("UPDATE job_runs SET status = 'ok', finished_at = now(), detail = %s WHERE id = %s", (Jsonb(detail, dumps=_dumps), run_id))
    return result


def _dumps(value) -> str:
    import json  # noqa: PLC0415

    return json.dumps(value, ensure_ascii=False, default=str)


# ── workers and schedules ───────────────────────────────────────────────────────────────


class Worker:
    """Runs handlers for queues in threads of this process, and the maintenance and schedule ticks. `stop` lets running jobs finish."""

    def __init__(self, pool) -> None:
        self.pool = pool
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []
        self.running = 0
        self._lock = threading.Lock()
        self.handlers: list[tuple[str, Callable[[dict], object], int, float]] = []
        self.schedules: list[dict] = []

    def work(self, name: str, handler: Callable[[dict], object], local_concurrency: int = 1, polling_interval: float = 2.0) -> None:
        """One handler for a queue, called with each job's data; local_concurrency jobs of it run at once in this process."""
        self.handlers.append((name, handler, local_concurrency, polling_interval))

    def schedule(self, name: str, cron: str, run: Callable[[], object], missed: str = "skip", timezone: str = "Asia/Shanghai") -> None:
        """A cron timer; each run is recorded in job_runs. A slot missed while no worker ran is run once with missed="once", else skipped."""
        self.schedules.append({"name": name, "cron": cron, "run": run, "missed": missed, "timezone": timezone})
        self.work(f"cron.{name}", lambda _data, n=name, r=run: record_run(self.pool, n, r), 1, 15.0)

    def start(self) -> None:
        with self.pool.connection() as conn:
            for s in self.schedules:
                conn.execute(
                    """INSERT INTO job_schedules (name, cron, timezone, missed) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (name) DO UPDATE SET cron = EXCLUDED.cron, timezone = EXCLUDED.timezone, missed = EXCLUDED.missed, updated_at = now()""",
                    (s["name"], s["cron"], s["timezone"], s["missed"]),
                )
            current = {f"cron.{s['name']}" for s in self.schedules}
            for (old,) in [(r["name"],) for r in conn.execute("SELECT DISTINCT name FROM jobs WHERE name LIKE 'cron.%'").fetchall()]:
                if old not in current:
                    delete_queue(conn, old)
            for r in conn.execute("SELECT name FROM job_schedules").fetchall():
                if f"cron.{r['name']}" not in current:
                    delete_queue(conn, f"cron.{r['name']}")
        for name, handler, concurrency, interval in self.handlers:
            for slot in range(concurrency):
                self._spawn(f"{name}#{slot}", self._loop, name, handler, interval)
        self._spawn("maintenance", self._ticks)

    def _spawn(self, label: str, target, *args) -> None:
        t = threading.Thread(target=target, args=args, name=f"job-{label}", daemon=True)
        t.start()
        self.threads.append(t)

    def _loop(self, name: str, handler, interval: float) -> None:
        while not self.stopping.is_set():
            try:
                with self.pool.connection() as conn:
                    jobs = fetch(conn, name)
            except Exception:
                log.exception("fetching %s failed", name)
                self.stopping.wait(max(interval, 1.0))
                continue
            if not jobs:
                self.stopping.wait(interval)
                continue
            for job in jobs:
                with self._lock:
                    self.running += 1
                try:
                    self._run(job, handler)
                finally:
                    with self._lock:
                        self.running -= 1

    def _run(self, job: dict, handler) -> None:
        try:
            output = handler(job["data"])
        except BaseException as error:
            log.warning("job %s %s failed: %s", job["name"], job["id"], "".join(traceback.format_exception_only(type(error), error)).strip())
            with self.pool.connection() as conn:
                fail(conn, job["id"], str(error) or type(error).__name__)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return
        with self.pool.connection() as conn:
            complete(conn, job["id"], output if isinstance(output, (dict, list)) else None)

    def _ticks(self) -> None:
        last_maintenance = 0.0
        while not self.stopping.is_set():
            try:
                if time.monotonic() - last_maintenance >= 60:
                    with self.pool.connection() as conn:
                        maintain(conn)
                    last_maintenance = time.monotonic()
                self.fire_due_schedules()
            except Exception:
                log.exception("maintenance tick failed")
            self.stopping.wait(15)

    def fire_due_schedules(self, now: datetime | None = None) -> list[str]:
        """Queues each timer whose slot has come, once across all workers. Returns the timers fired."""
        from croniter import croniter  # noqa: PLC0415

        now = now or datetime.now(UTC)
        fired: list[str] = []
        for s in self.schedules:
            slot = croniter(s["cron"], now.astimezone(ZoneInfo(s["timezone"]))).get_prev(datetime).astimezone(UTC)
            with self.pool.connection() as conn:
                row = conn.execute(
                    """UPDATE job_schedules SET last_slot = %(slot)s WHERE name = %(name)s AND (last_slot IS NULL OR last_slot < %(slot)s)
                       RETURNING (SELECT last_slot FROM job_schedules WHERE name = %(name)s) AS before""",
                    {"slot": slot, "name": s["name"]},
                ).fetchone()
                if not row:
                    continue
                # A timer first seen starts from its next slot; a late one runs only when it asks to catch up.
                if row["before"] is None or (s["missed"] == "skip" and now - slot > timedelta(seconds=60)):
                    continue
                if enqueue(conn, f"cron.{s['name']}", {}, singleton_key=None) is not None:
                    fired.append(s["name"])
        return fired

    def stop(self, timeout: float = 470.0) -> bool:
        """Stops taking jobs and waits for the running ones to finish; returns whether they all did within the timeout."""
        self.stopping.set()
        deadline = time.monotonic() + timeout
        for t in self.threads:
            t.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in self.threads)
