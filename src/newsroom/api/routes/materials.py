"""/api/materials: the 素材區. It is for one person on their own machine, so there is no login: putting an article in and out is open like the bookmark it
follows (the area holds at most 500 articles), and a download needs only a header the site's own pages send (a page on another site cannot add one to a request
to this address, which keeps it from being started by whatever else the browser has open). Anyone who can reach the api can start downloads, so a site that is
ever opened to others needs the admin session in front of the fetch route (engine/admin/auth.py session_principal) first.

  GET    /api/materials                      the area: every pick with its state, files and gaps
  POST   /api/materials/sync                 {"ids": [...]} puts several articles in at once (the page sends the bookmarks of this browser)
  PUT    /api/materials/{id}                 puts one article in
  DELETE /api/materials/{id}                 takes one out (not while it is being downloaded)
  POST   /api/materials/{id}/fetch           {"kinds": ["article", "photo", "video"]} starts a download; 202 started, 409 already running"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import problem
from newsroom.api.routes.admin_auth import json_response
from newsroom.collect import picks

log = logging.getLogger("newsroom.api")
REQUEST_HEADER = "x-materials-request"
MAX_BODY = 64 * 1024


def _tools() -> dict[str, bool]:
    """Whether the two downloaders for videos (yt-dlp) and pictures (gallery-dl) can be found; a download works without them but gets fewer sites."""
    return {
        "ytdlp": bool(shutil.which("yt-dlp") or importlib.util.find_spec("yt_dlp")),
        "galleryDl": bool(shutil.which("gallery-dl") or importlib.util.find_spec("gallery_dl")),
    }


async def _json_body(request: Request) -> dict | None:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def register_materials(app: FastAPI) -> None:
    @app.get("/api/materials", include_in_schema=False)
    async def listing(request: Request) -> Response:
        def read() -> dict:
            with request.app.state.pool.connection() as conn:
                return {"picks": picks.list_picks(conn), "tools": _tools(), "kinds": list(picks.KINDS)}

        return json_response(await run_in_threadpool(read))

    @app.post("/api/materials/sync", include_in_schema=False)
    async def sync(request: Request) -> Response:
        body = await _json_body(request)
        ids = body.get("ids") if body else None
        if not isinstance(ids, list) or len(ids) > 1000 or not all(isinstance(i, str) for i in ids):
            return problem(request, 400, "invalid_request", "ids must be a list of at most 1000 article ids.")

        def put() -> int:
            with request.app.state.pool.connection() as conn:
                return picks.add_picks(conn, ids)

        return json_response({"added": await run_in_threadpool(put)})

    @app.put("/api/materials/{article_id}", include_in_schema=False)
    async def add(request: Request, article_id: str) -> Response:
        def put() -> int:
            with request.app.state.pool.connection() as conn:
                return picks.add_picks(conn, [article_id])

        return json_response({"added": await run_in_threadpool(put)})

    @app.delete("/api/materials/{article_id}", include_in_schema=False)
    async def remove(request: Request, article_id: str) -> Response:
        def drop() -> bool:
            with request.app.state.pool.connection() as conn:
                return picks.remove_pick(conn, article_id)

        return json_response({"removed": await run_in_threadpool(drop)})

    @app.post("/api/materials/{article_id}/fetch", include_in_schema=False)
    async def fetch(request: Request, article_id: str) -> Response:
        if request.headers.get(REQUEST_HEADER) != "1":
            return problem(request, 403, "forbidden", "This request has to come from the site's own pages.")
        body = await _json_body(request)
        kinds = body.get("kinds") if body is not None else None
        if body is None or (kinds is not None and (not isinstance(kinds, list) or not all(isinstance(k, str) and k in picks.KINDS for k in kinds))):
            return problem(request, 400, "invalid_request", f"kinds must be some of {', '.join(picks.KINDS)}.")
        outcome = await run_in_threadpool(picks.start, request.app.state.pool, article_id, kinds)
        if outcome == "missing":
            return problem(request, 404, "not_found", "That article is not in the 素材區.")
        if outcome == "busy":
            return problem(request, 409, "conflict", "A download for it is already waiting or running.")
        return json_response({"state": "queued", "wanted": picks.wanted_of(kinds)}, 202)
