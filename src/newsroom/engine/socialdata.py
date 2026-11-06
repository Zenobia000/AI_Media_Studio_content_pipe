"""SocialData (X search). Paid per request: every call goes through receipts and the budget. Port of providers/socialdata.ts."""

from __future__ import annotations

import json
import re
from typing import cast
from urllib.parse import quote, quote_plus, urlencode

from newsroom.engine.config import credential
from newsroom.engine.lib.http_fetch import guarded_fetch
from newsroom.engine.receipts import CallOutcome, ProviderRejectedError, ReceiptRequest, assert_accepted, paid_request


def objects_cost(objects: int) -> dict:
    """SocialData bills US$0.20 per 1,000 objects returned by a successful response. Recorded per call as an estimate."""
    return {"amount": objects * 0.0002, "currency": "USD", "basis": "estimated"}


def _form_quote(value, safe="", encoding=None, errors=None) -> str:
    """application/x-www-form-urlencoded as URLSearchParams writes it: a space is +, and only letters, digits and *-._ stay as they are."""
    return quote_plus(value, safe="*", encoding=encoding, errors=errors).replace("~", "%7E")


def api_base() -> str:
    """The API root; tests point it at a local stub."""
    return (credential("collectors", "SOCIALDATA_BASE_URL") or "https://api.socialdata.tools").rstrip("/")


def _get(pool, path: str, request: dict, *, timeout_ms: int, max_bytes: int, settle, missing: dict | None = None) -> dict:
    """One paid GET behind a receipt; `settle` turns the parsed answer into what the receipt keeps. A lookup passes the usage of its 404 as `missing`: the post or
    article does not exist, an answer that costs nothing. Any other status outside 2xx means SocialData did not take the request (assert_accepted)."""
    key = credential("collectors", "SOCIALDATA_API_KEY")
    if not key:
        raise RuntimeError("SOCIALDATA_API_KEY is not configured")

    def call(permit=None) -> CallOutcome:
        res = guarded_fetch(
            f"{api_base()}{path}",
            headers={"authorization": f"Bearer {key}", "accept": "application/json"},
            timeout_ms=timeout_ms,
            max_bytes=max_bytes,
            route="direct",
        )
        if res.status == 404 and missing is not None:
            return CallOutcome(response=None, usage=missing, cost=objects_cost(0))
        text = res.text()
        assert_accepted("socialdata", res.status, text)
        return settle(json.loads(text))

    return paid_request(pool, ReceiptRequest(service="socialdata", **request), call)


LOOKUP = {"timeout_ms": 30_000, "max_bytes": 4 * 1024 * 1024}  # lookups of one post or article


def search_tweets(pool, query: str, purpose: str, subject: str, window: str, kind: str = "Latest", cursor: str | None = None) -> dict:
    """Searches recent tweets. `window` makes the receipt identity time-bucketed so a retry in the same bucket reuses the stored response instead of paying again."""
    params = {"query": query, "type": kind, **({"cursor": cursor} if cursor else {})}

    def settle(data: dict) -> CallOutcome:
        n = len(data.get("tweets") or [])
        return CallOutcome(response=data, usage={"tweets": n}, cost=objects_cost(n))

    receipt = _get(
        pool, f"/twitter/search?{urlencode(params, quote_via=_form_quote)}",
        {"purpose": purpose, "subject": subject, "identity": {"query": query, "type": kind, "cursor": cursor, "window": window}, "request_summary": {"query": query, "type": kind}},
        timeout_ms=60_000, max_bytes=12 * 1024 * 1024, settle=settle,
    )  # fmt: skip
    data = receipt["response"]
    return {"tweets": data.get("tweets") or [], "nextCursor": data.get("next_cursor"), "receiptId": receipt["receiptId"], "reused": receipt["reused"]}


def _decode_entities(text: str) -> str:
    """The provider escapes these three plain-text characters. One pass preserves literal entity text."""
    return re.sub(r"&(?:amp|lt|gt);", lambda m: {"&amp;": "&", "&lt;": "<", "&gt;": ">"}[m.group(0)], text)


def tweet_text(t: dict) -> str:
    # Decode before inserting expanded URLs: literal code and destination URLs keep their own bytes.
    text = _decode_entities(cast(str, t.get("full_text") if t.get("full_text") is not None else t.get("text") or ""))
    # Expand t.co links and drop trailing media links.
    for u in (t.get("entities") or {}).get("urls") or []:
        text = text.replace(u["url"], u["expanded_url"])
    media = (t.get("extended_entities") or {}).get("media") or (t.get("entities") or {}).get("media") or []
    for _ in media:
        text = re.sub(r"\s*https://t\.co/\w+\s*$", "", text, count=1)
    return text.strip()


def tweet_media(t: dict) -> list[dict]:
    media = (t.get("extended_entities") or {}).get("media") or (t.get("entities") or {}).get("media") or []
    out = []
    for m in media:
        info = m.get("original_info") or {}
        out.append({
            "kind": "image" if m.get("type") == "photo" else "video", "url": m["media_url_https"], "width": info.get("width"), "height": info.get("height"),
            "poster": None if m.get("type") == "photo" else m["media_url_https"],
        })  # fmt: skip
    return out


def get_article(pool, tweet_id: str, purpose: str, subject: str) -> dict | None:
    """The long-form article a post published, looked up by the id of that post. None when the post published none. Paid per article."""

    def settle(data: dict) -> CallOutcome:
        # A post without an article answers with the post alone, or with {status: "error"}.
        article = None if data.get("status") == "error" else data.get("article")
        return CallOutcome(response=article, usage={"articles": 1 if article else 0}, cost=objects_cost(1))

    receipt = _get(
        pool, f"/twitter/article/{quote(tweet_id, safe='')}",
        {"purpose": purpose, "subject": subject, "identity": {"article": tweet_id}, "request_summary": {"article": tweet_id}}, **LOOKUP, missing={"articles": 0}, settle=settle,
    )  # fmt: skip
    return receipt["response"] or None


PRIVATE_REFUSAL = "Forbidden: Requested resource cannot be viewed due to user's privacy settings"


def private_account_refusal(error: BaseException) -> bool:
    """SocialData refused a post because its account is private: no post of it can show who the account is now."""
    return isinstance(error, ProviderRejectedError) and error.status == 403 and PRIVATE_REFUSAL in str(error)


def get_tweet(pool, tweet_id: str, purpose: str, subject: str, window: str | None = None) -> dict | None:
    """One tweet by id. Paid; the receipt makes retries free. A `window` asks again in each window instead of reusing the first answer."""
    receipt = _get(
        pool, f"/twitter/tweets/{quote(tweet_id, safe='')}",
        {"purpose": purpose, "subject": subject, "identity": {"tweet": tweet_id, "window": window} if window else {"tweet": tweet_id}, "request_summary": {"tweet": tweet_id}},
        **LOOKUP, missing={"tweets": 0}, settle=lambda data: CallOutcome(response=data, usage={"tweets": 1}, cost=objects_cost(1)),
    )  # fmt: skip
    return receipt["response"] or None
