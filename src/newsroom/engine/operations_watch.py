"""The worker watchdog, run from the api process (the worker cannot report its own death). It keeps its state in settings and sends its own alerts. Port of
operations/watch.ts."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from newsroom.engine.notify_agent import flush_agent_events, wake_agent
from newsroom.engine.notify_feishu import Finding, format_alert, format_recovery, send_alert

log = logging.getLogger("newsroom.watch")
# The process manager restarts a crashed worker within seconds and a deploy restarts it on purpose; half an hour without a heartbeat means those did not help.
WORKER_STALE_MS = 30 * 60_000


def worker_down() -> Finding:
    return Finding(
        key="worker", level="now", title="後臺處理服務停了，網站不會出現新內容", impact="新內容的採集、處理、推送和日報全部暫停，網站停在舊內容上",
        heals="系統自動重啟沒有成功", action="把這條發給 AI 排查重啟",
    )  # fmt: skip


def _iso(d: datetime) -> str:
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def check_worker_heartbeat(pool) -> None:
    """Alerts once when the worker's heartbeat is older than half an hour, and once when it recovers. The stored state moves only after the message went out, so a failed
    send is tried again at the next check; the lock keeps two api processes (two overlap during a deploy) from both sending."""
    from dataclasses import replace

    went_down = None
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('watchdog.worker'))")
        hb = conn.execute("SELECT updated_at FROM settings WHERE key = 'heartbeat.worker'").fetchone()
        if hb:
            prior = conn.execute("SELECT value FROM settings WHERE key = 'watchdog.worker'").fetchone()
            now = time.time() * 1000
            stale = now - hb["updated_at"].timestamp() * 1000 > WORKER_STALE_MS
            state = "down" if stale else "up"
            if prior and prior["value"]["state"] == state:
                went_down = {"since": datetime.fromisoformat(prior["value"]["since"].replace("Z", "+00:00"))} if stale else None
            else:
                # A first check that finds the worker running only records it.
                if stale:
                    f = replace(worker_down(), since=hb["updated_at"])
                    woken = wake_agent(pool, f, now)
                    shown = replace(worker_down(), action="已自動通知 AI 排查重啟；如果半小時還沒收到“已恢復”再問 AI") if woken else worker_down()
                    msg = format_alert(shown, hb["updated_at"], now)
                    send_alert(msg["title"], msg["lines"])
                elif prior:
                    msg = format_recovery(worker_down().title, datetime.fromisoformat(prior["value"]["since"].replace("Z", "+00:00")), now)
                    send_alert(msg["title"], msg["lines"])
                # "since" of a down state is the last heartbeat, so the recovery can say how long it lasted.
                since = hb["updated_at"] if stale else datetime.fromtimestamp(now / 1000, UTC)
                conn.execute(
                    """INSERT INTO settings (key, value, updated_by) VALUES ('watchdog.worker', %s, 'api')
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                    (Jsonb({"state": state, "since": _iso(since)}),),
                )
                went_down = {"since": hb["updated_at"]} if stale else None
    # The hub deduplicates uncertain sends by their persisted occurrence ID.
    if went_down:
        wake_agent(pool, replace(worker_down(), since=went_down["since"]))
    flush_agent_events(pool)


def start_worker_watchdog(pool, stop: threading.Event) -> threading.Thread:
    def loop() -> None:
        while not stop.wait(5 * 60):
            try:
                check_worker_heartbeat(pool)
            except Exception:
                log.exception("worker watchdog failed")

    t = threading.Thread(target=loop, daemon=True, name="worker-watchdog")
    t.start()
    return t
