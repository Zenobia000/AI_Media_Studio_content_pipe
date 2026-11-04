"""Manual changes: every change a person or an operations script makes is recorded — who, when, what, before and after, and why. A change
made against a view that has moved on since is refused with a Conflict, which the admin API answers as 409. Port of audit.ts."""

from __future__ import annotations

import json
from datetime import UTC


class Conflict(Exception):
    code = "conflict"


def _default(value):
    """What JSON.stringify makes of a Date (ISO, milliseconds, Z); other values the column types can hold become text."""
    from datetime import datetime

    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return float(value) if hasattr(value, "as_tuple") else str(value)


def _json(value):
    return None if value is None else json.dumps(value, ensure_ascii=False, default=_default)


def audit(conn, actor: str, action: str, subject: str | None, reason: str | None, before, after, request_id: str | None = None) -> None:
    """Every manual change: who, when, what, why. Inside `with conn.transaction()` the row commits with the caller's transaction."""
    conn.execute(
        """INSERT INTO audit_log (actor, action, subject, reason, before, after, request_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
        (actor, action, subject, reason, _json(before), _json(after), request_id),
    )


def audit_history(conn, subject: str, limit: int = 20) -> list[dict]:
    """The latest manual changes of one subject (`source:<id>`, `content:<id>`), newest first."""
    return conn.execute(
        "SELECT created_at, actor, action, reason, before, after FROM audit_log WHERE subject = %s ORDER BY created_at DESC LIMIT %s",
        (subject, limit),
    ).fetchall()


def list_audit(conn, page: int, subject: str | None = None, action: str | None = None) -> dict:
    """The admin's audit page: every change, or those of one subject or action prefix, 100 a page."""
    rows = conn.execute(
        """SELECT id, created_at, actor, action, subject, reason, before, after FROM audit_log
           WHERE (%(s)s::text IS NULL OR subject = %(s)s) AND (%(a)s::text IS NULL OR action LIKE %(like)s)
           ORDER BY created_at DESC LIMIT 100 OFFSET %(off)s""",
        {"s": subject, "a": action, "like": f"{action or ''}%", "off": (page - 1) * 100},
    ).fetchall()
    return {"page": page, "rows": rows}
