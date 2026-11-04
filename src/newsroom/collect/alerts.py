"""The S-tier alert (COL-FR-018): a candidate that reaches S for the first time is told once, with its title, how many independent sources
carry it and where to find it in the inbox. Delivery is core.notify.send_alert (off unless FEISHU_INTERNAL_ENABLED, then it only logs); the
candidate is marked either way, so turning the channel on later does not replay old news."""

from __future__ import annotations

import json
import logging

from newsroom.collect.inbox import Candidate
from newsroom.core.notify import SendAlert, send_alert

log = logging.getLogger("newsroom.alerts")


def alert_text(c: Candidate, inbox_file: str) -> tuple[str, list[str]]:
    lines = [f"共振來源 {c.participant_count} 個：{'、'.join(c.source_names)}"]
    if c.heat_reason:
        lines.append(f"為什麼是 S：{c.heat_reason}")
    lines.append(f"建議：{c.suggested_program}／{'、'.join(c.suggested_formats)}")
    lines.append(f"inbox：{inbox_file}（{c.id}）")
    return f"🔥 S 級選題：{c.title}", lines


def notify_new_s(conn, listed: list[Candidate], inbox_file: str, send: SendAlert = send_alert) -> list[str]:
    """Tells each S candidate not told before, once. Returns the ids told (or logged, with the channel off)."""
    hot = {c.id: c for c in listed if c.tier == "S" and not c.carried}
    if not hot:
        return []
    # Claim first so two overlapping rounds cannot both tell it; a failed send gives the claim back.
    claimed = conn.execute(
        """UPDATE candidate_state SET s_notified_at = now()
           WHERE candidate_id = ANY(%s) AND s_notified_at IS NULL AND status = 'new' RETURNING candidate_id""",
        (list(hot),),
    ).fetchall()
    told: list[str] = []
    for row in claimed:
        cid = row["candidate_id"]
        try:
            title, lines = alert_text(hot[cid], inbox_file)
            send(title, lines)
            told.append(cid)
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE candidate_state SET s_notified_at = NULL WHERE candidate_id = %s", (cid,))
            log.warning(json.dumps({"msg": "S-tier alert not sent; retried next round", "candidate": cid, "error": str(e)}, ensure_ascii=False))
    return told
