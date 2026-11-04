"""MCP: /api/mcp, remote Streamable HTTP, anonymous, read-only, stateless, no push (port of apps/api/src/routes/mcp.ts). This file is the HTTP side:
which hosts and origins may call it, CORS, and the checks the SDK makes before a message reaches a tool."""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import problem
from newsroom.engine import pack
from newsroom.engine.config import config
from newsroom.engine.publication.mcp import SUPPORTED_VERSIONS, handle_message, sse

log = logging.getLogger("newsroom.api")

MAX_REQUEST_BODY_SIZE = 256 * 1024
CORS_METHODS = "POST, GET, DELETE, OPTIONS"
CORS_HEADERS = "Content-Type, Accept, MCP-Protocol-Version, MCP-Session-Id, Last-Event-ID, MCP-Method, MCP-Name"


def hostname_from_authority(authority: str | None) -> str | None:
    """The host of a Host / X-Forwarded-Host value: one host with an optional port, nothing else."""
    if not isinstance(authority, str):
        return None
    m = re.fullmatch(r"(\[[0-9a-f:.]+\]|[a-z0-9._-]+)(?::([0-9]+))?", authority, re.I)
    if not m or (m.group(2) is not None and int(m.group(2)) > 65535):
        return None
    host = m.group(1).lower()
    if not host.startswith("["):
        return host
    return host


def allowed_hosts() -> set[str]:
    site = pack.site()
    origin_host = (site.get("DEPLOYMENT") or {}).get("originHost")
    names = [
        urlsplit(config().site_url).hostname,
        origin_host,
        "localhost",
        "127.0.0.1",
        "[::1]",
        *(os.environ.get("MCP_ALLOWED_HOSTS") or "").split(","),
    ]
    return {h for h in (hostname_from_authority(n.strip()) for n in names if n) if h}


def allowed_origin(origin: str | None) -> bool:
    """Browsers on the site and local development addresses."""
    if not origin:
        return True
    try:
        u = urlsplit(origin)
        site = urlsplit(config().site_url)
        if u.scheme == site.scheme and u.hostname == site.hostname:
            return True
        return u.hostname in ("localhost", "127.0.0.1") and u.scheme in ("http", "https")
    except ValueError:
        return False


def cors_headers(origin: str | None) -> dict[str, str]:
    out = {"Vary": "Origin"}
    if origin:
        expose = ["MCP-Protocol-Version", "MCP-Session-Id", "Link", *((pack.site()["POLICY"]["terms"].get("headers") or {}).keys())]
        out["Access-Control-Allow-Origin"] = origin
        out["Access-Control-Expose-Headers"] = ", ".join(expose)
    return out


def rpc_error(status: int, code: int, message: str, msg_id=None, headers: dict | None = None) -> Response:
    body = json.dumps({"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": msg_id}, ensure_ascii=False, separators=(",", ":"))
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def register_mcp(app: FastAPI) -> None:
    allowed = allowed_hosts()

    @app.api_route("/api/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def serve(request: Request) -> Response:
        origin = request.headers.get("origin")
        base = {"Cache-Control": "no-store"}
        authority_header = "host" if "x-forwarded-host" not in request.headers else "x-forwarded-host"
        values = request.headers.getlist(authority_header)
        host = hostname_from_authority(values[0]) if len(values) == 1 else None
        if host is None or host not in allowed:
            return Response('{"error":"misdirected_request"}', status_code=421, media_type="application/json", headers=base)
        if not allowed_origin(origin):
            return Response('{"error":"origin_not_allowed"}', status_code=403, media_type="application/json", headers=base)
        headers = {**base, **cors_headers(origin)}
        raw = await request.body()
        if len(raw) > MAX_REQUEST_BODY_SIZE:
            return rpc_error(413, -32000, "MCP request body is too large.", headers=headers)
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        body = None
        if request.method != "GET" and (raw or content_type):
            # The server framework reads the body first: JSON is parsed, text is kept as it is, anything else is refused.
            if content_type == "application/json":
                try:
                    body = json.loads(raw)
                except ValueError:
                    return problem(request, 400, "invalid_request", "The request could not be processed.")
            elif content_type == "text/plain":
                body = raw.decode("utf-8", "replace")
            else:
                return problem(request, 415, "invalid_request", "The request could not be processed.")
        elif request.method == "POST" and not raw and content_type:
            return problem(request, 400, "invalid_request", "The request could not be processed.")
        if request.method == "POST" and isinstance(body, list):
            return rpc_error(400, -32600, "Batch requests are not supported", headers=headers)
        # Nothing is ever pushed, so there is nothing to listen for.
        if request.method == "POST" and isinstance(body, dict) and body.get("method") == "subscriptions/listen":
            mid = body.get("id")
            return rpc_error(404, -32601, "Method not found", mid if isinstance(mid, (str, int)) and not isinstance(mid, bool) else None, headers)
        if request.method == "GET":
            return rpc_error(405, -32000, "Method not allowed.", headers=headers)
        if request.method == "DELETE":
            return rpc_error(405, -32000, "Method not allowed", headers=headers)
        accept = (request.headers.get("accept") or "").lower()
        if "application/json" not in accept or "text/event-stream" not in accept:
            return rpc_error(406, -32000, "Not Acceptable: Client must accept both application/json and text/event-stream", headers=headers)
        if content_type != "application/json":
            return rpc_error(415, -32000, "Unsupported Media Type: Content-Type must be application/json", headers=headers)
        if isinstance(body, str):
            return rpc_error(400, -32700, "Parse error: Invalid JSON", headers=headers)
        invalid = "Bad Request: the request body is not a valid JSON-RPC message"
        if not isinstance(body, dict):
            return rpc_error(400, -32600, invalid, headers=headers)
        mid = body.get("id")
        id_ok = "id" not in body or (isinstance(mid, (str, int)) and not isinstance(mid, bool))
        is_request = body.get("jsonrpc") == "2.0" and isinstance(body.get("method"), str) and id_ok
        is_response = body.get("jsonrpc") == "2.0" and ("result" in body or "error" in body) and "method" not in body and "id" in body
        if not is_request and not is_response:
            return rpc_error(400, -32600, invalid, mid if id_ok and "id" in body else None, headers)
        if is_response or "id" not in body:
            return Response(status_code=202, headers=headers)
        version = request.headers.get("mcp-protocol-version")
        if version is not None and body["method"] != "initialize" and version not in SUPPORTED_VERSIONS:
            return rpc_error(
                400,
                -32000,
                f"Bad Request: Unsupported protocol version: {version} (supported versions: {', '.join(SUPPORTED_VERSIONS)})",
                headers=headers,
            )
        try:
            answer = await run_in_threadpool(handle_message, request.app.state.pool, body)
        except Exception:
            log.exception("mcp error")
            return rpc_error(500, -32603, "Internal error", headers=headers)
        return Response(sse(answer), media_type="text/event-stream", headers={**headers, "X-Accel-Buffering": "no"})

    @app.options("/api/mcp", include_in_schema=False)
    def preflight(request: Request) -> Response:
        origin = request.headers.get("origin")
        if not allowed_origin(origin):
            return Response('{"error":"origin_not_allowed"}', status_code=403, media_type="application/json", headers={"Cache-Control": "no-store"})
        return Response(
            status_code=204,
            headers={"Cache-Control": "no-store", **cors_headers(origin), "Access-Control-Allow-Methods": CORS_METHODS,
                     "Access-Control-Allow-Headers": CORS_HEADERS, "Access-Control-Max-Age": "600", "Allow": CORS_METHODS},
        )  # fmt: skip

    @app.api_route("/api/mcp", methods=["PUT", "PATCH"], include_in_schema=False)
    def not_allowed() -> Response:
        return rpc_error(405, -32000, "Method not allowed", headers={"Allow": CORS_METHODS, "Cache-Control": "no-store"})
