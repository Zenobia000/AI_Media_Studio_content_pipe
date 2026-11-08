"""POST /api/chat: the website's chat window. It answers only from the articles on the site (newsroom.rag.ask); a question the articles do not cover
gets "not found", and the model is not called for it. Each question stands alone: earlier turns are not sent to the search."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.api.respond import js_json, problem
from newsroom.rag.ask import ask
from newsroom.rag.index import EmbedError

log = logging.getLogger("newsroom.api")
MAX_QUESTION = 500


def _answer(pool, question: str) -> dict:
    with pool.connection() as conn:
        data = ask(conn, question)
    return {"found": data["found"], "answer": data["answer"], "sources": [{k: s[k] for k in ("n", "title", "url", "score")} for s in data["sources"]]}


def register_chat(app: FastAPI) -> None:
    @app.post("/api/chat", include_in_schema=False)
    async def chat(request: Request) -> Response:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return problem(request, 400, "invalid_request", "The request body must be JSON.")
        question = body.get("question") if isinstance(body, dict) else None
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > MAX_QUESTION:
            return problem(request, 400, "invalid_request", f"question must be 1 to {MAX_QUESTION} characters.")
        try:
            result = await run_in_threadpool(_answer, request.app.state.pool, question.strip())
        except EmbedError:
            log.exception("chat model unavailable")
            return problem(request, 503, "model_unavailable", "The chat model is not available right now.")
        return Response(js_json(result).encode(), media_type="application/json", headers={"Cache-Control": "no-store"})
