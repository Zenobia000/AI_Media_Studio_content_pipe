"""First-party site API (/api/site/*): port of apps/api/src/routes/site.ts. Not public, not versioned. Reads through the same public read layer as
v1; no cookies are read or set."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import QueryError, js_json, json_with_etag, loose_query, problem
from newsroom.engine.lib.cursor import InvalidCursorError
from newsroom.engine.publication.pool import SearchBusyError
from newsroom.engine.site import item_availability, load_changelog, load_contact, load_maker_avatar, load_site_stats, site_meta
from newsroom.engine.taxonomy import is_category_key, is_channel_key

log = logging.getLogger("newsroom.api")


class BadRequest(Exception):
    pass


def site_handler(fn: Callable[..., Response]) -> Callable[..., Response]:
    @wraps(fn)
    def wrapper(request: Request, *args, **kwargs) -> Response:
        try:
            return fn(request, *args, **kwargs)
        except (BadRequest, QueryError) as e:
            return problem(request, 400, "invalid_request", str(e))
        except InvalidCursorError as e:
            return problem(request, 400, "invalid_cursor", str(e))
        except SearchBusyError as e:
            return problem(request, 503, "temporarily_unavailable", "search busy", retry_after=e.retry_after)
        except Exception:
            log.exception("site api error: %s", request.url.path)
            return problem(request, 503, "temporarily_unavailable", "temporarily unavailable", retry_after=10)

    return wrapper


def parse_filters(q: dict[str, str]) -> dict:
    channel = q.get("channel", "all")
    if not is_channel_key(channel):
        raise BadRequest("invalid channel")
    category = q.get("category")
    if category is not None and not is_category_key(category):
        raise BadRequest("invalid category")
    tag = q["tag"].strip()[:60] if q.get("tag", "").strip() else None
    return {"channel": channel, "category": category, "tag": tag}


def register_site(app: FastAPI) -> None:
    @app.get("/api/site/meta")
    @site_handler
    def meta(request: Request) -> Response:
        return json_with_etag(request, site_meta(), "meta", "public, max-age=60, s-maxage=60")

    @app.get("/api/site/contact")
    @site_handler
    def contact(request: Request) -> Response:
        with request.app.state.pool.connection() as conn:
            body = {**load_contact(conn), "makerAvatar": load_maker_avatar(conn)}
        return json_with_etag(request, body, "contact", "public, max-age=300, s-maxage=300")

    @app.get("/api/site/stats")
    @site_handler
    def stats(request: Request) -> Response:
        return json_with_etag(request, load_site_stats(request.app.state.pool), "stats", "public, max-age=300, s-maxage=300")

    @app.get("/api/site/changelog")
    @site_handler
    def changelog(request: Request) -> Response:
        return json_with_etag(request, load_changelog(), "changelog", "public, max-age=300, s-maxage=300")

    @app.get("/api/site/items/availability")
    @site_handler
    def availability(request: Request) -> Response:
        ids = [i for i in loose_query(request).get("ids", "").split(",") if i]
        with request.app.state.pool.connection() as conn:
            body = item_availability(conn, ids)
        return Response(js_json(body), media_type="application/json", headers={"Cache-Control": "no-store"})
