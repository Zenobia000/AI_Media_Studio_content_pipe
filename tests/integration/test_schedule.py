"""The scheduler of collection and fetch (collect/schedule.py): which minute a cron line means, on the Taipei clock, and which jobs are due."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from newsroom.collect import schedule as S


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("expr", "at", "due"),
    [
        ("40 6 * * *", "2026-11-14 22:40", True),  # 06:40 in Taipei is 22:40 UTC the day before
        ("40 6 * * *", "2026-11-15 06:40", False),
        ("*/10 * * * *", "2026-11-15 03:20", True),
        ("*/10 * * * *", "2026-11-15 03:25", False),
        ("20,50 * * * *", "2026-11-15 03:50", True),
        ("20,50 * * * *", "2026-11-15 03:30", False),
        ("0 9-17 * * *", "2026-11-15 01:00", True),  # 09:00 in Taipei
        ("0 9-17 * * *", "2026-11-15 10:00", False),  # 18:00
        ("5/15 * * * *", "2026-11-15 03:35", True),  # from minute 5, every 15
        ("5/15 * * * *", "2026-11-15 03:30", False),
        ("0 0 * * 0", "2026-11-14 16:00", True),  # midnight of Sunday the 15th in Taipei; cron's 0 is Sunday
        ("0 0 * * 0", "2026-11-15 16:00", False),  # Monday
        ("0 0 1 12 *", "2026-11-30 16:00", True),  # the first of December
        ("0 0 1 12 *", "2026-10-31 16:00", False),
    ],
)
def test_a_cron_line_is_read_on_the_taipei_clock(expr, at, due):
    assert S.cron_matches(expr, utc(at)) is due


def test_the_jobs_due_at_a_minute():
    jobs = S.jobs()
    assert {j.name for j in jobs} == {"fetch-selftest", "newsletter-split", "signals-snapshot", "page-watch", "inbox-write"}

    def due(at: str) -> set[str]:
        return {j.name for j in S.due(jobs, utc(at))}

    assert due("2026-11-14 22:40") == {"fetch-selftest", "page-watch"}  # 06:40
    assert due("2026-11-15 03:00") == {"page-watch", "inbox-write"}
    assert due("2026-11-15 03:05") == {"signals-snapshot"}
    assert due("2026-11-15 03:50") == {"newsletter-split", "page-watch"}
    assert due("2026-11-15 03:07") == set()


@pytest.mark.usefixtures("conn")
def test_a_job_runs_unless_it_is_already_running_and_a_failure_does_not_stop_the_scheduler(conn):
    ran: list[str] = []
    S.run_job(S.Job("test-schedule-ok", "* * * * *", lambda: ran.append("ok")))

    def broken():
        ran.append("broken")
        raise RuntimeError("boom")

    S.run_job(S.Job("test-schedule-broken", "* * * * *", broken))  # logged, not raised
    assert ran == ["ok", "broken"]
    # held by another connection (another process running the same job): skipped, and run again once it is free
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", ("newsroom:test-schedule-held",))
    try:
        S.run_job(S.Job("test-schedule-held", "* * * * *", lambda: ran.append("held")))
        assert "held" not in ran
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("newsroom:test-schedule-held",))
    S.run_job(S.Job("test-schedule-held", "* * * * *", lambda: ran.append("held")))
    assert ran[-1] == "held"
