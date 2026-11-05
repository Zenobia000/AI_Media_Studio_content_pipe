"""Feishu delivery. Two separate apps: the login app (admin OAuth) and the message app (internal feedback chat, operations alert chat, image upload). Content groups use
custom bot webhooks, and so do alerts when FEISHU_ALERT_WEBHOOK_URL is set (they are plain text; feedback screenshots need the app). Alerts and feedback never go to content
groups, and content never goes to internal chats. What the owner is told (send_alert) goes first to Agent 自動化中臺, which passes it on. Everything outward is off unless
explicitly enabled (development and tests stay silent). Port of notify/feishu.ts."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from newsroom.engine.config import config, credential
from newsroom.engine.lib.text import js_slice
from newsroom.engine.time import beijing_stamp

MARK = {"now": "🔴", "today": "🟠"}


def api_base() -> str:
    return os.environ.get("FEISHU_API_BASE") or "https://open.feishu.cn/open-apis"


def feishu_internal_enabled() -> bool:
    return os.environ.get("FEISHU_INTERNAL_ENABLED") == "true"


_token: dict = {"token": None, "expires": 0.0}


def tenant_token() -> str:
    if _token["token"] and _token["expires"] > time.time() + 60:
        return _token["token"]
    app_id, secret = credential("integrations", "FEISHU_APP_ID"), credential("integrations", "FEISHU_APP_SECRET")
    if not app_id or not secret:
        raise RuntimeError("Feishu message app is not configured")
    res = httpx.post(f"{api_base()}/auth/v3/tenant_access_token/internal", json={"app_id": app_id, "app_secret": secret}, timeout=15)
    data = res.json()
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"feishu token: {data.get('msg')}")
    _token.update(token=data["tenant_access_token"], expires=time.time() + (data.get("expire") or 3600))
    return _token["token"]


IMAGE_REFUSED = {234001, 234006, 234010, 234011}  # Feishu's answers that the picture itself is unacceptable: trying again cannot help


class ImageRefusedError(Exception):
    pass


def upload_image(data: bytes, filename: str) -> str:
    res = httpx.post(
        f"{api_base()}/im/v1/images",
        headers={"authorization": f"Bearer {tenant_token()}"},
        data={"image_type": "message"},
        files={"image": (filename, data)},
        timeout=30,
    )
    body = res.json()
    if body.get("code") in IMAGE_REFUSED:
        raise ImageRefusedError(f"feishu upload: {body.get('msg')}")
    if body.get("code") != 0 or not body.get("data"):
        raise RuntimeError(f"feishu upload: {body.get('msg')}")
    return body["data"]["image_key"]


def send_to_chat(chat_id: str, msg_type: str, content) -> str:
    res = httpx.post(
        f"{api_base()}/im/v1/messages?receive_id_type=chat_id", headers={"authorization": f"Bearer {tenant_token()}"},
        content=json.dumps({"receive_id": chat_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False, separators=(",", ":"))}, ensure_ascii=False, separators=(",", ":")).encode(),
        timeout=20,
    )  # fmt: skip
    body = res.json()
    if body.get("code") != 0:
        raise RuntimeError(f"feishu send: {body.get('msg')}")
    return (body.get("data") or {}).get("message_id") or ""


@dataclass
class Finding:
    """What the owner is told about an open problem (operations alerts): plain words, no queue, unit or table names."""

    key: str
    level: str  # now | today | later
    title: str
    impact: str | None = None
    heals: str | None = None
    action: str | None = None
    detail: str | None = None
    since: datetime | None = None
    observed_at: datetime | None = None
    owner: object = None  # True, "only" or None
    agent_only: bool = False
    agent_type: str | None = None
    extra: dict = field(default_factory=dict)


def duration(ms: float) -> str:
    minutes = max(1, int(ms / 60_000 + 0.5))
    if minutes < 60:
        return f"{minutes} 分鐘"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小時" + (f" {minutes % 60} 分鐘" if minutes % 60 else "")
    return f"{minutes // 1440} 天"


def format_alert(f: Finding, since: datetime, now_ms: float, repeat: bool = False) -> dict:
    """The message for an open problem: first notice or a repeat."""
    level = "today" if f.level == "later" else f.level
    elapsed = now_ms - since.timestamp() * 1000
    lasting = f"（已持續 {duration(elapsed)}）" if elapsed >= 60_000 else ""
    lines = [f"影響：{f.impact}" if f.impact else None, f"會自己好嗎：{f.heals}" if f.heals else None, f"你需要：{f.action}" if f.action else None]
    return {"title": f"{MARK[level]} {'仍未恢復：' if repeat else ''}{f.title}{lasting}", "lines": [x for x in lines if x]}


def format_return(f: Finding, count: int) -> dict:
    """A problem handed to the agent that keeps coming back: told once, with no recovery to follow."""
    level = "today" if f.level == "later" else f.level
    lines = [
        f"影響：{f.impact}" if f.impact else None,
        "會自己好嗎：每次都交給 AI 處理好了，但又出現，說明還沒修到根上",
        "你需要：讓 AI 查清它為什麼反覆出現，不只處理這一次",
    ]
    return {"title": f"{MARK[level]} 反覆出現：{f.title}（一天內第 {count} 次）", "lines": [x for x in lines if x]}


def format_recovery(title: str, since: datetime, now_ms: float) -> dict:
    return {"title": f"✅ 已恢復：{title}", "lines": [f"持續 {duration(now_ms - since.timestamp() * 1000)}（{beijing_stamp(since)} 起）"]}


WEBHOOK_GAP = 0.6
WEBHOOK_TEXT_BYTES = 18_000
_webhook_lock = threading.Lock()
_webhook_last = [-1e18]


def webhook_turn(send):
    """One request at a time, sent the gap after the last one was answered or failed: Feishu got that one before it answered, so however late a timer, a request or an
    answer, it never gets two closer than the gap."""
    with _webhook_lock:
        wait = _webhook_last[0] + WEBHOOK_GAP - time.monotonic()
        while wait > 0:
            time.sleep(wait)
            wait = _webhook_last[0] + WEBHOOK_GAP - time.monotonic()
        try:
            return send()
        finally:
            _webhook_last[0] = time.monotonic()


def _json_bytes(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=False).encode()) - 2


def webhook_pieces(text: str) -> list[str]:
    """The text in pieces that fit a request, cut after a line where one fits; joined they are the text."""
    pieces: list[str] = []
    rest = text
    while _json_bytes(rest) > WEBHOOK_TEXT_BYTES:
        end = used = 0
        for ch in rest:
            used += _json_bytes(ch)
            if used > WEBHOOK_TEXT_BYTES:
                break
            end += len(ch)
        line = rest.rfind("\n", 0, end)
        if line > 0:
            end = line + 1
        pieces.append(rest[:end])
        rest = rest[end:]
    pieces.append(rest)
    return pieces


def told_through_hub(pieces: list[str]) -> list[str]:
    """Hands the pieces to the hub's /tell with the source key, each under its own message key. The pieces it didn't take, from the first it refused or couldn't be
    reached for, which go to the group instead."""
    hub, key = credential("integrations", "WEBHOOK_HUB_URL"), credential("integrations", "WEBHOOK_HUB_KEY")
    if not hub or not key:
        return pieces
    from urllib.parse import urljoin

    ident = f"alert-{int(time.time() * 1000)}-{hashlib.sha256(''.join(pieces).encode()).hexdigest()[:12]}"
    for i, piece in enumerate(pieces):
        try:
            res = httpx.post(
                urljoin(hub, "/tell"), headers={"authorization": f"Bearer {key}", "content-type": "application/json"}, follow_redirects=False,
                content=json.dumps({"key": f"{ident}-{i}", "text": piece}, ensure_ascii=False, separators=(",", ":")).encode(), timeout=15,
            )  # fmt: skip
            if res.status_code != 200:
                raise RuntimeError(f"HTTP {res.status_code}")
        except Exception as error:
            print(
                json.dumps({"level": "warn", "msg": "alert not taken by the hub; the rest sent to the alert group", "taken": i, "error": str(error)})
            )
            return pieces[i:]
    return []


def send_alert(title: str, lines: list[str]) -> str:
    """What the owner is told: through Agent 自動化中臺 when the hub is set up; when it isn't, or doesn't take it, the alert group's bot webhook, else the alert chat,
    falling back to the internal feedback chat - never a content group."""
    env = config().environment_name
    text = f"{'' if env == 'production' else f'【{env}】'}{title}\n" + "\n".join(lines)
    if not feishu_internal_enabled():
        print(
            json.dumps(
                {"level": "warn", "msg": "alert (not sent: FEISHU_INTERNAL_ENABLED is off)", "title": title, "lines": lines}, ensure_ascii=False
            )
        )
        return "disabled"
    rest = told_through_hub(webhook_pieces(text))
    if not rest:
        return "sent"
    webhook = credential("integrations", "FEISHU_ALERT_WEBHOOK_URL")
    if webhook:
        for piece in rest:
            result = webhook_turn(lambda piece=piece: send_webhook(webhook, {"msg_type": "text", "content": {"text": piece}}))
            if result["status"] != "sent":
                raise RuntimeError(f"feishu alert webhook {result['status']}: {result['body']}")
        return "sent"
    chat = credential("integrations", "FEISHU_ALERT_CHAT_ID") or credential("integrations", "FEISHU_INTERNAL_CHAT_ID")
    if not chat:
        return "disabled"
    send_to_chat(chat, "text", {"text": "".join(rest)})
    return "sent"


SCREENSHOT_GIVE_UP = 24 * 3600  # a screenshot that still cannot be uploaded this long after the feedback is given up: the text goes without it


def _screenshot_for(pool, fb: dict) -> dict:
    """The screenshot to attach, uploaded now or on an earlier try. Only the Feishu image key is kept: the local file is removed once uploaded, or once the upload is
    given up."""
    key = fb["screenshot_key"]
    if key and key.startswith("feishu:"):
        return {"imageKey": key[len("feishu:") :], "note": None}
    if key == "gone:upload":
        return {"imageKey": None, "note": "（截圖未能上傳，已刪除）"}
    if key == "gone:missing":
        return {"imageKey": None, "note": "（截圖檔案已不存在）"}
    if not key or not key.startswith("local:"):
        return {"imageKey": None, "note": None}
    file = config().data_dir / "feedback-screenshots" / key[len("local:") :]
    try:
        data = file.read_bytes()
    except OSError:
        with pool.connection() as conn:
            conn.execute("UPDATE feedback SET screenshot_key = 'gone:missing' WHERE id = %s", (fb["id"],))
        return {"imageKey": None, "note": "（截圖檔案已不存在）"}
    try:
        image_key = upload_image(data, Path(file).name)
        with pool.connection() as conn:
            conn.execute("UPDATE feedback SET screenshot_key = %s WHERE id = %s", (f"feishu:{image_key}", fb["id"]))
        file.unlink(missing_ok=True)
        return {"imageKey": image_key, "note": None}
    except Exception as error:
        # The forwarding sweep tries again; after a day, or at once when Feishu refuses the picture itself, the text goes without it.
        if not isinstance(error, ImageRefusedError) and time.time() - fb["created_at"].timestamp() < SCREENSHOT_GIVE_UP:
            raise
        with pool.connection() as conn:
            conn.execute("UPDATE feedback SET screenshot_key = 'gone:upload' WHERE id = %s", (fb["id"],))
        file.unlink(missing_ok=True)
        return {"imageKey": None, "note": "（截圖未能上傳，已刪除）"}


def forward_feedback_to_feishu(pool, feedback_id: int) -> str:
    """Forwards one feedback to the internal chat, with its screenshot. Until that succeeds the feedback keeps the reason in forward_error, and the forwarding sweep
    tries it again."""
    if not feishu_internal_enabled():
        return "disabled"
    chat = credential("integrations", "FEISHU_INTERNAL_CHAT_ID")
    if not chat:
        return "disabled"
    with pool.connection() as conn:
        fb = conn.execute(
            "SELECT id, content, email, note, page_url, screenshot_key, created_at FROM feedback WHERE id = %s AND forwarded_at IS NULL",
            (feedback_id,),
        ).fetchone()
    if not fb:
        return "disabled"
    try:
        shot = _screenshot_for(pool, fb)
        paragraphs: list = [
            [{"tag": "text", "text": fb["content"]}],
            [{"tag": "text", "text": f"頁面：{fb['page_url'] or '—'}"}],
            [{"tag": "text", "text": f"郵箱：{fb['email'] or '（未留）'}"}],
        ]
        if fb["note"]:
            paragraphs.append([{"tag": "text", "text": fb["note"]}])
        if shot["imageKey"]:
            paragraphs.append([{"tag": "img", "image_key": shot["imageKey"]}])
        elif shot["note"]:
            paragraphs.append([{"tag": "text", "text": shot["note"]}])
        send_to_chat(chat, "post", {"zh_cn": {"title": f"反饋 #{fb['id']}", "content": paragraphs}})
        with pool.connection() as conn:
            conn.execute("UPDATE feedback SET forwarded_at = now(), forward_error = NULL WHERE id = %s", (feedback_id,))
        return "sent"
    except Exception as error:
        with pool.connection() as conn:
            conn.execute("UPDATE feedback SET forward_error = %s WHERE id = %s", (js_slice(str(error), 0, 300), feedback_id))
        raise


def post_webhook(url: str, card) -> dict:
    """Custom-bot webhook for content groups (selected cards and other pushes)."""
    return send_webhook(url, {"msg_type": "interactive", "card": card})


def send_webhook(url: str, message) -> dict:
    res = httpx.post(
        url, content=json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(), headers={"content-type": "application/json"}, timeout=15
    )
    body = res.text
    # A successful HTTP transport is not an acknowledgement: proxies can return HTML or empty JSON.
    status = "failed" if 400 <= res.status_code < 500 else "unknown"
    try:
        data = json.loads(body)
        code = None
        if isinstance(data, dict):
            code = data.get("code") if data.get("code") is not None else data.get("StatusCode")
        if res.is_success and isinstance(code, (int, float)) and not isinstance(code, bool):
            status = "sent" if code == 0 else "failed"
    except ValueError:
        pass  # non-JSON body
    return {"status": status, "body": js_slice(body, 0, 500)}
