"""First-party site API (/api/site/*): port of apps/api/src/routes/site.ts. Not public, not versioned. Reads through the same public read layer as
v1; no cookies are read or set."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from functools import wraps

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import QueryError, js_json, json_with_etag, loose_query, problem
from newsroom.engine.lib.cursor import InvalidCursorError
from newsroom.engine.publication.pool import SearchBusyError
from newsroom.engine.publication.reports import (
    list_reports,
    load_report,
    load_report_month,
    load_report_navigation,
    report_navigation,
)
from newsroom.engine.publication.site_lists import hot_search_links, load_hot_strip, load_pool, load_timeline
from newsroom.engine.publication.site_story import load_group_reports, load_hot, load_story_detail, load_story_followups
from newsroom.engine.publication.site_topics import list_topic_summaries, load_topic_page
from newsroom.engine.publication.stories import resolve_story
from newsroom.engine.publication.topics import topic_browse_links
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


def js_number(value: str | None) -> float:
    """Number(x): NaN becomes 0 here, since every caller uses `Number(x) || fallback`."""
    try:
        n = float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0
    return 0.0 if math.isnan(n) else n


def js_page(raw: str | None) -> float:
    """Number(query.page ?? 1): NaN stays invalid (the page check rejects it)."""
    if raw is None:
        return 1.0
    try:
        return float(raw) if raw.strip() else 0.0
    except ValueError:
        return float("nan")


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

    @app.get("/api/site/timeline")
    @site_handler
    def timeline(request: Request) -> Response:
        q = loose_query(request)
        filters = parse_filters(q)
        limit = int(min(max(js_number(q.get("limit")) or 20, 1), 40))
        unfiltered = filters["channel"] == "all" and not filters["category"] and not filters["tag"] and not q.get("cursor")
        pool = request.app.state.pool
        data = load_timeline(pool, filters, q.get("cursor") or None, limit)
        hot = load_hot_strip(pool) if unfiltered else None
        response = json_with_etag(request, {**data, "hot": hot}, "tl", "public, max-age=60, s-maxage=60")
        # Any cache in front and the home page built from this answer share one absolute deadline.
        response.headers["X-Accel-Expires"] = f"@{int(time.time()) + 60}"
        return response

    @app.get("/api/site/pool")
    @site_handler
    def pool_route(request: Request) -> Response:
        q = loose_query(request)
        filters = parse_filters(q)
        page = int(min(max(js_number(q.get("page")) or 1, 1), 50))
        search = q["q"].strip()[:200] if q.get("q", "").strip() else None
        tab = "relevance" if q.get("tab") == "relevance" else "time"
        body = load_pool(request.app.state.pool, {**filters, "q": search, "tab": tab, "page": page})
        return json_with_etag(request, body, "pool", "public, max-age=60, s-maxage=60")

    @app.get("/api/site/search/suggestions")
    @site_handler
    def suggestions(request: Request) -> Response:
        body = {"topics": topic_browse_links(), "hot": hot_search_links(request.app.state.pool)}
        return json_with_etag(request, body, "suggestions", "public, max-age=30, s-maxage=30, must-revalidate")

    @app.get("/api/site/hot")
    @site_handler
    def hot(request: Request) -> Response:
        return json_with_etag(request, load_hot(request.app.state.pool), "hot", "public, max-age=30, s-maxage=30")

    @app.get("/api/site/stories/{public_id}/followups")
    @site_handler
    def followups(request: Request, public_id: str) -> Response:
        result = load_story_followups(request.app.state.pool, public_id)
        if not result:
            return problem(request, 404, "not_found", "story not found")
        return Response(js_json(result), media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.get("/api/site/groups/{fact_id}/reports")
    @site_handler
    def group_reports(request: Request, fact_id: str) -> Response:
        f = parse_filters(loose_query(request))
        data = load_group_reports(request.app.state.pool, fact_id, f["channel"], f["category"], f["tag"])
        if not data:
            return problem(request, 404, "not_found", "reading group unavailable")
        return Response(js_json(data), media_type="application/json", headers={"Cache-Control": "no-store"})

    @app.get("/api/site/stories/{public_id}")
    @site_handler
    def story(request: Request, public_id: str) -> Response:
        pool = request.app.state.pool
        with pool.connection() as conn:
            found = resolve_story(conn, public_id)
        if found["kind"] == "merged":
            return Response(js_json({"mergedInto": found["target"]}), status_code=308, media_type="application/json",
                            headers={"Location": f"/api/site/stories/{found['target']}", "Cache-Control": "public, max-age=300"})  # fmt: skip
        if found["kind"] == "not_found":
            return problem(request, 404, "not_found", "story not found", cache_control="public, max-age=60")
        data = load_story_detail(pool, found["storyId"])
        if not data:
            return problem(request, 404, "not_found", "story not public", cache_control="public, max-age=60")
        return json_with_etag(request, data, "story", "public, max-age=60, s-maxage=60")

    kinds = ("daily", "weekly", "monthly")
    key_re = re.compile(r"\d{4}-(\d{2}(-\d{2})?|W\d{2})")
    cache60 = "public, max-age=60, s-maxage=60"

    @app.get("/api/site/reports/{kind}")
    @site_handler
    def report_list(request: Request, kind: str) -> Response:
        if kind not in kinds:
            return problem(request, 404, "not_found", "unknown report kind")
        body = {"kind": kind, "items": list_reports(request.app.state.pool, kind)}
        return json_with_etag(request, body, "reports", cache60)

    # The latest report page needs its archive selector and the report in one HTTP request.
    @app.get("/api/site/reports/{kind}/latest-page")
    @site_handler
    def report_latest(request: Request, kind: str) -> Response:
        if kind not in kinds:
            return problem(request, 404, "not_found", "unknown report kind")
        pool = request.app.state.pool
        index = list_reports(pool, kind)
        report = load_report(pool, kind, index[0]["key"]) if index else None
        body = {"index": report_navigation(kind, index, report["key"] if report else ""), "report": report}
        return json_with_etag(request, body, "report-latest", cache60)

    @app.get("/api/site/reports/daily/months/{month}")
    @site_handler
    def report_month(request: Request, month: str) -> Response:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            return problem(request, 404, "not_found", "month not found")
        return json_with_etag(request, {"items": load_report_month(request.app.state.pool, "daily", month)}, "report-month", cache60)

    @app.get("/api/site/reports/{kind}/navigation/{key}")
    @site_handler
    def report_nav(request: Request, kind: str, key: str) -> Response:
        if kind not in kinds or not key_re.fullmatch(key):
            return problem(request, 404, "not_found", "report not found")
        return json_with_etag(request, {"items": load_report_navigation(request.app.state.pool, kind, key)}, "report-navigation", cache60)

    @app.get("/api/site/reports/{kind}/{key}")
    @site_handler
    def report_detail(request: Request, kind: str, key: str) -> Response:
        if kind not in kinds or not key_re.fullmatch(key):
            return problem(request, 404, "not_found", "report not found")
        data = load_report(request.app.state.pool, kind, key)
        if not data:
            return problem(request, 404, "not_found", "report not found", cache_control="public, max-age=60")
        return json_with_etag(request, data, "report", "public, max-age=120, s-maxage=120")

    @app.get("/api/site/topics")
    @site_handler
    def topic_directory(request: Request) -> Response:
        return json_with_etag(request, list_topic_summaries(request.app.state.pool), "topics", "public, max-age=300, s-maxage=300")

    @app.get("/api/site/topics/{slug}")
    @site_handler
    def topic_page(request: Request, slug: str) -> Response:
        data = load_topic_page(request.app.state.pool, slug, js_page(loose_query(request).get("page")))
        if not data:
            return problem(request, 404, "not_found", "topic page not found", cache_control="public, max-age=60")
        return json_with_etag(request, data, "topic", "public, max-age=60, s-maxage=60")
