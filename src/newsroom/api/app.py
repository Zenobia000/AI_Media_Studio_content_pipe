"""The HTTP API (apps/api): the same routes, headers and error format as the TypeScript server, which the website's server reads over HTTP."""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from newsroom.api.respond import problem
from newsroom.api.routes.agent import register_agent
from newsroom.api.routes.feeds import register_feeds
from newsroom.api.routes.site import register_site
from newsroom.api.routes.static import register_static
from newsroom.api.routes.v1 import register_v1, register_v1_fallbacks
from newsroom.core.db import _FloatNumeric
from newsroom.engine.config import config
from newsroom.engine.http_policy import OAUTH_PROBE_PATHS, resolve_redirect

log = logging.getLogger("newsroom.api")


def _configure(conn) -> None:
    from psycopg.rows import dict_row  # noqa: PLC0415

    conn.row_factory = dict_row
    conn.adapters.register_loader("numeric", _FloatNumeric)


def build_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Keep connections through quiet minutes: a reconnect costs a SCRAM exchange on the next request.
        pool = ConnectionPool(
            database_url or config().database_url, min_size=1, max_size=int(os.environ.get("DATABASE_POOL_MAX") or 10), max_idle=600,
            timeout=10, configure=_configure, kwargs={"autocommit": True}, open=False,
        )  # fmt: skip
        pool.open()
        app.state.pool = pool
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def request_scope(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        path, query = request.url.path, request.url.query
        if path in OAUTH_PROBE_PATHS:  # OAuth discovery probes from MCP clients: a cheap 404, never a rendered page
            return Response(
                '{"error":"not_found"}', status_code=404, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"}
            )
        decision = resolve_redirect(path, f"?{query}" if query else "")
        if decision:
            headers = dict(decision.headers)
            if decision.location:
                return Response(status_code=decision.status, headers={**headers, "Location": decision.location})
            return Response("Gone" if decision.status == 410 else "Not found", status_code=decision.status, media_type="text/plain", headers=headers)
        started = time.monotonic()
        response = await call_next(request)
        ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 500 or (ms >= 1000 and path != "/api/mcp" and not path.startswith("/api/img-proxy")):
            log.warning('{"method":"%s","path":"%s","status":%s,"ms":%s,"msg":"request"}', request.method, path, response.status_code, ms)
        return response

    @app.get("/api/health")
    def health(request: Request) -> Response:
        started = time.monotonic()
        with request.app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        body = f'{{"ok":true,"db":"ok","ms":{round((time.monotonic() - started) * 1000)},"release":"{os.environ.get("AIHOT_RELEASE", "dev")}"}}'
        return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})

    register_site(app)
    register_feeds(app)
    register_static(app)
    register_v1(app)
    register_agent(app)
    register_v1_fallbacks(app)

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException):
        request.state.__dict__.setdefault("request_id", str(uuid.uuid4()))
        if exc.status_code in (404, 405):
            if request.url.path.startswith("/api/"):
                return problem(request, 404, "not_found", "No such endpoint.")
            return Response("Not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "public, max-age=60"})
        return problem(request, exc.status_code, "invalid_request", "The request could not be processed.")

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return problem(request, 400, "invalid_request", "The request could not be processed.")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        request.state.__dict__.setdefault("request_id", str(uuid.uuid4()))
        log.error("unhandled", exc_info=exc)
        return problem(request, 503, "temporarily_unavailable", "Temporarily unavailable.", retry_after=30)

    return app
