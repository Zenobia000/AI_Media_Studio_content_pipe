"""Feedback handling: list, status, kind and note, the pool of suggested ideas, per-source bans, and deletion on request (privacy notice: feedback
material is removed once handling ends or when the sender asks). Port of admin/feedback.ts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from newsroom.engine.audit import Conflict, audit
from newsroom.engine.config import config
from newsroom.engine.operations_feedback import feedback_email_key
from newsroom.engine.publication.v1 import iso

FEEDBACK_STATUSES = ["new", "triaged", "replied", "resolved", "spam"]
# What handling a feedback needs: fix a bug, keep a suggestion, answer a question, or nothing of these.
FEEDBACK_KINDS = ["bug", "suggestion", "question", "other"]
IDEA_MAX = 40  # an idea's name is a short phrase, the same for every suggestion of the same thing


def feedback_ideas(conn) -> list[dict]:
    """The whole suggestion pool, most asked first: one sender asking twice counts once, spam never counts."""
    return conn.execute(
        """SELECT idea, count(DISTINCT source_hash)::int AS people FROM feedback
           WHERE kind = 'suggestion' AND idea IS NOT NULL AND status <> 'spam' GROUP BY idea ORDER BY people DESC, max(created_at) DESC"""
    ).fetchall()


def list_feedback(pool, f: dict) -> dict:
    page = max(1, f.get("page") or 1)
    q = f"%{f['q'].strip()}%" if f.get("q") and f["q"].strip() else None
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT fb.id, fb.content, fb.email, fb.page_url, split_part(fb.screenshot_key, ':', 1) AS screenshot, fb.source_hash, fb.status, fb.kind, fb.idea, fb.note,
                      fb.forwarded_at, fb.forward_error, fb.created_at, fb.updated_at,
                      EXISTS (SELECT 1 FROM feedback_bans b WHERE b.source_hash = fb.source_hash AND (b.expires_at IS NULL OR b.expires_at > now())) AS banned,
                      (SELECT count(*)::int FROM feedback o WHERE o.source_hash = fb.source_hash) AS from_source
               FROM feedback fb
               WHERE (%(status)s::text IS NULL OR fb.status = %(status)s) AND (%(kind)s::text IS NULL OR fb.kind = %(kind)s)
                 AND (%(idea)s::text IS NULL OR fb.idea = %(idea)s) AND (%(id)s::bigint IS NULL OR fb.id = %(id)s)
                 AND (%(q)s::text IS NULL OR fb.content ILIKE %(q)s OR fb.email ILIKE %(q)s OR fb.page_url ILIKE %(q)s)
               ORDER BY fb.created_at DESC LIMIT 50 OFFSET %(offset)s""",
            {"status": f.get("status"), "kind": f.get("kind"), "idea": f.get("idea"), "id": f.get("id"), "q": q, "offset": (page - 1) * 50},
        ).fetchall()
        counts = conn.execute("SELECT status, count(*)::int AS n FROM feedback GROUP BY 1").fetchall()
        kinds = conn.execute("SELECT kind, count(*)::int AS n FROM feedback WHERE kind IS NOT NULL GROUP BY 1").fetchall()
        ideas = feedback_ideas(conn)
        bans = conn.execute(
            """SELECT source_hash, reason, created_by, created_at, expires_at FROM feedback_bans
               WHERE expires_at IS NULL OR expires_at > now() ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()
        # Query these separately from the capped ban list: a displayed feedback may have an older ban.
        email_keys = list(dict.fromkeys(feedback_email_key(r["email"]) for r in rows if r["email"]))
        email_bans = (
            {b["source_hash"] for b in conn.execute(
                "SELECT source_hash FROM feedback_bans WHERE source_hash = ANY(%s) AND (expires_at IS NULL OR expires_at > now())", (email_keys,)
            ).fetchall()}
            if email_keys else set()
        )  # fmt: skip
    return {
        "page": page,
        "rows": [{**r, "email_banned": bool(r["email"]) and feedback_email_key(r["email"]) in email_bans} for r in rows],
        "counts": {c["status"]: c["n"] for c in counts}, "kinds": {c["kind"]: c["n"] for c in kinds}, "ideas": ideas, "bans": bans,
    }  # fmt: skip


def update_feedback(pool, feedback_id: int, status, kind, idea, note, version: str, actor: str, given: set[str]) -> dict | None:
    """Status, kind, idea and note, against the version the admin read. Only a suggestion names an idea, so a feedback reclassified as anything
    else leaves the pool. `given` names the fields the request carried (a missing field keeps its value; a null one clears it)."""
    if status and status not in FEEDBACK_STATUSES:
        raise Exception(f"unknown status {status}")  # noqa: TRY002 (a plain error: the guard answers 500, as the TypeScript does)
    if kind and kind not in FEEDBACK_KINDS:
        raise Exception(f"unknown kind {kind}")  # noqa: TRY002
    new_idea = ((idea or "").strip() or None) if "idea" in given else None
    if new_idea and len(new_idea) > IDEA_MAX:
        raise Exception(f"建議名最多 {IDEA_MAX} 字")  # noqa: TRY002
    with pool.connection() as conn, conn.transaction():
        before = conn.execute("SELECT id, status, kind, idea, note, updated_at FROM feedback WHERE id = %s FOR UPDATE", (feedback_id,)).fetchone()
        if not before:
            return None
        if iso(before["updated_at"]) != version:
            raise Conflict("這條反饋已被修改，請重新整理後再操作")
        new_kind = kind if "kind" in given else before["kind"]
        if new_idea and new_kind != "suggestion":
            raise Exception("只有建議能歸入建議池")  # noqa: TRY002
        kept_idea = (new_idea if "idea" in given else before["idea"]) if new_kind == "suggestion" else None
        after = conn.execute(
            """UPDATE feedback SET status = coalesce(%s, status), kind = %s, idea = %s, note = %s, updated_at = now()
               WHERE id = %s RETURNING id, status, kind, idea, note, updated_at""",
            (status, new_kind, kept_idea, note if "note" in given else before["note"], feedback_id),
        ).fetchone()

        def record(r):
            return {"status": r["status"], "kind": r["kind"], "idea": r["idea"], "note": r["note"]}

        audit(conn, actor, "feedback.update", f"feedback:{feedback_id}", None, record(before), record(after))
    return after


def ban_source(pool, source_hash: str, reason: str, actor: str, expires_at: datetime | None = None) -> None:
    """Refuses further feedback from one source (an unreadable hash of IP and browser family)."""
    if not reason.strip():
        raise Exception("reason is required")  # noqa: TRY002
    if expires_at and not expires_at > datetime.now(UTC):
        raise Exception("expiry must be in the future")  # noqa: TRY002
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO feedback_bans (source_hash, reason, created_by, expires_at) VALUES (%s, %s, %s, %s)
               ON CONFLICT (source_hash) DO UPDATE SET reason = EXCLUDED.reason, created_by = EXCLUDED.created_by, created_at = now(), expires_at = EXCLUDED.expires_at""",
            (source_hash, reason, actor, expires_at),
        )
        audit(conn, actor, "feedback.ban", f"feedback-source:{source_hash}", reason, None, {"expires_at": iso(expires_at)} if expires_at else None)


def ban_feedback_email(pool, feedback_id: int, reason: str, actor: str) -> dict:
    """Only explicit moderation of a saved report can pause an email; it is not verified identity."""
    from newsroom.api.routes.admin_auth import BadRequest  # noqa: PLC0415

    with pool.connection() as conn:
        row = conn.execute("SELECT email FROM feedback WHERE id = %s", (feedback_id,)).fetchone()
    if not row or not (row["email"] or "").strip():
        raise BadRequest("這條反饋沒有可封禁的郵箱。")
    key = feedback_email_key(row["email"])
    expires = datetime.now(UTC) + timedelta(hours=48)
    ban_source(pool, key, reason, actor, expires)
    return {"key": key, "expiresAt": expires}


def unban_source(pool, source_hash: str, actor: str) -> None:
    with pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM feedback_submission_state WHERE source_hash = %s", (source_hash,))
        rows = conn.execute("DELETE FROM feedback_bans WHERE source_hash = %s RETURNING reason", (source_hash,)).fetchall()
        if rows:
            audit(conn, actor, "feedback.unban", f"feedback-source:{source_hash}", None, {"reason": rows[0]["reason"]}, None)


def screenshot_path(key: str | None):
    if not key or not key.startswith("local:"):
        return None
    name = key[len("local:") :]
    return config().data_dir / "feedback-screenshots" / name if re.fullmatch(r"[\w.-]+", name) else None


def feedback_screenshot(pool, feedback_id: int):
    with pool.connection() as conn:
        row = conn.execute("SELECT screenshot_key FROM feedback WHERE id = %s", (feedback_id,)).fetchone()
    return screenshot_path(row["screenshot_key"] if row else None)


def erase_feedback(pool, feedback_id: int, reason: str, actor: str) -> dict | None:
    """Removes the sender's material (text, email, page, screenshot) and keeps only the handling record."""
    if not reason.strip():
        raise Exception("reason is required")  # noqa: TRY002
    with pool.connection() as conn:
        row = conn.execute("SELECT screenshot_key FROM feedback WHERE id = %s", (feedback_id,)).fetchone()
        if not row:
            return None
        file = screenshot_path(row["screenshot_key"])
        if file:
            file.unlink(missing_ok=True)
        conn.execute(
            "UPDATE feedback SET content = '（已按要求刪除）', email = NULL, page_url = NULL, screenshot_key = NULL, updated_at = now() WHERE id = %s",
            (feedback_id,),
        )
        audit(conn, actor, "feedback.erase", f"feedback:{feedback_id}", reason, None, None)
    return {"erased": True}
