"""Signed image proxy (port of apps/api/src/routes/media.ts). Only addresses this site signed are served; a missing, wrong or expired signature is
refused without any upstream fetch, so the proxy can never serve as a free image host. A GIF or an AVIF variant waiting for background
conversion is served as it is and kept a minute at most: this process does not queue the conversion (the worker's job queue does)."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import etag_matches, loose_query
from newsroom.engine.media.images import ImageBudgetExceeded, budget, failure_seconds, produce_avif, produce_image
from newsroom.engine.media.imgproxy import verify_proxy_request

log = logging.getLogger("newsroom.api")
LONGEST_CACHE_SECONDS = 30 * 24 * 3600  # as long as an avatar keeps its address


def image_cache_control(exp: str, limit: int) -> str:
    remaining = min(limit, int(exp) - math.floor(time.time()))
    return f"public, max-age={remaining}, s-maxage={remaining}" if remaining > 0 else "no-store"


def text(body: str, status: int, headers: dict[str, str]) -> Response:
    return Response(body, status_code=status, media_type="text/plain; charset=utf-8", headers=headers)


def register_media(app: FastAPI) -> None:
    def record_trip(value: dict) -> None:
        """The first time a window is exceeded, the alerts' digest learns of it."""
        try:
            with app.state.pool.connection() as conn:
                conn.execute(
                    """INSERT INTO settings (key, value, updated_by) VALUES ('egress.imgproxy', %s, 'api')
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                    (Jsonb(value),),
                )
        except Exception:
            log.warning("could not record the image budget trip")

    budget().on_trip = record_trip

    @app.get("/api/img-proxy", include_in_schema=False)
    async def img_proxy(request: Request) -> Response:
        q = loose_query(request)
        verdict = verify_proxy_request(q.get("u"), q.get("mode"), q.get("exp"), q.get("sig"))
        if not verdict["ok"]:
            malformed = verdict["reason"] in ("missing", "bad-url")
            return text(
                "invalid query\n" if malformed else "forbidden\n", 400 if malformed else 403,
                {"Cache-Control": "no-store", "X-Img-Proxy-Sig": "expired" if verdict["reason"] == "expired" else "invalid"},
            )  # fmt: skip
        try:
            produce = produce_avif if verdict["avif"] else produce_image
            image = await run_in_threadpool(produce, verdict["url"], verdict["mode"])
        except ImageBudgetExceeded:
            return text("Image proxy is busy; try again later", 503, {"Retry-After": "60", "Cache-Control": "no-store", "X-Img-Proxy-Sig": "valid"})
        except Exception as error:
            log.warning("img-proxy upstream failed: host=%s mode=%s err=%r", urlsplit(verdict["url"]).hostname, verdict["mode"], error)
            return text(
                "Upstream image unavailable",
                502,
                {"Cache-Control": image_cache_control(q["exp"], failure_seconds(error)), "X-Img-Proxy-Sig": "valid"},
            )
        pending = image.pending_animation or image.pending_avif
        # A validator describes the actual representation, including a GIF replaced by prepared WebP.
        etag = '"' + hashlib.sha256(image.type.encode() + b"\0" + image.body).hexdigest() + '"'
        headers = {
            "Content-Type": image.type,
            # Nothing queues the conversion here, so a pending picture is never cached for long.
            "Cache-Control": "no-store" if pending else image_cache_control(q["exp"], LONGEST_CACHE_SECONDS),
            "ETag": etag, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Img-Proxy-Sig": "valid",
        }  # fmt: skip
        if etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(image.body, headers=headers)


__all__ = ["budget", "register_media"]
