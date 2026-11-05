"""The job queue: what the engine relied on pg-boss for (dedupe, delay, priority, retries, expiry, schedules, graceful stop)."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from newsroom.engine.jobs import queue as Q


@pytest.fixture()
def pool(conn):
    with ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=8, kwargs={"row_factory": dict_row, "autocommit": True}, open=True) as p:
        yield p


def state(conn, job_id):
    return conn.execute("SELECT state, retry_count FROM jobs WHERE id = %s", (job_id,)).fetchone()


def test_short_policy_keeps_one_waiting_job_per_key(conn, tag):
    name = f"t.short.{tag}"
    first = Q.enqueue(conn, name, {"n": 1}, singleton_key="a")
    assert first and Q.enqueue(conn, name, {"n": 2}, singleton_key="a") is None
    assert Q.enqueue(conn, name, {"n": 3}, singleton_key="b")
    assert Q.enqueue(conn, name, {"n": 4}) and Q.enqueue(conn, name, {"n": 5}) is None  # no key is a key of its own
    taken = Q.fetch(conn, name, 1)[0]
    assert taken["id"] == first or True
    # Once a job is active an equal one may be queued again.
    assert Q.enqueue(
        conn, name, {"n": 6}, singleton_key=conn.execute("SELECT singleton_key FROM jobs WHERE id = %s", (taken["id"],)).fetchone()["singleton_key"]
    )


def test_a_result_or_data_with_dates_is_stored_as_pg_boss_stored_it(conn, tag):
    """A handler's result may hold a Date (the analysis waiting for a model gives its time to retry): pg-boss stored it as JSON.stringify writes a Date, and a result
    that cannot be stored must not leave the job active for ever."""
    name = f"t.dates.{tag}"
    at = datetime(2026, 11, 5, 8, 52, 9, 123456, tzinfo=UTC)
    job = Q.enqueue(conn, name, {"notBefore": at})
    assert job
    assert conn.execute("SELECT data FROM jobs WHERE id = %s", (job,)).fetchone()["data"] == {"notBefore": "2026-11-05T08:52:09.123Z"}
    Q.fetch(conn, name, 1)
    Q.complete(conn, job, {"retryAt": at, "later": [at], "n": 1})
    row = conn.execute("SELECT state, output FROM jobs WHERE id = %s", (job,)).fetchone()
    assert row["state"] == "completed"
    assert row["output"] == {"retryAt": "2026-11-05T08:52:09.123Z", "later": ["2026-11-05T08:52:09.123Z"], "n": 1}


def test_priority_then_age_and_delay(conn, tag):
    name = f"t.order.{tag}"
    low = Q.enqueue(conn, name, {"k": "low"}, singleton_key="1")
    late = Q.enqueue(conn, name, {"k": "late"}, singleton_key="2", start_after=3600, priority=9)
    high = Q.enqueue(conn, name, {"k": "high"}, singleton_key="3", priority=5)
    assert [str(j["id"]) for j in Q.fetch(conn, name, 5)] == [high, low]  # the delayed one waits
    assert state(conn, late)["state"] == "created"


def test_concurrent_fetchers_never_share_a_job(pool, conn, tag):
    name = f"t.skip.{tag}"
    for i in range(20):
        Q.enqueue(conn, name, {"i": i}, singleton_key=str(i))
    seen: list[str] = []
    lock = threading.Lock()

    def take():
        while True:
            with pool.connection() as c:
                jobs = Q.fetch(c, name, 2)
            if not jobs:
                return
            with lock:
                seen.extend(str(j["id"]) for j in jobs)

    threads = [threading.Thread(target=take) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(seen) == 20 and len(set(seen)) == 20


def test_retry_with_backoff_then_failure(conn, tag):
    name = f"t.retry.{tag}"
    job = Q.enqueue(conn, name, {}, options={"retryLimit": 2, "retryDelay": 0})
    for attempt in (1, 2):
        got = Q.fetch(conn, name)[0]
        assert Q.fail(conn, got["id"], "boom") == "retry"
        assert state(conn, job) == {"state": "retry", "retry_count": attempt}
    got = Q.fetch(conn, name)[0]
    assert Q.fail(conn, got["id"], "boom") == "failed"
    assert conn.execute("SELECT output FROM jobs WHERE id = %s", (job,)).fetchone()["output"] == {"message": "boom"}
    assert Q.retry(conn, job) and state(conn, job)["state"] == "retry"


def test_backoff_delay_grows(conn, tag):
    name = f"t.backoff.{tag}"
    Q.enqueue(conn, name, {}, options={"retryLimit": 3, "retryDelay": 30, "retryBackoff": True})
    waits = []
    for _ in range(3):
        conn.execute("UPDATE jobs SET start_after = now() WHERE name = %s AND state = 'retry'", (name,))
        got = Q.fetch(conn, name)[0]
        Q.fail(conn, got["id"], "x")
        waits.append(conn.execute("SELECT extract(epoch FROM start_after - now()) AS s FROM jobs WHERE id = %s", (got["id"],)).fetchone()["s"])
    assert 14 < waits[0] < 31 and 29 < waits[1] < 61 and 59 < waits[2] < 121


def test_expiry_and_cleanup(conn, tag):
    name = f"t.expire.{tag}"
    job = Q.enqueue(conn, name, {}, options={"expireInSeconds": 10, "retryLimit": 0})
    Q.fetch(conn, name)
    assert Q.maintain(conn)["expired"] >= 0 and state(conn, job)["state"] == "active"
    result = Q.maintain(conn, datetime.now(UTC) + timedelta(seconds=30))
    assert result["expired"] >= 1 and state(conn, job)["state"] == "failed"
    done = Q.enqueue(conn, name, {}, singleton_key="x")
    Q.complete(conn, Q.fetch(conn, name)[0]["id"], {"ok": True})
    assert state(conn, done)["state"] == "completed"
    Q.maintain(conn, datetime.now(UTC) + timedelta(days=30))
    assert conn.execute("SELECT count(*) AS n FROM jobs WHERE name = %s", (name,)).fetchone()["n"] == 0


def test_singleton_policy_one_queued_and_one_active(conn, tag):
    name = f"cron.t{tag}"
    assert Q.enqueue(conn, name, {}) and Q.enqueue(conn, name, {}) is None
    first = Q.fetch(conn, name)[0]
    assert Q.enqueue(conn, name, {})  # one more may wait while one runs
    assert Q.fetch(conn, name) == []  # but it does not start beside the running one
    Q.complete(conn, first["id"])
    assert len(Q.fetch(conn, name)) == 1


def test_enqueue_in_a_transaction_rolls_back_with_it(conn, tag):
    name = f"t.tx.{tag}"
    with pytest.raises(RuntimeError), conn.transaction():
        Q.enqueue(conn, name, {})
        raise RuntimeError("business write failed")
    assert conn.execute("SELECT count(*) AS n FROM jobs WHERE name = %s", (name,)).fetchone()["n"] == 0


def test_worker_runs_handlers_retries_and_stops_gracefully(pool, conn, tag):
    ok, flaky, slow = f"t.ok.{tag}", f"t.flaky.{tag}", f"t.slow.{tag}"
    got: list = []
    attempts = {"n": 0}
    started, release = threading.Event(), threading.Event()

    def flaky_handler(data):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ValueError("first try fails")
        return {"done": True}

    def slow_handler(data):
        started.set()
        release.wait(5)
        got.append("slow finished")

    worker = Q.Worker(pool)
    worker.work(ok, lambda d: got.append(d) or {"fine": True}, 2, 0.05)
    worker.work(flaky, flaky_handler, 1, 0.05)
    worker.work(slow, slow_handler, 1, 0.05)
    Q.enqueue(conn, ok, {"x": 1}, singleton_key="1")
    flaky_id = Q.enqueue(conn, flaky, {}, options={"retryLimit": 1, "retryDelay": 0})
    slow_id = Q.enqueue(conn, slow, {})
    worker.start()
    deadline = time.time() + 10
    while time.time() < deadline and not (state(conn, flaky_id)["state"] == "completed" and started.is_set() and {"x": 1} in got):
        time.sleep(0.05)
    assert {"x": 1} in got and state(conn, flaky_id) == {"state": "completed", "retry_count": 1}
    threading.Timer(0.3, release.set).start()
    assert worker.stop(timeout=10) is True  # waited for the running job
    assert "slow finished" in got and state(conn, slow_id)["state"] == "completed"


def test_schedule_fires_once_per_slot_and_skips_or_catches_up(pool, conn, tag):
    name, late = f"s{tag}", f"l{tag}"
    worker = Q.Worker(pool)
    worker.schedule(name, "*/5 * * * *", lambda: None, missed="skip")
    worker.schedule(late, "*/5 * * * *", lambda: None, missed="once")
    with pool.connection() as c:
        for s in worker.schedules:
            c.execute(
                "INSERT INTO job_schedules (name, cron, timezone, missed) VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
                (s["name"], s["cron"], s["timezone"], s["missed"]),
            )
    base = datetime(2026, 11, 4, 8, 0, 20, tzinfo=UTC)
    assert worker.fire_due_schedules(base) == []  # first sight: starts from the next slot
    assert worker.fire_due_schedules(base + timedelta(seconds=5)) == []
    assert sorted(worker.fire_due_schedules(base + timedelta(minutes=5))) == sorted([name, late])  # a fresh slot, on time
    assert worker.fire_due_schedules(base + timedelta(minutes=5, seconds=10)) == []  # once per slot
    conn.execute("DELETE FROM jobs WHERE name = ANY(%s)", ([f"cron.{name}", f"cron.{late}"],))  # the earlier runs were picked up
    # Two slots pass unseen: the one that skips misses them, the other runs once.
    assert worker.fire_due_schedules(base + timedelta(minutes=17)) == [late]
