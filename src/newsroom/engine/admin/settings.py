"""Operator settings: about-page QR codes (replaced without a release), notification targets (switching a group on records enabled_at so older
content is never back-filled) and per-service request budgets (the circuit breaker paid calls check before sending). Port of admin/settings.ts."""

from __future__ import annotations

import hashlib
import io

from psycopg.types.json import Jsonb

from newsroom.engine.audit import audit
from newsroom.engine.config import config
from newsroom.engine.site import load_contact

MAX_QR_BYTES = 2 * 1024 * 1024


def replace_contact_qr(pool, slot: str, data: bytes, actor: str) -> dict:
    from PIL import Image  # noqa: PLC0415

    if slot not in ("wechatQr", "feishuQr"):
        raise Exception("unknown slot")  # noqa: TRY002
    if len(data) > MAX_QR_BYTES:
        raise Exception("二維碼圖片最大 2MB")  # noqa: TRY002
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = (img.format or "").lower(), img.size
    except Exception:
        fmt, width, height = "", 0, 0
    if fmt not in ("png", "jpeg", "webp"):
        raise Exception("需要 PNG、JPG 或 WebP 圖片")  # noqa: TRY002
    if width < 120 or height < 120:
        raise Exception("圖片太小，二維碼可能掃不出來")  # noqa: TRY002
    ext = "jpg" if fmt == "jpeg" else fmt
    name = f"qr-{'wechat' if slot == 'wechatQr' else 'feishu'}-{hashlib.sha256(data).hexdigest()[:8]}.{ext}"
    directory = config().data_dir / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    with pool.connection() as conn, conn.transaction():
        before = load_contact(conn)
        nxt = {**before, slot: f"/contact/{name}"}
        conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES ('contact_qr', %s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (Jsonb(nxt), actor),
        )
        audit(conn, actor, "settings.contact_qr", "settings:contact_qr", None, {slot: before.get(slot)}, {slot: nxt[slot]})
    return nxt


def list_targets(conn) -> list[dict]:
    return conn.execute(
        """SELECT t.key, t.purpose, t.kind, t.enabled, t.enabled_at, t.config_ref, t.note, t.updated_at,
                  (SELECT count(*)::int FROM deliveries d WHERE d.target_key = t.key AND d.created_at > now() - interval '7 days') AS deliveries_7d,
                  (SELECT max(d.sent_at) FROM deliveries d WHERE d.target_key = t.key) AS last_sent_at
           FROM notify_targets t ORDER BY t.purpose, t.key"""
    ).fetchall()


def list_budgets(conn) -> list[dict]:
    return conn.execute(
        """SELECT b.service, b.per_minute, b.per_hour, b.per_day, b.note, b.updated_at,
                  (SELECT count(*)::int FROM receipt_attempts a WHERE a.service = b.service AND a.origin = 'live' AND a.started_at > now() - interval '1 day') AS used_day,
                  (SELECT count(*)::int FROM receipt_attempts a WHERE a.service = b.service AND a.origin = 'live' AND a.started_at > now() - interval '1 hour') AS used_hour
           FROM budgets b ORDER BY b.service"""
    ).fetchall()


def settings_overview(pool) -> dict:
    """The settings page: contact QR codes, content push targets and paid-service budgets."""
    with pool.connection() as conn:
        return {"contact": load_contact(conn), "targets": list_targets(conn), "budgets": list_budgets(conn)}


def set_target_enabled(pool, key: str, enabled: bool, reason: str, actor: str) -> dict | None:
    if not (reason or "").strip():
        raise Exception("reason is required")  # noqa: TRY002
    with pool.connection() as conn, conn.transaction():
        before = conn.execute("SELECT enabled, enabled_at FROM notify_targets WHERE key = %s", (key,)).fetchone()
        if not before:
            return None
        after = conn.execute(
            """UPDATE notify_targets SET enabled = %(on)s, enabled_at = CASE WHEN %(on)s AND NOT enabled THEN now() ELSE enabled_at END, updated_at = now()
               WHERE key = %(key)s RETURNING key, enabled, enabled_at""",
            {"on": enabled, "key": key},
        ).fetchone()
        audit(conn, actor, "notify.enable" if enabled else "notify.disable", f"notify-target:{key}", reason, _plain(before), _plain(after))
    return {**after, "pushEnabledHere": config().feishu_content_push_enabled}


def _plain(row: dict) -> dict:
    from newsroom.engine.publication.v1 import iso  # noqa: PLC0415

    return {k: (iso(v) if hasattr(v, "isoformat") else v) for k, v in row.items()}


def update_budget(pool, service: str, per_minute, per_hour, per_day, reason: str, actor: str) -> dict:
    if not (reason or "").strip():
        raise Exception("reason is required")  # noqa: TRY002
    for v in (per_minute, per_hour, per_day):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise Exception("budgets are non-negative integers (0 stops the service)")  # noqa: TRY002
    with pool.connection() as conn, conn.transaction():
        before = conn.execute("SELECT per_minute, per_hour, per_day FROM budgets WHERE service = %s", (service,)).fetchone()
        after = conn.execute(
            """INSERT INTO budgets (service, per_minute, per_hour, per_day, note) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (service) DO UPDATE SET per_minute = EXCLUDED.per_minute, per_hour = EXCLUDED.per_hour, per_day = EXCLUDED.per_day, note = EXCLUDED.note, updated_at = now()
               RETURNING service, per_minute, per_hour, per_day""",
            (service, per_minute, per_hour, per_day, reason),
        ).fetchone()
        audit(conn, actor, "budget.update", f"budget:{service}", reason, before, after)
    return after
