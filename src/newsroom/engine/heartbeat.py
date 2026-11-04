"""Process heartbeats for the runs view and the watchdog: one settings row per process role (operations/heartbeat.ts)."""

from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

log = logging.getLogger("newsroom.heartbeat")
_STARTED = datetime.now(UTC)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def beat(pool, role: str) -> None:
    value = {"pid": os.getpid(), "host": socket.gethostname(), "release": os.environ.get("AIHOT_RELEASE", "dev"), "startedAt": _iso(_STARTED), "at": _iso(datetime.now(UTC))}
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (f"heartbeat.{role}", Jsonb(value), role),
        )


def start_heartbeat(pool, role: str, stop: threading.Event) -> threading.Thread:
    """Beats now and every minute until `stop` is set."""

    def run() -> None:
        while not stop.is_set():
            try:
                beat(pool, role)
            except Exception:
                log.debug("heartbeat failed", exc_info=True)
            stop.wait(60)

    t = threading.Thread(target=run, name="heartbeat", daemon=True)
    t.start()
    return t
