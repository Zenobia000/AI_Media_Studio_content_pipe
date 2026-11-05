"""Content-group deliveries (Feishu custom-bot webhooks), such as the cards of selected items. One row per target and dedupe key, so nothing is pushed twice; an outcome
we cannot know ("unknown") is never retried automatically; content older than a target's enabled_at is never back-filled. FEISHU_CONTENT_PUSH_ENABLED is the safety valve:
off, deliveries are recorded as skipped and nothing leaves the process. Port of notify/deliver.ts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from newsroom.engine.audit import Conflict, audit
from newsroom.engine.config import config, credential
from newsroom.engine.lib.text import js_slice
from newsroom.engine.notify_feishu import post_webhook
from newsroom.engine.notify_selected_content import selected_content


def ensure_content_targets(pool) -> None:
    """Default content targets; they start disabled and are switched on in production only."""
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO notify_targets (key, purpose, kind, enabled, config_ref, note) VALUES
                 ('feishu-content-main', 'content', 'feishu_webhook', false, 'FEISHU_PUSH_WEBHOOK_URL', '飛書內容主群'),
                 ('feishu-content-mirror', 'content', 'feishu_webhook', false, 'FEISHU_PUSH_MIRROR_WEBHOOK_URL', '飛書內容映象群')
               ON CONFLICT (key) DO NOTHING"""
        )


def _send_delivery(pool, delivery_id: int, url: str, card) -> dict:
    """The acknowledgement and stored outcome follow the same rules for first sends and recovery."""
    try:
        result = post_webhook(url, card)
    except Exception as error:
        # A timeout or lost connection may still have delivered. Never retry it automatically.
        result = {"status": "unknown", "body": js_slice(str(error), 0, 500)}
    with pool.connection() as conn:
        conn.execute(
            "UPDATE deliveries SET status = %s, response = %s, sent_at = %s, updated_at = now() WHERE id = %s",
            (result["status"], result["body"], datetime.now(UTC) if result["status"] == "sent" else None, delivery_id),
        )
    return {"status": result["status"]}


def deliver_content(pool, req: dict) -> list[dict]:
    """req: subjectKind, subjectId, dedupeKey, contentAt, card, targetKey?, siblings?"""
    # A selected article may acquire a different fact after correction. Its own previous delivery still counts, even when the dedupe key changes. Other kinds of push
    # keep their own keys.
    sent_subjects = [req["subjectId"], *(req.get("siblings") or [])] if req["subjectKind"] == "selected" else list(req.get("siblings") or [])
    with pool.connection() as conn:
        targets = conn.execute(
            "SELECT key FROM notify_targets WHERE purpose = 'content' AND enabled AND (%s::text IS NULL OR key = %s) ORDER BY key",
            (req.get("targetKey"), req.get("targetKey")),
        ).fetchall()
    results: list[dict] = []
    for target in targets:
        # The preceding target can take seconds to answer. Re-read before each send so a withdrawal, silence or correction reaches mirrors as well as the first group.
        current = selected_content(pool, req["subjectId"]) if req["subjectKind"] == "selected" else None
        if current and current["status"] != "ready":
            break
        card = current["card"] if current else req["card"]
        with pool.connection() as conn, conn.transaction():
            # Reserve a target in a short transaction: concurrent siblings must see each other's pending delivery, even before either network request starts. No lock
            # is held while sending.
            t = conn.execute(
                "SELECT key, kind, enabled_at, config_ref FROM notify_targets WHERE key = %s AND purpose = 'content' AND enabled FOR UPDATE",
                (target["key"],),
            ).fetchone()
            claim = None
            if t and not (t["enabled_at"] and req["contentAt"] < t["enabled_at"]):
                told = (
                    conn.execute(
                        """SELECT 1 FROM deliveries WHERE target_key = %s AND subject_kind = %s AND subject_id = ANY(%s::text[])
                           AND status IN ('pending', 'sent', 'unknown', 'sending') LIMIT 1""",
                        (t["key"], req["subjectKind"], sent_subjects),
                    ).fetchone()
                    if sent_subjects
                    else None
                )
                if not told:
                    row = conn.execute(
                        """INSERT INTO deliveries (target_key, subject_kind, subject_id, dedupe_key, status, payload) VALUES (%s, %s, %s, %s, 'pending', %s)
                           ON CONFLICT (target_key, dedupe_key) DO NOTHING RETURNING id""",
                        (t["key"], req["subjectKind"], req["subjectId"], req["dedupeKey"], Jsonb(card)),
                    ).fetchone()
                    claim = {"id": row["id"], "target": t} if row else None
        if not claim:
            continue
        did, t = claim["id"], claim["target"]
        if not config().feishu_content_push_enabled or t["kind"] != "feishu_webhook":
            with pool.connection() as conn:
                conn.execute("UPDATE deliveries SET status = 'skipped', response = 'content push disabled', updated_at = now() WHERE id = %s", (did,))
            results.append({"target": t["key"], "status": "skipped"})
            continue
        url = credential("integrations", t["config_ref"]) if t["config_ref"] else None
        if not url:
            with pool.connection() as conn:
                conn.execute("UPDATE deliveries SET status = 'failed', response = 'webhook not configured', updated_at = now() WHERE id = %s", (did,))
            results.append({"target": t["key"], "status": "failed"})
            continue
        with pool.connection() as conn:
            conn.execute("UPDATE deliveries SET status = 'sending', attempts = attempts + 1, updated_at = now() WHERE id = %s", (did,))
        results.append({"target": t["key"], **_send_delivery(pool, did, url, card)})
    return results


def resend_delivery(pool, delivery_id: int, version: str | None = None) -> dict:
    """Sends a stored delivery again after an operator checked the group and found it missing. Only for deliveries in doubt or definitely failed; the safety valve still
    applies."""
    with pool.connection() as conn:
        d = conn.execute(
            """SELECT d.status, d.updated_at::text AS version, d.payload, d.target_key, t.config_ref, t.kind, t.enabled, t.enabled_at, d.subject_kind, d.subject_id
               FROM deliveries d JOIN notify_targets t ON t.key = d.target_key WHERE d.id = %s""",
            (delivery_id,),
        ).fetchone()
    if not d:
        raise RuntimeError(f"delivery {delivery_id} not found")
    if d["status"] not in ("unknown", "failed"):
        raise Conflict("這條投遞不需要處理")
    if not config().feishu_content_push_enabled or d["kind"] != "feishu_webhook":
        raise RuntimeError("content push is disabled in this environment")
    if not d["enabled"]:
        raise Conflict("這個推送群已停用")
    current = selected_content(pool, d["subject_id"]) if d["subject_kind"] == "selected" else None
    if current and (current["status"] != "ready" or (d["enabled_at"] and current["article"]["discovered_at"] < d["enabled_at"])):
        raise Conflict("這條內容已不再符合推送條件")
    payload = current["card"] if current else d["payload"]
    url = credential("integrations", d["config_ref"]) if d["config_ref"] else None
    if not url:
        raise RuntimeError("webhook not configured")
    # Keep PostgreSQL's timestamp precision: a retry may already have failed again by the time this update runs. Only the request that claims the version the operator
    # read may send the card.
    with pool.connection() as conn:
        claimed = conn.execute(
            """UPDATE deliveries SET status = 'sending', attempts = attempts + 1, payload = %s, updated_at = now()
               WHERE id = %s AND status IN ('unknown', 'failed') AND updated_at::text = %s""",
            (Jsonb(payload), delivery_id, version or d["version"]),
        ).rowcount
    if not claimed:
        raise Conflict("這條投遞已被其他操作處理，請重新整理後重試")
    return _send_delivery(pool, delivery_id, url, payload)


def mark_stale_deliveries(pool) -> dict:
    """Deliveries a stopped process left half way: "sending" may have reached the group, so it becomes "unknown" (alerted, resolved in the admin); "pending" never left,
    so it becomes "failed" (the admin can send it). Nothing is re-sent automatically."""
    cutoff = datetime.now(UTC) - timedelta(minutes=15)
    with pool.connection() as conn:
        unknown = conn.execute(
            "UPDATE deliveries SET status = 'unknown', response = coalesce(response, '傳送中程序中斷，是否送達未知'), updated_at = now() WHERE status = 'sending' AND updated_at < %s",
            (cutoff,),
        ).rowcount
        failed = conn.execute(
            "UPDATE deliveries SET status = 'failed', response = coalesce(response, '傳送前程序中斷，沒有發出'), updated_at = now() WHERE status = 'pending' AND updated_at < %s",
            (cutoff,),
        ).rowcount
    return {"unknown": unknown, "failed": failed}


def resolve_delivery(pool, delivery_id: int, inp: dict, actor: str) -> dict | None:
    """An in-doubt delivery: confirmed as arrived, given up, or sent again after checking the group."""
    outcome, note = inp.get("outcome"), inp.get("note")
    note = note.strip() if isinstance(note, str) else ""
    if outcome not in ("sent", "drop", "resend") or not note:
        raise ValueError("outcome must be sent, drop or resend, with a note")
    with pool.connection() as conn:
        before = conn.execute("SELECT status, updated_at::text AS version FROM deliveries WHERE id = %s", (delivery_id,)).fetchone()
    if not before:
        return None
    if before["status"] not in ("unknown", "failed"):
        raise Conflict("這條投遞不需要處理")
    if outcome in ("sent", "drop"):
        sent = outcome == "sent"
        with pool.connection() as conn:
            changed = conn.execute(
                f"""UPDATE deliveries SET status = %s, {"sent_at = coalesce(sent_at, now())," if sent else ""} response = %s, updated_at = now()
                    WHERE id = %s AND status IN ('unknown', 'failed') AND updated_at::text = %s""",
                ("sent" if sent else "failed", f"人工確認已送達：{note}" if sent else f"人工放棄：{note}", delivery_id, before["version"]),
            ).rowcount
        if not changed:
            raise Conflict("這條投遞已被其他操作處理，請重新整理後重試")
        status = "sent" if sent else "failed"
    else:
        status = resend_delivery(pool, delivery_id, before["version"])["status"]
    with pool.connection() as conn:
        audit(conn, actor, f"delivery.{outcome}", f"delivery:{delivery_id}", note, {"status": before["status"]}, {"status": status})
    return {"id": delivery_id, "status": status}
