"""RSS routes (port of apps/api/src/routes/feeds.ts). Unknown query parameters are accepted and never change content. A preflight gets 204 and
the allowed methods; feeds send no CORS headers. The full-text feeds (/feed/full.xml, /feed/full/category/*) are not ported yet."""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import apply_public_headers, text_with_etag
from newsroom.engine.publication.feeds import feed_cache_control, is_feed_category, item_feed, report_feed

log = logging.getLogger("newsroom.api")
RSS = "application/rss+xml; charset=utf-8"


def _serve(request: Request, build: Callable[[], str], kind: str) -> Response:
    try:
        xml = build()
    except Exception:
        log.exception("feed error: %s", request.url.path)
        return Response(
            "Feed temporarily unavailable", status_code=503, media_type="text/plain; charset=utf-8",
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )  # fmt: skip
    response = text_with_etag(request, xml, "rss", feed_cache_control(kind), RSS)
    apply_public_headers(response, cors=False)
    return response


def register_feeds(app: FastAPI) -> None:
    def preflight() -> Response:
        return Response(status_code=204, headers={"Allow": "GET, HEAD, OPTIONS"})

    for url in ["/feed.xml", "/feed/all.xml", "/feed/daily.xml", "/feed/weekly.xml", "/feed/monthly.xml", "/feed/category/{file}"]:
        app.add_api_route(url, preflight, methods=["OPTIONS"], include_in_schema=False)

    def item(url: str, kind: str) -> None:
        def handler(request: Request) -> Response:
            return _serve(request, lambda: item_feed(request.app.state.pool, kind, None), kind)

        app.add_api_route(url, handler, methods=["GET"], include_in_schema=False)

    item("/feed.xml", "selected")
    item("/feed/all.xml", "all")

    def report(kind: str) -> None:
        def handler(request: Request) -> Response:
            return _serve(request, lambda: report_feed(request.app.state.pool, kind), kind)

        app.add_api_route(f"/feed/{kind}.xml", handler, methods=["GET"], include_in_schema=False)

    for kind in ("daily", "weekly", "monthly"):
        report(kind)

    @app.get("/feed/category/{file}", include_in_schema=False)
    def category(request: Request, file: str) -> Response:
        slug = file.removesuffix(".xml") if file.endswith(".xml") else file
        if not file.endswith(".xml") or not is_feed_category(slug):
            return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8")
        return _serve(request, lambda: item_feed(request.app.state.pool, "selected", slug), "selected")
