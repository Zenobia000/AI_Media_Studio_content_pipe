"""What the owner is told. Off unless FEISHU_INTERNAL_ENABLED=true (development and tests stay silent: the message is only logged).
Goes to the alert group's bot webhook (FEISHU_ALERT_WEBHOOK_URL). The operations hub (OPS-FR-003, 楊舒媁) is not wired in yet."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable

import httpx

from newsroom.core.config import credential, flag

log = logging.getLogger("newsroom.notify")

SendAlert = Callable[[str, list[str]], str]


def send_alert(title: str, lines: list[str]) -> str:
    """Returns "sent" or "disabled"; raises when the channel is on and the send fails."""
    env_name = os.environ.get("AIHOT_ENVIRONMENT", "development")
    text = ("" if env_name == "production" else f"【{env_name}】") + title + "\n" + "\n".join(lines)
    webhook = credential("integrations", "FEISHU_ALERT_WEBHOOK_URL")
    if not flag("FEISHU_INTERNAL_ENABLED") or not webhook:
        log.warning(json.dumps({"msg": "alert (not sent: channel is off)", "title": title, "lines": lines}, ensure_ascii=False))
        return "disabled"
    with httpx.Client(timeout=15.0) as client:
        res = client.post(webhook, json={"msg_type": "text", "content": {"text": text}})
    try:
        code = res.json().get("code", res.json().get("StatusCode"))
    except ValueError:
        code = None
    if res.status_code >= 400 or (code is not None and code != 0):
        raise RuntimeError(f"feishu alert webhook failed: HTTP {res.status_code} {res.text[:200]}")
    return "sent"
