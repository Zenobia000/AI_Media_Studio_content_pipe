"""Free listing reads: one retry for transient transport/upstream failures within the original deadline. Port of sources/listing-fetch.ts."""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from newsroom.engine.lib.http_fetch import GuardedResponse, guarded_fetch

RETRY_DELAY = 1.0


def _transient_transport(error: BaseException) -> bool:
    if isinstance(
        error,
        (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, ConnectionResetError, BrokenPipeError),
    ):
        return True
    cause = error.__cause__ or error.__context__
    return cause is not None and cause is not error and _transient_transport(cause)


def _youtube_feed(url: str) -> bool:
    u = urlsplit(url)
    return (
        u.scheme == "https" and u.hostname in ("youtube.com", "www.youtube.com") and u.path == "/feeds/videos.xml"
        and re.fullmatch(r"UC[\w-]{22}", (parse_qs(u.query).get("channel_id") or [""])[0]) is not None
    )  # fmt: skip


def _transient_response(url: str, res: GuardedResponse) -> bool:
    # A working YouTube channel feed can intermittently return 404. Other missing URLs stay errors.
    return res.status in (500, 502, 503, 504) or (res.status == 404 and _youtube_feed(url) and _youtube_feed(res.url))


def fetch_listing(
    url: str, headers: dict | None = None, timeout_ms: int = 20_000, method: str = "GET", body: str | None = None, **opts
) -> GuardedResponse:
    repeatable = method.upper() == "GET" and body is None
    deadline = time.monotonic() + timeout_ms / 1000
    attempt = 0
    while True:
        can_retry = repeatable and attempt == 0 and deadline - time.monotonic() > RETRY_DELAY
        try:
            # Listing addresses come from the source configuration, so a listed private feed host may serve them.
            res = guarded_fetch(
                url,
                method=method,
                headers=headers,
                body=body,
                private_feed_host=True,
                timeout_ms=max(1, int((deadline - time.monotonic()) * 1000)),
                **opts,
            )
            if not can_retry or not _transient_response(url, res):
                return res
        except Exception as error:
            if not can_retry or not _transient_transport(error):
                raise
        time.sleep(RETRY_DELAY)
        attempt += 1
