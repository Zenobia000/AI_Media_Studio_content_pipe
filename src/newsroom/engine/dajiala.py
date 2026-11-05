"""Dajiala WeChat official-account data. Paid per request (post_history 0.14 yuan, article_detail 0.03); every call goes through receipts and the budget, and the
provider's own cost_money is kept as the actual cost. Port of providers/dajiala.ts."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from newsroom.engine.config import credential
from newsroom.engine.lib.http_fetch import guarded_fetch
from newsroom.engine.receipts import CallOutcome, ProviderRejectedError, ReceiptRequest, assert_accepted, paid_request


def dajiala_configured() -> bool:
    """Whether WeChat accounts are checked through Dajiala: only with DAJIALA_KEY. The scheduled checks and their queue run only then."""
    return credential("collectors", "DAJIALA_KEY") is not None


def _base() -> tuple[str, str]:
    key = credential("collectors", "DAJIALA_KEY")
    if not key:
        raise RuntimeError("DAJIALA_KEY is not configured")
    return (credential("collectors", "DAJIALA_BASE_URL") or "https://www.dajiala.com").rstrip("/"), key


def _outcome_of(data: dict, label: str) -> dict | None:
    """Provider codes: -1 rate limit (retry later); 20001 balance; 101/104/107 account gone; others rejected."""
    try:
        code = int(data.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    if code == -1:
        raise ProviderRejectedError(f"dajiala {label}: rate limited", 429, True)
    if code != 0:
        raise ProviderRejectedError(f"dajiala {label} code {code}: {data.get('msg') or ''}".strip(), code, False)
    cost = data.get("cost_money")
    return {"amount": cost, "currency": "CNY", "basis": "actual"} if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


def mp_history(pool, ghid: str, subject: str, window: str) -> dict:
    """Latest posts of one account (first page, newest first). `window` buckets the receipt identity."""
    url, key = _base()

    def call(permit=None) -> CallOutcome:
        res = guarded_fetch(
            f"{url}/fbmain/monitor/v3/post_history", method="POST", headers={"content-type": "application/json", "accept": "application/json"},
            body=json.dumps({"ghid": ghid, "key": key, "verifycode": ""}, separators=(",", ":")), timeout_ms=30_000, route="direct",
        )  # fmt: skip
        text = res.text()
        assert_accepted("dajiala", res.status, text)
        data = json.loads(text)
        cost = _outcome_of(data, "post_history")
        return CallOutcome(response=data, cost=cost, usage={"posts": len(data["data"]) if isinstance(data.get("data"), list) else 0})

    receipt = paid_request(
        pool,
        ReceiptRequest(
            service="dajiala", purpose="mp_history", subject=subject, identity={"ghid": ghid, "window": window}, request_summary={"ghid": ghid}
        ),
        call,
    )
    data = receipt["response"]
    return {
        "posts": data.get("data") or [],
        "nickname": data.get("nickname"),
        "remainMoney": data.get("remain_money"),
        "receiptId": receipt["receiptId"],
        "reused": receipt["reused"],
    }


def mp_article(pool, article_url: str, subject: str, identity: str) -> dict:
    """Plain-text body of one article (mode 1: text with image markers)."""
    url, key = _base()

    def call(permit=None) -> CallOutcome:
        res = guarded_fetch(
            f"{url}/fbmain/monitor/v3/article_detail?{urlencode({'url': article_url, 'key': key, 'mode': '1', 'verifycode': ''})}", redirect_policy="same-origin",
            headers={"accept": "application/json"}, timeout_ms=30_000, max_bytes=8 * 1024 * 1024, route="direct",
        )  # fmt: skip
        text = res.text()
        assert_accepted("dajiala", res.status, text)
        data = json.loads(text)
        cost = _outcome_of(data, "article_detail")
        return CallOutcome(response=data, cost=cost, usage={"chars": len(str(data.get("content") or ""))})

    receipt = paid_request(
        pool, ReceiptRequest(service="dajiala", purpose="mp_article", subject=subject, identity={"article": identity}, request_summary={"url": article_url}), call
    )  # fmt: skip
    j = receipt["response"]
    return {
        "title": j.get("title") or "",
        "content": j.get("content") or "",
        "author": j.get("author") or None,
        "desc": j.get("desc") or None,
        "pubtime": j.get("pubtime"),
        "receiptId": receipt["receiptId"],
    }
