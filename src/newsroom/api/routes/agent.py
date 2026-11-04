"""/api/v1/agent: Markdown answers for AI agents (port of apps/api/src/routes/agent.ts). They render with the same functions as the MCP tools;
errors are the usual v1 Problem JSON. New abilities become new addresses listed in the guide, which agents read without updating."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response

from newsroom.api.respond import QueryError, problem, strict_query, text_with_etag
from newsroom.api.routes.v1 import V1_OPERATIONS, enum_param, int_param, public_handler
from newsroom.engine.publication.agent import (
    agent_guide,
    daily_answer,
    hot_answer,
    latest_answer,
    period_answer,
    search_answer,
    search_items,
    story_answer,
)
from newsroom.engine.publication.reports import daily_with_notes, is_period_key, v1_period
from newsroom.engine.publication.stories import resolve_story, v1_hot_topics, v1_story
from newsroom.engine.publication.v1 import V1ItemsQuery, v1_items
from newsroom.engine.taxonomy import public_api_category_keys
from newsroom.engine.time import is_valid_date

MARKDOWN = "text/markdown; charset=utf-8"


def markdown(request: Request, text: str, etag_prefix: str, cache_control: str) -> Response:
    return text_with_etag(request, text, etag_prefix, cache_control, MARKDOWN)


def list_params(q: dict[str, str]) -> dict:
    keys = public_api_category_keys()
    return {
        "window": enum_param(q.get("window"), "window", ["24h", "7d"], "24h"),
        "category": None if "category" not in q else enum_param(q["category"], "category", keys, keys[0]),
        "limit": int_param(q.get("limit"), "limit", 1, 30, 10),
    }


def register_agent(app: FastAPI) -> None:
    @app.get("/api/v1/agent")
    @public_handler
    def guide(request: Request) -> Response:
        strict_query(request, [])
        # Cached like /openapi-v1.json: agents learn a new ability within minutes of its release.
        return markdown(request, agent_guide(), "agent-guide", "public, max-age=300, must-revalidate")

    @app.get("/api/v1/agent/latest")
    @public_handler
    def latest(request: Request) -> Response:
        q = strict_query(request, ["window", "mode", "category", "limit"])
        p = {**list_params(q), "mode": enum_param(q.get("mode"), "mode", ["selected", "all"], "selected")}
        res = v1_items(request.app.state.pool, V1ItemsQuery(p["mode"], p["window"], "timeline", p["category"], None, p["limit"], None))
        return markdown(request, latest_answer(res, p), "agent-latest", V1_OPERATIONS["items"]["cacheControl"])

    @app.get("/api/v1/agent/search")
    @public_handler
    def search(request: Request) -> Response:
        q = strict_query(request, ["q", "window", "category", "limit"])
        text = (q.get("q") or "").strip()
        if len(text) < 2 or len(text) > 200:
            raise QueryError("q must contain 2 to 200 characters.")
        p = {**list_params(q), "window": enum_param(q.get("window"), "window", ["24h", "7d"], "7d")}
        found = search_items(request.app.state.pool, text, p["window"], p["category"], p["limit"])
        body = search_answer(found, {"q": text, "window": p["window"], "category": p["category"]})
        return markdown(request, body, "agent-search", V1_OPERATIONS["items"]["cacheControl"])

    @app.get("/api/v1/agent/hot")
    @public_handler
    def hot(request: Request) -> Response:
        q = strict_query(request, ["limit"])
        with request.app.state.pool.connection() as conn:
            res = v1_hot_topics(conn)
        body = hot_answer(res, int_param(q.get("limit"), "limit", 1, 10, 10), "http")
        return markdown(request, body, "agent-hot", V1_OPERATIONS["hotTopics"]["cacheControl"])

    @app.get("/api/v1/agent/stories/{public_id}")
    @public_handler
    def story(request: Request, public_id: str) -> Response:
        q = strict_query(request, ["limit"])
        limit = int_param(q.get("limit"), "limit", 1, 50, 20)
        if len(public_id) > 128:
            raise QueryError("publicId must be a short opaque id.")
        with request.app.state.pool.connection() as conn:
            found = resolve_story(conn, public_id)
            if found["kind"] == "merged":
                found = resolve_story(conn, found["target"])
            body = v1_story(conn, found["storyId"]) if found["kind"] == "found" else None
        if not body:
            return problem(
                request, 404, "not_found", "沒有這個公開事件。只使用熱點結果裡給出的「來龍去脈」地址，不要猜。", cache_control="public, max-age=60"
            )
        return markdown(request, story_answer(body["story"], limit, "http"), "agent-story", V1_OPERATIONS["storyByPublicId"]["cacheControl"])

    @app.get("/api/v1/agent/daily")
    @public_handler
    def daily(request: Request) -> Response:
        strict_query(request, [])
        res = daily_with_notes(request.app.state.pool, "latest")
        if not res:
            return problem(request, 404, "not_found", "還沒有發布過日報。")
        return markdown(
            request, daily_answer(res["body"]["report"], "http", res["notes"]), "agent-daily", V1_OPERATIONS["latestDaily"]["cacheControl"]
        )

    @app.get("/api/v1/agent/daily/{date}")
    @public_handler
    def daily_by_date(request: Request, date: str) -> Response:
        strict_query(request, [])
        if not is_valid_date(date):
            raise QueryError("date must be a real YYYY-MM-DD calendar date.")
        res = daily_with_notes(request.app.state.pool, date)
        if not res:
            return problem(request, 404, "not_found", f"沒有 {date} 的日報；不要換一天冒充。", cache_control="public, max-age=60")
        return markdown(
            request, daily_answer(res["body"]["report"], "http", res["notes"]), "agent-daily", V1_OPERATIONS["dailyByDate"]["cacheControl"]
        )

    def periodic(kind: str, name: str, param: str, form: str, latest_cache: str, by_key_cache: str) -> None:
        @app.get(f"/api/v1/agent/{kind}")
        @public_handler
        def latest_period(request: Request) -> Response:
            strict_query(request, [])
            body = v1_period(request.app.state.pool, kind, "latest")
            if not body:
                return problem(request, 404, "not_found", f"還沒有發布過{name}。")
            return markdown(request, period_answer(body["report"], kind, "http"), f"agent-{kind}", latest_cache)

        @app.get("/api/v1/agent/" + kind + "/{key}")
        @public_handler
        def period_by_key(request: Request, key: str) -> Response:
            strict_query(request, [])
            if not is_period_key(kind, key):
                raise QueryError(f"{param} must be {form}.")
            body = v1_period(request.app.state.pool, kind, key)
            if not body:
                return problem(request, 404, "not_found", f"沒有 {key} 的{name}；不要換一期冒充。", cache_control="public, max-age=60")
            return markdown(request, period_answer(body["report"], kind, "http"), f"agent-{kind}", by_key_cache)

    ops = V1_OPERATIONS
    periodic("weekly", "週報", "week", "a real ISO week such as 2026-W39", ops["latestWeekly"]["cacheControl"], ops["weeklyByWeek"]["cacheControl"])
    periodic("monthly", "月報", "month", "a real month such as 2026-09", ops["latestMonthly"]["cacheControl"], ops["monthlyByMonth"]["cacheControl"])
