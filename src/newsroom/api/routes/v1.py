"""Public API v1 (long-term): port of apps/api/src/routes/v1.ts. Field shapes follow the OpenAPI document at /openapi-v1.json (the paths stay
/api/v1). This file has the shared pieces and the fallbacks; the operations are added as the publication layer behind them is ported."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from functools import wraps

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import QueryError, apply_public_headers, json_with_etag, problem, strict_query
from newsroom.engine.config import config
from newsroom.engine.lib.cursor import InvalidCursorError
from newsroom.engine.publication.v1 import V1ItemsQuery, v1_items
from newsroom.engine.taxonomy import public_api_category_keys

log = logging.getLogger("newsroom.api")


def op(query_keys: list[str], cache_control: str) -> dict:
    return {"queryKeys": query_keys, "cacheControl": cache_control}


_MIN = "public, max-age=60, s-maxage=60, must-revalidate"
_FIVE = "public, max-age=300, s-maxage=300, must-revalidate"
V1_OPERATIONS = {
    "items": op(["mode", "category", "window", "by", "q", "limit", "cursor"], _MIN),
    "hotTopics": op([], _MIN),
    "storyByPublicId": op([], _MIN),
    "dailies": op(["limit"], _MIN),
    "latestDaily": op([], _MIN),
    "dailyByDate": op([], _FIVE),
    "weeklies": op(["limit"], _FIVE),
    "latestWeekly": op([], _FIVE),
    "weeklyByWeek": op([], _FIVE),
    "monthlies": op(["limit"], _FIVE),
    "latestMonthly": op([], _FIVE),
    "monthlyByMonth": op([], _FIVE),
    "selectedSnapshot": op(["fields", "limit", "page"], _FIVE),
    "selectedChanges": op(["cursor", "limit"], _MIN),
}


def int_param(value: str | None, name: str, lo: int, hi: int, fallback: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"\d+", value):
        raise QueryError(f"{name} must be an integer from {lo} to {hi}.")
    n = int(value)
    if n < lo or n > hi:
        raise QueryError(f"{name} must be an integer from {lo} to {hi}.")
    return n


def enum_param(value: str | None, name: str, allowed: list[str], fallback: str) -> str:
    if value is None:
        return fallback
    if value not in allowed:
        raise QueryError(
            f"{name} must be '{allowed[0]}' or '{allowed[1]}'." if len(allowed) == 2 else f"{name} must be one of: {', '.join(allowed)}."
        )
    return value


class SnapshotRequiredError(Exception):
    pass


class SearchBusyError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("search is busy")
        self.retry_after = retry_after


def public_handler(fn: Callable[..., Response]) -> Callable[..., Response]:
    """Wraps a v1 handler with shared error mapping. Errors never leak internals."""

    @wraps(fn)
    def wrapper(request: Request, *args, **kwargs) -> Response:
        try:
            response = fn(request, *args, **kwargs)
        except QueryError as e:
            response = problem(request, 400, "invalid_request", str(e), title="Invalid request")
        except InvalidCursorError:
            response = problem(request, 400, "invalid_cursor", "The cursor is malformed.", title="Invalid cursor")
        except SnapshotRequiredError:
            response = problem(
                request, 409, "snapshot_required", "Missing or invalid v1 cursor; fetch /api/v1/selected/snapshot first.", title="Snapshot required"
            )
        except SearchBusyError as e:
            response = problem(request, 503, "temporarily_unavailable", "Search is busy; retry later.", retry_after=e.retry_after)
        except Exception:
            log.exception("public api error: %s", request.url.path)
            response = problem(request, 503, "temporarily_unavailable", "The service is temporarily unavailable; retry later.", retry_after=30)
        apply_public_headers(response)
        return response

    return wrapper


def _not_allowed(request: Request) -> Response:
    response = problem(request, 405, "method_not_allowed", "This endpoint supports only GET, HEAD, and OPTIONS.")
    apply_public_headers(response)
    response.headers["Allow"] = "GET, HEAD, OPTIONS"
    return response


def _preflight(request: Request) -> Response:
    response = Response(status_code=204, headers={"Cache-Control": "public, max-age=86400"})
    apply_public_headers(response)
    return response


def read_only_methods(app: FastAPI, url: str) -> None:
    """A read-only public address (a path or a wildcard): CORS preflight, and 405 for any other method."""
    app.add_api_route(url, _preflight, methods=["OPTIONS"], include_in_schema=False)
    app.add_api_route(url, _not_allowed, methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)


def _not_found(request: Request, rest: str = "") -> Response:
    path = request.url.path
    response = problem(
        request, 404, "not_found",
        f"No public API v1 operation exists at {path}. Recent items are at /api/v1/items; every operation is listed at {config().site_url}/openapi-v1.json",
        title="Not found",
    )  # fmt: skip
    apply_public_headers(response)
    return response


def send_v1(request: Request, body, etag_prefix: str, cache_control: str, etag_of=None) -> Response:
    """Every v1 JSON answer: with a weak ETag, 304 when it matches."""
    return json_with_etag(request, body, etag_prefix, cache_control, etag_of)


def register_v1(app: FastAPI) -> None:
    """The operations (added with the publication layer they read)."""

    @app.get("/api/v1/items")
    @public_handler
    def items(request: Request) -> Response:
        q = strict_query(request, V1_OPERATIONS["items"]["queryKeys"])
        mode = enum_param(q.get("mode"), "mode", ["selected", "all"], "selected")
        window = enum_param(q.get("window"), "window", ["24h", "7d"], "7d")
        by = enum_param(q.get("by"), "by", ["timeline", "published"], "timeline")
        category = None if "category" not in q else enum_param(q["category"], "category", public_api_category_keys(), public_api_category_keys()[0])
        search = None
        if "q" in q:
            search = q["q"].strip()
            if len(search) < 2 or len(search) > 200:
                raise QueryError("q must contain 2 to 200 characters.")
        limit = int_param(q.get("limit"), "limit", 1, 100, 50)
        if "cursor" in q and len(q["cursor"]) == 0:
            raise InvalidCursorError("empty cursor")
        body = v1_items(request.app.state.pool, V1ItemsQuery(mode, window, by, category, search, limit, q.get("cursor")))
        return send_v1(request, body, "v1-items", V1_OPERATIONS["items"]["cacheControl"])


def register_v1_fallbacks(app: FastAPI) -> None:
    """Registered last: CORS preflight, 405 for other methods, Problem 404 for undefined v1 paths."""
    for url in ("/api/v1", "/api/v1/{rest:path}", "/openapi-v1.json"):
        read_only_methods(app, url)
    app.add_api_route("/api/v1", _not_found, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/v1/{rest:path}", _not_found, methods=["GET"], include_in_schema=False)
