"""The alert checks that test_alerts leaves to a single glance: many sources failing at once, the age of the last backup, one source's own follow-ups, and jobs that have
waited too long. The TypeScript engine and the Python port each read their own copy of one database, set up the same way, and must find the same things (collectFindings)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import psycopg

from tests.parity.test_alerts import MIN, NOW, Side, at, world  # noqa: F401  (the fixture)

HOUR = 60 * MIN
N = "(CASE WHEN id LIKE 'fs-%' THEN substring(id from 4)::int ELSE 0 END)"  # the number of a test source, 0 for the others
NOSTORE = {k: "" for k in ("DB_BACKUP_STORE_SECRET_ID", "DB_BACKUP_STORE_SECRET_KEY", "DB_BACKUP_STORE_BUCKET", "DB_BACKUP_STORE_REGION")}
STORE = {
    "DB_BACKUP_STORE_SECRET_ID": "id",
    "DB_BACKUP_STORE_SECRET_KEY": "key",
    "DB_BACKUP_STORE_BUCKET": "bucket",
    "DB_BACKUP_STORE_REGION": "ap-test",
}


def both(urls, sql: str, params: tuple = ()) -> None:
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(sql, params or None)


def findings(side: Side, extra: dict | None = None, when: int = NOW) -> list[dict]:
    calls = [["collectFindings", when]]
    want, got = side.run(calls, extra=extra)
    side.compare("findings", calls, want, got)
    return got[0]


def keys(found: list[dict], prefix: str) -> list[str]:
    return sorted(f["key"] for f in found if f["key"].startswith(prefix))


def clean(urls) -> None:
    both(urls, "DELETE FROM fetch_runs")
    both(urls, "DELETE FROM job_runs")
    both(urls, "DELETE FROM sources WHERE id LIKE 'fs-%'")
    both(urls, "DELETE FROM settings WHERE key IN ('backup.last') OR key LIKE 'alerts.%'")


def add_sources(urls, n: int) -> None:
    both(
        urls,
        "INSERT INTO sources (id, name, kind, tier, participation_mode) SELECT 'fs-' || i, 'Many ' || i, 'rss', 'T2', 'editorial' FROM generate_series(1, %s) i",
        (n,),
    )


def run(urls, status_by_source: str, error: str | None, when: datetime, where: str) -> None:
    """One fetch run of the enabled sources matching `where`."""
    both(
        urls,
        f"INSERT INTO fetch_runs (source_id, started_at, finished_at, status, error) SELECT id, %s, %s, %s, %s FROM sources WHERE enabled AND {where.replace('%', '%%')}",
        (when, when, status_by_source, error),
    )


def test_many_sources_failing(world, monkeypatch, tmp_path):  # noqa: F811
    side = Side(world, tmp_path, monkeypatch)
    clean(world)
    add_sources(world, 60)
    # 66 enabled sources; the latest run of every one of them failed, with four different errors; later runs of the sources that are not in the "failing" group are ignored
    # when they lie after the instant asked about
    t = at(NOW - HOUR)
    # 30, 15, 10 and 5 sources fail with their own error, and the 6 others with a fifth: different counts, because a tie has no order
    for i, (error, low, high) in enumerate([("connect timeout", 1, 30), ("HTTP 503", 31, 45), ("dns failure", 46, 55), ("tls handshake", 56, 60)]):
        run(world, "failed", error, t - timedelta(minutes=i), f"{N} BETWEEN {low} AND {high}")
    run(world, "failed", "proxy refused", t, "id NOT LIKE 'fs-%'")
    found = findings(side)
    assert "content.fetch" in keys(found, "content.fetch"), found
    detail = next(f["detail"] for f in found if f["key"] == "content.fetch")
    assert "失敗信源佔 100%" in detail

    # fifteen of the 60 recover with a newer run: 51 left failing, and 51 < 15% is false, so it stays
    run(world, "ok", None, at(NOW - 30 * MIN), f"{N} BETWEEN 1 AND 15")
    assert keys(findings(side), "content.fetch") == ["content.fetch"]
    # ten more: 41 failing, below the count of 50
    run(world, "ok", None, at(NOW - 20 * MIN), f"{N} BETWEEN 16 AND 25")
    assert keys(findings(side), "content.fetch") == []
    # a run in the future of the instant asked about changes nothing, and neither does a run that is still going
    both(
        world, "INSERT INTO fetch_runs (source_id, started_at, status) SELECT id, %s, 'ok' FROM sources WHERE id LIKE 'fs-%%'", (at(NOW + 30 * MIN),)
    )
    both(
        world,
        "INSERT INTO fetch_runs (source_id, started_at, status) SELECT id, %s, 'running' FROM sources WHERE id LIKE 'fs-%%'",
        (at(NOW - 5 * MIN),),
    )
    assert keys(findings(side), "content.fetch") == []
    # the failures are all there when asked about a time before the recoveries
    assert keys(findings(side, when=NOW - 40 * MIN), "content.fetch") == ["content.fetch"]
    # a failing share under 15% of a large site stays quiet even with 60 failures
    clean(world)
    add_sources(world, 500)
    run(world, "ok", None, at(NOW - HOUR), f"{N} > 60 OR {N} = 0")
    run(world, "failed", "HTTP 503", at(NOW - HOUR), f"{N} BETWEEN 1 AND 60")
    assert keys(findings(side), "content.fetch") == []
    run(world, "failed", "HTTP 503", at(NOW - 10 * MIN), f"{N} BETWEEN 61 AND 110")
    assert keys(findings(side), "content.fetch") == ["content.fetch"]  # 110 of 506 failing: more than 50 and more than 15%... 21.7%


def test_backup_age(world, monkeypatch, tmp_path):  # noqa: F811
    side = Side(world, tmp_path, monkeypatch)
    clean(world)
    # no object store configured: nothing to say
    assert keys(findings(side, NOSTORE), "backup.") == []

    def backup(value: dict | None, ok_at: float | None = None, failed_at: float | None = None) -> None:
        both(world, "DELETE FROM settings WHERE key = 'backup.last'")
        both(world, "DELETE FROM job_runs")
        if value is not None:
            both(
                world,
                "INSERT INTO settings (key, value, updated_by) VALUES ('backup.last', %s::jsonb, 'worker')",
                (json.dumps({**value, "objects": []}),),
            )
        if ok_at is not None:
            both(
                world, "INSERT INTO job_runs (job, started_at, finished_at, status) VALUES ('ops.backup', %s, %s, 'ok')", (at(ok_at - MIN), at(ok_at))
            )
        if failed_at is not None:
            both(
                world,
                "INSERT INTO job_runs (job, started_at, finished_at, status, error) VALUES ('ops.backup', %s, %s, 'failed', 'boom')",
                (at(failed_at - MIN), at(failed_at)),
            )

    def stamp(ms: float) -> str:
        return at(ms).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at(ms).microsecond // 1000:03d}Z"

    cases = [
        ("no record at all", None, None, None, ["backup.stale"]),
        ("a good backup two hours ago", {"at": stamp(NOW - 2 * HOUR), "uploaded": True}, None, None, []),
        ("a good backup just inside a day", {"at": stamp(NOW - 29 * HOUR), "uploaded": True}, None, None, []),
        ("a backup a day and a half ago", {"at": stamp(NOW - 40 * HOUR), "uploaded": True}, None, None, ["backup.stale"]),
        ("a backup two and a half days ago", {"at": stamp(NOW - 60 * HOUR), "uploaded": True}, None, None, ["backup.failed"]),
        ("exactly fifty hours", {"at": stamp(NOW - 50 * HOUR), "uploaded": True}, None, None, ["backup.stale"]),
        ("not uploaded, recent", {"at": stamp(NOW - 2 * HOUR), "uploaded": False}, None, None, ["backup.stale"]),
        ("not uploaded, a successful run an hour ago", {"at": stamp(NOW - 2 * HOUR), "uploaded": False}, NOW - HOUR, None, ["backup.stale"]),
        ("not uploaded, long ago, and no run history to count from", {"at": stamp(NOW - 70 * HOUR), "uploaded": False}, None, None, ["backup.stale"]),
        ("not uploaded, long ago, with a first attempt long ago", {"at": stamp(NOW - 70 * HOUR), "uploaded": False}, None, NOW - 70 * HOUR, ["backup.failed"]),
        ("the archive of files failed", {"at": stamp(NOW - 60 * HOUR), "uploaded": True, "filesError": "tar failed"}, None, None, ["backup.failed"]),
        ("the archive of files failed, recent", {"at": stamp(NOW - 2 * HOUR), "uploaded": True, "filesError": "tar failed"}, None, None, []),
        ("the run history is newer than the record", {"at": stamp(NOW - 60 * HOUR), "uploaded": True}, NOW - 2 * HOUR, None, []),
        ("only failed runs, the first one long ago", None, None, NOW - 60 * HOUR, ["backup.failed"]),
        ("only failed runs, the first one recent", None, None, NOW - HOUR, ["backup.stale"]),
        ("a failed record, a failed run, the last success long ago", {"at": stamp(NOW - HOUR), "uploaded": False}, NOW - 55 * HOUR, NOW - HOUR, ["backup.failed"]),
    ]  # fmt: skip
    seen = set()
    for label, value, ok_at, failed_at, expect in cases:
        backup(value, ok_at, failed_at)
        found = findings(side, STORE)
        assert keys(found, "backup.") == expect, (label, found)
        seen.update(expect)
    assert seen == {"backup.stale", "backup.failed"}
    backup(None)
    assert keys(findings(side, NOSTORE), "backup.") == []  # without the store configured again


def test_one_sources_own_follow_ups(world, monkeypatch, tmp_path):  # noqa: F811
    side = Side(world, tmp_path, monkeypatch)
    clean(world)
    add_sources(world, 6)
    # three sources that keep failing, one whose runs fail and then work, one that keeps reading a page without entries, one that works
    for n in (1, 2, 3):
        for k in range(1, 6):
            both(
                world,
                "INSERT INTO fetch_runs (source_id, started_at, finished_at, status, error) VALUES (%s, %s, %s, 'failed', %s)",
                (f"fs-{n}", at(NOW - k * HOUR), at(NOW - k * HOUR), f"HTTP 50{n}"),
            )
    for k in range(1, 6):
        both(
            world,
            "INSERT INTO fetch_runs (source_id, started_at, finished_at, status, error) VALUES ('fs-4', %s, %s, %s, %s)",
            (at(NOW - k * HOUR), at(NOW - k * HOUR), "failed" if k > 1 else "ok", "x" if k > 1 else None),
        )
    for k in range(1, 6):
        both(
            world,
            "INSERT INTO fetch_runs (source_id, started_at, finished_at, status, found_count, detail) VALUES ('fs-5', %s, %s, 'ok', 0, %s::jsonb)",
            (at(NOW - k * HOUR), at(NOW - k * HOUR), json.dumps({"unusable": True})),
        )
    both(
        world,
        "INSERT INTO fetch_runs (source_id, started_at, finished_at, status, found_count) VALUES ('fs-6', %s, %s, 'ok', 5)",
        (at(NOW - HOUR), at(NOW - HOUR)),
    )
    both(
        world,
        "UPDATE sources SET fail_count = 5, health = 'failing', last_error = 'HTTP 50' || substring(id from 4), last_fetch_at = %s WHERE id IN ('fs-1', 'fs-2', 'fs-3')",
        (at(NOW - HOUR),),
    )
    found = findings(side)
    follow = keys(found, "sources.")
    assert any(k.startswith("sources.failing.fs-") for k in follow), follow
    # collection switched off: no follow-ups at all
    both(world, "UPDATE sources SET enabled = false WHERE id = 'fs-1'")
    follow2 = keys(findings(side), "sources.")
    assert "sources.failing.fs-1" not in follow2 and follow2 != follow, (follow, follow2)


def test_jobs_waiting_too_long(world, monkeypatch, tmp_path):  # noqa: F811
    side = Side(world, tmp_path, monkeypatch)
    clean(world)
    findings(side)  # the TypeScript engine creates its queue tables when it starts

    def waiting(state: str, started_after_ms: float, n: int = 1, name: str = "events.group") -> None:
        # the TypeScript queue is pg-boss's table, the Python one the port's own
        with psycopg.connect(world["al_node"], autocommit=True) as c:
            c.execute(
                "INSERT INTO pgboss.job (name, state, start_after) SELECT %s, %s::pgboss.job_state, %s FROM generate_series(1, %s)",
                (name, state, at(started_after_ms), n),
            )
        with psycopg.connect(world["al_py"], autocommit=True) as c:
            c.execute(
                "INSERT INTO jobs (name, state, start_after, singleton_key) SELECT %s, %s, %s, nextval('job_runs_id_seq')::text FROM generate_series(1, %s)",
                (name, state, at(started_after_ms), n),
            )

    def empty() -> None:
        with psycopg.connect(world["al_node"], autocommit=True) as c:
            c.execute("DELETE FROM pgboss.job")
        with psycopg.connect(world["al_py"], autocommit=True) as c:
            c.execute("DELETE FROM jobs")

    empty()
    assert keys(findings(side), "queue.") == []
    waiting("created", NOW - HOUR, 3)  # an hour: not yet
    assert keys(findings(side), "queue.") == []
    waiting("retry", NOW - 3 * HOUR, 2)
    waiting("created", NOW - 150 * MIN, 1)
    found = findings(side)
    assert keys(found, "queue.") == ["queue.events.group"], found
    detail = next(f["detail"] for f in found if f["key"] == "queue.events.group")
    assert detail.startswith("6 個等待"), detail
    empty()
    waiting("active", NOW - 5 * HOUR, 4)  # running, not waiting
    waiting("completed", NOW - 5 * HOUR, 4)
    waiting("created", NOW + 5 * HOUR, 4)  # deferred into the future
    assert keys(findings(side), "queue.") == []
    # asked about a later time, the hour-old job has waited long enough
    waiting("created", NOW - HOUR, 2)
    assert keys(findings(side, when=NOW + 2 * HOUR), "queue.") == ["queue.events.group"]
