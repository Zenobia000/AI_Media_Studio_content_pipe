"""Jina Reader (r.jina.ai): browser-rendered page text. Paid per request, reached through the egress proxy, always behind receipts and the per-minute/hour/day budget
(any zero stops it). Port of providers/jina.ts."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from newsroom.engine.config import credential
from newsroom.engine.lib.http_fetch import guarded_fetch
from newsroom.engine.lib.page_content import UnavailablePageError, assert_page_content, content_document
from newsroom.engine.lib.text import js_slice
from newsroom.engine.receipts import CallOutcome, ReceiptRequest, assert_accepted, paid_request

JINA_PREFIX = "https://r.jina.ai/"  # a source address read through Jina: this prefix, then the page's own address
CONTENT = "\nMarkdown Content:\n"  # Jina's answer starts with header lines (Title, URL Source, Published Time); the page follows this line


def jina_read(
    pool,
    target_url: str,
    purpose: str,
    subject: str,
    *,
    fmt: str | None = None,
    cache_tolerance_seconds: int | None = None,
    per_read: bool = False,
    round_: str | None = None,
) -> dict:
    """Reads a page through Jina: `raw` is the whole answer, `markdown` the page after its header lines. The receipt key is the target URL plus the day, so a same-day
    retry of an article's body or detail reuses it. A listing is read afresh on every fetch (`per_read`); a caller that reads on rounds of its own keys the read by its
    `round_`: a retry of the round reuses it, the next round reads afresh."""
    key = credential("collectors", "JINA_API_KEY")
    if not key:
        raise RuntimeError("JINA_API_KEY is not configured")
    base = (credential("collectors", "JINA_BASE_URL") or "https://r.jina.ai").rstrip("/")
    # A listing whose freshness matters caps how old Jina's cached rendering may be.
    valid = isinstance(cache_tolerance_seconds, int) and not isinstance(cache_tolerance_seconds, bool) and cache_tolerance_seconds >= 0
    tolerance = {"x-cache-tolerance": str(cache_tolerance_seconds)} if valid else {}
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    read = (
        {"round": round_} if round_ else {"read": str(uuid.uuid4())} if per_read else {}
    )  # a per-read listing is keyed by an ID of its own, not the clock
    format_ = fmt or "markdown"

    def call(permit=None) -> CallOutcome:
        res = guarded_fetch(
            f"{base}/{target_url}", headers={"authorization": f"Bearer {key}", "x-return-format": format_, "accept": "text/plain", **tolerance}, timeout_ms=60_000, max_bytes=6 * 1024 * 1024
        )  # fmt: skip
        text = res.text()
        assert_accepted("jina", res.status, text)
        # Billed in tokens; estimated at about 0.36 yuan per million. Without the usage header nothing is guessed.
        try:
            tokens = float(res.headers.get("x-usage-tokens") or 0) or None
            tokens = int(tokens) if tokens is not None and tokens.is_integer() else tokens
        except ValueError:
            tokens = None
        cost = {"amount": tokens / 1e6 * 0.36, "currency": "CNY", "basis": "estimated"} if tokens else None
        return CallOutcome(
            response={"text": js_slice(text, 0, 2_000_000), "status": res.status},
            request_id=res.headers.get("x-request-id"),
            usage={"bytes": len(res.body), "tokens": tokens},
            cost=cost,
        )

    receipt = paid_request(
        pool, ReceiptRequest(service="jina", model=None, purpose=purpose, subject=subject, identity={"url": target_url, "day": day, "format": format_, **read}, request_summary={"url": target_url}), call
    )  # fmt: skip
    raw = str((receipt["response"] or {}).get("text") or "")
    at = raw.find(CONTENT)
    markdown = (raw if at < 0 else raw[at + len(CONTENT) :]).strip()
    # The renderer has been paid even when the target failed. Validate after saving its receipt, and only read protocol warnings before Markdown Content (an article may
    # quote the same words).
    if fmt == "html":
        content_document(raw)
    else:
        header = "" if at < 0 else raw[:at]
        failure = re.search(r"^Warning:.*(?:Target URL returned error [45]\d\d\b|requiring CAPTCHA).*$", header, re.I | re.M)
        if failure:
            raise UnavailablePageError(failure.group(0))
        title = re.search(r"^Title:\s*(.*)$", header, re.I | re.M)
        assert_page_content(title.group(1) if title else "", markdown)
    return {"markdown": markdown, "raw": raw, "receiptId": receipt["receiptId"]}
