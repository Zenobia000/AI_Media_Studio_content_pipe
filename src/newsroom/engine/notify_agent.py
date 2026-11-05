"""Publish durable, source-owned events to the shared hub. Its documented idempotency contract makes uncertain HTTP results safe to retry with the same ID.
Execution/claim/retry state belongs to the hub. Port of notify/agent.ts."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from psycopg.types.json import Jsonb

from newsroom.engine.config import config, credential
from newsroom.engine.lib.text import js_slice
from newsroom.engine.notify_feishu import Finding, feishu_internal_enabled


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _subject(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9#._:/@-]{1,100}", key) else f"finding:{_hash(key)}"


def agent_configured() -> bool:
    """Whether an agent takes the alerts: events go to the hub, which wakes it."""
    return feishu_internal_enabled() and bool(credential("integrations", "WEBHOOK_HUB_URL")) and bool(credential("integrations", "WEBHOOK_HUB_KEY"))


def _deliver(pool, key: str, receipt: dict) -> bool:
    """Whether the hub has the event."""
    if receipt.get("accepted"):
        return True
    if not agent_configured():
        return False
    failure = "網路連線失敗或超時"
    try:
        res = httpx.post(
            credential("integrations", "WEBHOOK_HUB_URL"), follow_redirects=False,
            headers={"authorization": f"Bearer {credential('integrations', 'WEBHOOK_HUB_KEY')}", "content-type": "application/json"},
            content=json.dumps(receipt["event"], ensure_ascii=False, separators=(",", ":")).encode(), timeout=15,
        )  # fmt: skip
        if res.status_code == 200:
            with pool.connection() as conn:
                conn.execute("""UPDATE settings SET value = value || '{"accepted":true}'::jsonb, updated_at = now() WHERE key = %s""", (key,))
            return True
        failure = f"HTTP {res.status_code}"
    except Exception:
        pass  # timeout may mean accepted: keep the exact persisted ID for the next attempt. Never block owner alerts.
    with pool.connection() as conn:
        conn.execute("UPDATE settings SET value = value || %s, updated_at = now() WHERE key = %s", (Jsonb({"lastError": failure}), key))
    return False


def agent_transport_findings(pool, now_ms: float) -> list[Finding]:
    """Transport diagnosis only; execution and claims still belong exclusively to the hub."""
    with pool.connection() as conn:
        rows = conn.execute("SELECT value FROM settings WHERE key LIKE 'webhook.event.%' AND value->>'accepted' = 'false'").fetchall()
    stuck = [r for r in rows if now_ms - datetime.fromisoformat(r["value"]["queuedAt"].replace("Z", "+00:00")).timestamp() * 1000 >= 30 * 60_000]
    if not stuck:
        return []
    reasons = "、".join(dict.fromkeys(r["value"].get("lastError") or "尚未投遞" for r in stuck))
    return [Finding(
        key="webhook.transport", level="today", owner=True, title="AI 自動處理暫時收不到新任務",
        impact="交給 AI 處理的告警送不到它那裡；恢復之前，這些告警直接發給你", heals="尚未確認恢復", action="把這條發給 AI，檢查網站伺服器到自動化中臺的連線",
        detail=f"{len(stuck)} 項未送達；{reasons}",
    )]  # fmt: skip


def handoffs(pool, key: str, since: datetime) -> int:
    """How many problems under this key were handed to the agent since then: each incident is one event."""
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT count(*)::int AS n FROM settings WHERE key LIKE 'webhook.event.%%'
               AND value->'event'->>'subject' = %s AND value->'event'->>'type' IN ('alert.now', 'alert.today')
               AND (value->>'queuedAt')::timestamptz >= %s""",
            (_subject(key), since),
        ).fetchone()
    return row["n"]


def _publish(pool, event: dict, now_ms: float | None = None) -> bool:
    if not agent_configured():
        return False
    key = f"webhook.event.{_hash(event['id'])}"
    queued = datetime.fromtimestamp((now_ms if now_ms is not None else time.time() * 1000) / 1000, UTC)
    stamp = queued.strftime("%Y-%m-%dT%H:%M:%S.") + f"{queued.microsecond // 1000:03d}Z"
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, 'alerts') ON CONFLICT (key) DO NOTHING",
            (key, Jsonb({"event": event, "accepted": False, "queuedAt": stamp})),
        )
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return _deliver(pool, key, row["value"])


def flush_agent_events(pool) -> None:
    """Retry only pending transport receipts, never re-dispatch an accepted event."""
    if not agent_configured():
        return
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'webhook.event.%' AND value->>'accepted' = 'false' ORDER BY updated_at LIMIT 20"
        ).fetchall()
    for row in rows:
        _deliver(pool, row["key"], row["value"])


def _iso(d: datetime) -> str:
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def wake_agent(pool, f: Finding, now_ms: float | None = None) -> bool:
    identity = f.key if f.agent_only else f"{f.key}:{_iso(f.since)}"
    event_type = (f.agent_type or "alert.review") if f.agent_only else "alert.today" if f.level == "today" else "alert.now"
    return _publish(pool, {
        "id": identity if len(identity) <= 200 else f"alert:{_hash(identity)}", "type": event_type, "subject": _subject(f.key), "summary": js_slice(f.title, 0, 200),
        "detail": js_slice("\n".join(x for x in (f.impact, f.detail) if x), 0, 4000), "link": urljoin(config().site_url, "/admin/runs"),
    }, now_ms)  # fmt: skip


def wake_digest(pool, date: str, count: int) -> bool:
    """Called after the daily follow-up message was generated. No model or separate scheduler here. Whether the hub took the day's list."""
    return _publish(
        pool,
        {
            "id": f"digest:{date}",
            "type": "alert.digest",
            "subject": f"digest:{date}",
            "summary": f"系統日報 {date}",
            "detail": f"{count} 項待跟進",
            "link": urljoin(config().site_url, "/admin/runs"),
        },
    )
