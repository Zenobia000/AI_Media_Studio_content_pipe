"""The schedules of collection and fetch, run by one process (`newsroom run-scheduler`). They used to run inside AIHOT's worker; the times and
the "a missed run is skipped, not made up" rule are the same. Times are Asia/Shanghai (UTC+8, the same clock as Taipei).

A run that is already going (this process or another one) is not started a second time: each job takes a PostgreSQL advisory lock for its
duration."""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from newsroom.core import db
from newsroom.core.config import credential

log = logging.getLogger("newsroom.schedule")
TZ = timezone(timedelta(hours=8))


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
            if step != 1:
                b = hi
        out.update(range(a, b + 1, step))
    return out


def cron_matches(expr: str, at: datetime) -> bool:
    """A five-field cron expression (minute hour day-of-month month day-of-week; lists, ranges and steps) against a minute."""
    minute, hour, dom, month, dow = expr.split()
    local = at.astimezone(TZ)
    weekday = (local.weekday() + 1) % 7  # cron: 0 is Sunday
    return (
        local.minute in _field(minute, 0, 59)
        and local.hour in _field(hour, 0, 23)
        and local.day in _field(dom, 1, 31)
        and local.month in _field(month, 1, 12)
        and weekday in _field(dow, 0, 6)
    )


@dataclass
class Job:
    name: str
    cron: str
    run: Callable[[], object]  # opens its own connection


def _with_conn(fn):
    def run():
        with db.connect() as conn:
            return fn(conn)

    return run


def jobs() -> list[Job]:
    from newsroom.collect.changedetect import run_watch_poll  # noqa: PLC0415
    from newsroom.collect.inbox import ManagedBlockError  # noqa: PLC0415
    from newsroom.collect.inbox_writer import write_inbox  # noqa: PLC0415
    from newsroom.collect.newsletters import split_newsletters  # noqa: PLC0415
    from newsroom.collect.signals import collect_signals  # noqa: PLC0415
    from newsroom.fetch.selftest import load_targets, run_selftest  # noqa: PLC0415

    def selftest(conn):
        checks = run_selftest(conn, load_targets())
        return {
            "failed": [f"{c.name} {c.url or ''}：{c.detail}" for c in checks if c.status == "fail"],
            "warned": [f"{c.name}：{c.detail}" for c in checks if c.status == "warn"],
        }

    def inbox(conn):
        try:
            return write_inbox(conn)
        except ManagedBlockError as e:
            return {"error": str(e)}

    return [
        Job("fetch-selftest", "40 6 * * *", _with_conn(selftest)),
        Job("newsletter-split", "20,50 * * * *", _with_conn(lambda c: split_newsletters(c))),
        Job("signals-snapshot", "5 * * * *", _with_conn(lambda c: collect_signals(c, github_token=credential("collectors", "GITHUB_TOKEN")))),
        Job("page-watch", "*/10 * * * *", _with_conn(run_watch_poll)),
        Job("inbox-write", "*/30 * * * *", _with_conn(inbox)),
    ]


def run_job(job: Job) -> None:
    """Runs one job unless it is already running anywhere; a failure is logged and does not stop the others."""
    with db.connect() as lock:
        got = cast(dict, lock.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS ok", (f"newsroom:{job.name}",)).fetchone())["ok"]
        if not got:
            log.info(json.dumps({"job": job.name, "msg": "already running, skipped"}))
            return
        started = time.monotonic()
        try:
            result = job.run()
            log.info(
                json.dumps(
                    {"job": job.name, "ok": True, "seconds": round(time.monotonic() - started, 1), "result": result}, ensure_ascii=False, default=str
                )
            )
        except Exception:  # noqa: BLE001
            log.error(json.dumps({"job": job.name, "ok": False, "error": traceback.format_exc()[-800:]}, ensure_ascii=False))
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"newsroom:{job.name}",))


def due(all_jobs: list[Job], at: datetime) -> list[Job]:
    return [j for j in all_jobs if cron_matches(j.cron, at)]


def run(once: bool = False) -> None:
    """Checks once a minute which jobs are due. A minute the process was not running is not made up (missed: skip). With `once`, runs the
    jobs due this minute and returns."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    all_jobs = jobs()
    if once:
        for job in due(all_jobs, datetime.now(timezone.utc)):
            run_job(job)
        return
    log.info(json.dumps({"msg": "scheduler started", "jobs": [f"{j.name} ({j.cron})" for j in all_jobs]}, ensure_ascii=False))
    last_minute = None
    while True:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if now != last_minute:
            last_minute = now
            for job in due(all_jobs, now):
                # A long job (the self-test takes minutes) must not hold up the others.
                threading.Thread(target=run_job, args=(job,), daemon=True, name=job.name).start()
        time.sleep(5.0)
