"""Feedback: content, optional email, page URL, one optional screenshot. Port of operations/feedback.ts. Abuse control uses an unreadable source
identifier (HMAC of client IP + UA family), persistent rate limits, temporary bans and exact-submission retry deduplication.

Forwarding to the internal chat is not done here: a report is saved with forward_error = 'pending', and the worker's retry job (which exists
for a chat that is down) sends it. Without that chat nothing is forwarded in the TypeScript either."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from newsroom.engine.audit import audit
from newsroom.engine.config import config, credential
from newsroom.engine.publication.v1 import iso

HOUR_MS = 3600_000
# Different reports a source may save in a rolling hour; attempts an hour, retries included, for a source and for its whole network.
MAX_SUBMISSIONS = 3
MAX_ATTEMPTS = 20


class FeedbackRejected(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


def sniff_image_type(data: bytes) -> str | None:
    """The picture's real type from its first bytes (what the browser claimed is not trusted)."""
    if data[:8] == bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if re.fullmatch(rb"GIF8[79]a", data[:6]):
        return "image/gif"
    return None


def _secret() -> str:
    return credential("auth", "SESSION_SECRET") or "dev-feedback-secret"


def _b64url(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def feedback_source_hash(ip: str, user_agent: str) -> str:
    family = "/".join(re.findall(r"(Chrome|Safari|Firefox|Edg|MicroMessenger|Mobile|Android|iPhone|iPad|Mac OS X|Windows)", user_agent)[:4])
    return _b64url(hmac.new(_secret().encode(), f"{ip}|{family}".encode(), hashlib.sha256).digest())[:24]


def feedback_email_key(email: str) -> str:
    """An explicit moderator ban can follow a supplied email without storing it in the ban/audit log."""
    return "email:" + _b64url(hmac.new(_secret().encode(), email.strip().lower().encode(), hashlib.sha256).digest())


def page_address(raw: str | None) -> str | None:
    """The page a report came from, as the admin opens it: a web address or a path on this site. A script or data address is dropped and the
    report kept; a leading "//" or "/\\" leaves the site."""
    page = (raw or "").strip()[:500]
    if not page:
        return None
    if page.startswith("/"):
        return None if re.match(r"^/[/\\]", page) else page
    try:
        return page if urlsplit(page).scheme in ("http", "https") and urlsplit(page).netloc else None
    except ValueError:
        return None


def _decodes(data: bytes) -> bool:
    """Decoding the whole picture settles it: a PNG signature followed by noise would be kept and offered again and again."""
    from PIL import Image  # noqa: PLC0415

    Image.MAX_IMAGE_PIXELS = 60_000_000
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
        return True
    except Exception:
        return False


def _expire_bans(conn, now: datetime, keys: list[str] | None = None) -> None:
    rows = conn.execute(
        "DELETE FROM feedback_bans WHERE expires_at <= %s AND (%s::text[] IS NULL OR source_hash = ANY(%s)) RETURNING source_hash, reason, expires_at",
        (now, keys, keys),
    ).fetchall()
    for row in rows:
        audit(conn, "feedback-auto", "feedback.unban", f"feedback-source:{row['source_hash']}", "臨時封禁到期",
              {"source_hash": row["source_hash"], "reason": row["reason"], "expires_at": iso(row["expires_at"])}, None)  # fmt: skip


def _active_ban(conn, keys: list[str], now: datetime) -> FeedbackRejected | None:
    ban = conn.execute(
        """SELECT expires_at FROM feedback_bans WHERE source_hash = ANY(%s) AND (expires_at IS NULL OR expires_at > %s)
           ORDER BY expires_at DESC NULLS FIRST LIMIT 1""",
        (keys, now),
    ).fetchone()
    if not ban:
        return None
    if ban["expires_at"]:
        wait = max(1, -(-int((ban["expires_at"] - now).total_seconds() * 1000) // 1000))
        return FeedbackRejected(429, "rate_limited", "反饋提交過於頻繁，已暫時暫停。請稍後再試，草稿會保留。", wait)
    return FeedbackRejected(403, "forbidden", "暫時無法提交反饋。")


def submit_feedback(pool, content: str, email: str | None, page_url: str | None, screenshot: dict | None, ip: str, user_agent: str) -> dict:
    """Saves a report and returns {"id"}; raises FeedbackRejected for what the sender can fix or must wait for."""
    content = content.strip()
    if len(content) < 2:
        raise FeedbackRejected(400, "invalid_request", "請寫下反饋內容。")
    if len(content) > 5000:
        raise FeedbackRejected(400, "invalid_request", "反饋內容最多 5000 字。")
    email = (email or "").strip() or None
    if email and (len(email) > 200 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
        raise FeedbackRejected(400, "invalid_request", "郵箱格式不正確。")
    page = page_address(page_url)
    source = feedback_source_hash(ip, user_agent)
    network = "network:" + _b64url(hmac.new(_secret().encode(), ip.encode(), hashlib.sha256).digest())
    # A ban holds under every key the source is known by.
    keys = [source, *([feedback_email_key(email)] if email else [])]
    with pool.connection() as conn:
        banned = _active_ban(conn, keys, datetime.now(UTC))
    if banned:
        raise banned
    mime = None
    if screenshot:
        # Some phones send JPEGs as image/jpg or with no type at all: the bytes decide.
        mime = sniff_image_type(screenshot["data"])
        if not mime:
            raise FeedbackRejected(400, "invalid_request", "截圖需要是 PNG、JPG、WebP 或 GIF。")
        if len(screenshot["data"]) > 8 * 1024 * 1024:
            raise FeedbackRejected(400, "invalid_request", "截圖最大 8MB。")
        if not _decodes(screenshot["data"]):
            raise FeedbackRejected(400, "invalid_request", "截圖無法識別，請換一張圖片。")
    # Preserve code and whitespace inside reports. Changed contact, page or screenshot is a new report.
    shot_hash = hashlib.sha256(screenshot["data"]).hexdigest() if screenshot else None
    fingerprint = hashlib.sha256(json.dumps([content, email, page, shot_hash], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    outcome = _decide(pool, source, network, keys, fingerprint, content, email, page, screenshot, mime)
    # Rejections are raised after commit so their counters and temporary bans survive.
    if outcome.get("rejected"):
        raise outcome["rejected"]
    return {"id": outcome["id"]}


def _decide(pool, source, network, keys, fingerprint, content, email, page, screenshot, mime) -> dict:
    out: dict = {}
    try:
        _decide_in_transaction(pool, source, network, keys, fingerprint, content, email, page, screenshot, mime, out)
    except Exception:
        if out.get("file"):
            out["file"].unlink(missing_ok=True)
        raise
    return out


def _decide_in_transaction(pool, source, network, keys, fingerprint, content, email, page, screenshot, mime, out: dict) -> dict:
    with pool.connection() as conn, conn.transaction():
        expires = datetime.now(UTC) + timedelta(hours=1)
        # Lock the shared network before the browser source in every API process.
        net = conn.execute(
            """INSERT INTO feedback_submission_state (source_hash, expires_at) VALUES (%s, %s)
               ON CONFLICT (source_hash) DO UPDATE SET expires_at = EXCLUDED.expires_at RETURNING attempts""",
            (network, expires),
        ).fetchone()
        state = conn.execute(
            """INSERT INTO feedback_submission_state (source_hash, expires_at) VALUES (%s, %s)
               ON CONFLICT (source_hash) DO UPDATE SET expires_at = EXCLUDED.expires_at RETURNING attempts, submissions""",
            (source, expires),
        ).fetchone()
        now = int(time.time() * 1000)
        ban = _active_ban(conn, keys, datetime.fromtimestamp(now / 1000, UTC))
        if ban:
            out["rejected"] = ban
            return out
        _expire_bans(conn, datetime.fromtimestamp(now / 1000, UTC), keys)
        attempts = [t for t in state["attempts"] if t > now - HOUR_MS]
        submissions = [s for s in state["submissions"] if s["at"] > now - HOUR_MS]
        duplicate = next((s for s in submissions if s["fingerprint"] == fingerprint), None)
        # A lost response or a few repeated clicks are safe retries. A continuing replay is still abuse.
        if len(attempts) >= MAX_ATTEMPTS or (not duplicate and len(submissions) >= MAX_SUBMISSIONS):
            until = datetime.fromtimestamp((now + HOUR_MS) / 1000, UTC)
            reason = "持續重複提交反饋" if duplicate else "一小時內持續提交反饋"
            conn.execute(
                "INSERT INTO feedback_bans (source_hash, reason, created_by, expires_at) VALUES (%s, %s, 'feedback-auto', %s) ON CONFLICT (source_hash) DO NOTHING",
                (source, reason, until),
            )
            audit(conn, "feedback-auto", "feedback.ban", f"feedback-source:{source}", reason, None, {"expires_at": iso(until)})
            out["rejected"] = FeedbackRejected(429, "rate_limited", "反饋提交過於頻繁，已暫停 1 小時。請稍後再試，草稿會保留。", 3600)
            return out
        # A shared quota throttles rotating UA families without banning the whole network.
        network_attempts = [t for t in net["attempts"] if t > now - HOUR_MS]
        if len(network_attempts) >= MAX_ATTEMPTS:
            wait = max(1, -(-(network_attempts[0] + HOUR_MS - now) // 1000))
            out["rejected"] = FeedbackRejected(429, "rate_limited", "同一網路反饋提交過於頻繁。請稍後再試，草稿會保留。", wait)
            return out
        network_attempts.append(now)
        conn.execute("UPDATE feedback_submission_state SET attempts = %s WHERE source_hash = %s", (Jsonb(network_attempts), network))
        attempts.append(now)
        conn.execute(
            "UPDATE feedback_submission_state SET attempts = %s, submissions = %s, expires_at = %s WHERE source_hash = %s",
            (Jsonb(attempts), Jsonb(submissions), datetime.fromtimestamp((now + HOUR_MS) / 1000, UTC), source),
        )
        if duplicate:
            out["id"] = duplicate["id"]
            return out
        screenshot_key = None
        if screenshot and mime:
            name = f"{uuid.uuid4()}.{mime.split('/')[1]}"
            directory = config().data_dir / "feedback-screenshots"
            directory.mkdir(parents=True, exist_ok=True)
            out["file"] = directory / name
            out["file"].write_bytes(screenshot["data"])
            screenshot_key = f"local:{name}"
        row = conn.execute(
            """INSERT INTO feedback (content, email, page_url, screenshot_key, source_hash, forward_error)
               VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id""",
            (content, email, page, screenshot_key, source),
        ).fetchone()
        submissions.append({"id": row["id"], "at": now, "fingerprint": fingerprint})
        conn.execute("UPDATE feedback_submission_state SET submissions = %s WHERE source_hash = %s", (Jsonb(submissions), source))
        out["id"] = row["id"]
    return out
