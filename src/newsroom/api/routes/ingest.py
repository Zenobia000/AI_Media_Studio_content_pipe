"""POST /api/ingest/items: external collection scripts push items here with the ingest token (never an admin session); with INGEST_RATE_LIMIT set, each client's pushes
are also limited in the process (port of apps/api/src/routes/ingest.ts)."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import js_json, problem
from newsroom.engine.config import credential
from newsroom.engine.ingest import IngestError, ingest_items

log = logging.getLogger("newsroom.api")
PLACEHOLDER = re.compile(r"^(|changeme|change-me|placeholder|xxx+|todo|test|dev|your[-_]?token.*)\Z", re.I)
_windows: dict[str, list[float]] = {}
_lock = threading.Lock()


def authorized(request: Request) -> bool:
    """Constant-time check; an empty or placeholder server token rejects everything."""
    expected = credential("auth", "INGEST_TOKEN") or ""
    if PLACEHOLDER.match(expected) or len(expected) < 16:
        return False
    m = re.match(r"^Bearer\s+(.+)$", request.headers.get("authorization") or "", re.I)
    given = m.group(1).strip() if m else ""
    return hmac.compare_digest(given.encode(), expected.encode())


def rate_limit() -> int:
    try:
        return max(0, int(float(os.environ.get("INGEST_RATE_LIMIT") or 0)))
    except ValueError:
        return 0


def limited(key: str, per_minute: int) -> bool:
    now = time.time()
    with _lock:
        window = [t for t in _windows.get(key, []) if now - t < 60]
        if len(window) >= per_minute:
            return True
        window.append(now)
        _windows[key] = window
    return False


def _plain(request: Request, status: int, text: str) -> Response:
    return Response(text.encode(), status_code=status, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


def register_ingest(app: FastAPI) -> None:
    @app.post("/api/ingest/items", include_in_schema=False)
    async def ingest(request: Request) -> Response:
        # The server framework reads (and judges) the body before the handler runs.
        raw = await request.body()
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        body: object = {}
        if ctype == "application/json" and not raw:
            return problem(request, 400, "invalid_request", "The request could not be processed.")
        if raw:
            if ctype != "application/json":
                return problem(request, 415, "invalid_request", "The request could not be processed.")
            try:
                body = json.loads(raw)
            except ValueError:
                return problem(request, 400, "invalid_request", "The request could not be processed.")
        body = {} if body is None else body
        if not authorized(request):
            return _plain(request, 401, "Unauthorized")
        limit = rate_limit()
        if limit > 0 and limited(f"items:{request.client.host if request.client else ''}", limit):
            return Response(js_json({"ok": False, "error": "rate limited"}).encode(), status_code=429, media_type="application/json", headers={"Retry-After": "60", "Cache-Control": "no-store"})
        try:
            result = await run_in_threadpool(ingest_items, request.app.state.pool, body if isinstance(body, dict) else {})
        except IngestError as e:
            return Response(js_json({"ok": False, "error": str(e)}).encode(), status_code=e.status, media_type="application/json", headers={"Cache-Control": "no-store"})
        except Exception:
            log.exception("ingest items failed")
            return Response(js_json({"ok": False, "error": "internal error"}).encode(), status_code=500, media_type="application/json", headers={"Cache-Control": "no-store"})
        return Response(js_json(result).encode(), media_type="application/json", headers={"Cache-Control": "no-store"})
