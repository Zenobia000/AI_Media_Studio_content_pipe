"""The worker process: collection and body extraction jobs, their timers and the process heartbeat (apps/worker/src/main.ts, for what is ported)."""

from __future__ import annotations

import logging
import signal
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from newsroom.core.db import _FloatNumeric
from newsroom.engine.config import config
from newsroom.engine.heartbeat import start_heartbeat
from newsroom.engine.jobs.queue import Worker
from newsroom.engine.jobs.sources import register_processing_jobs, register_source_jobs
from newsroom.engine.sources.collect import SHUTDOWN

log = logging.getLogger("newsroom.worker")


def _configure(conn) -> None:
    conn.row_factory = dict_row
    conn.adapters.register_loader("numeric", _FloatNumeric)


def open_pool(database_url: str | None = None, size: int = 16) -> ConnectionPool:
    return ConnectionPool(
        database_url or config().database_url, min_size=1, max_size=size, max_idle=600, timeout=30, check=ConnectionPool.check_connection,
        configure=_configure, kwargs={"autocommit": True}, open=True,
    )  # fmt: skip


def build_worker(pool) -> Worker:
    worker = Worker(pool)
    register_source_jobs(worker)
    register_processing_jobs(worker)
    return worker


def run(database_url: str | None = None) -> None:
    """Runs until SIGINT or SIGTERM, then lets the running jobs finish."""
    pool = open_pool(database_url)
    worker = build_worker(pool)
    stop = threading.Event()
    start_heartbeat(pool, "worker", stop)
    worker.start()
    done = threading.Event()

    def on_signal(*_):
        done.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log.info("worker started")
    done.wait()
    log.info("worker stopping")
    SHUTDOWN.set()
    stop.set()
    worker.stop()
    pool.close()
