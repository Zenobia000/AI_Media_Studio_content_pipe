"""POST /api/chat: the website's chat window, a conversation that is limited to the site's articles (newsroom.rag.ask). The body is
{"messages": [{"role": "user" | "assistant", "content": "..."}, ...]}, the last one a user's question. The answer is a stream of JSON lines:
{"type": "sources", "found": bool, "sources": [...]}, then {"type": "delta", "text": "..."} pieces, then {"type": "done"}; {"type": "error", "code": "..."}
if it breaks. A question the articles do not cover gets found=false with the "not found" text as its one delta, and the model is not asked for it."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import FastAPI, Request, Response
from starlette.responses import StreamingResponse

from newsroom.api.respond import js_json, problem
from newsroom.rag.ask import chat_stream, plan
from newsroom.rag.index import EmbedError

log = logging.getLogger("newsroom.api")
MAX_QUESTION = 500
MAX_MESSAGES = 40


def _line(obj: dict) -> bytes:
    return (js_json(obj) + "\n").encode()


def _stream(pool, history: list[dict[str, str]], question: str) -> Iterator[bytes]:
    try:
        with pool.connection() as conn:
            p = plan(conn, question, history)
        yield _line({"type": "sources", "kind": p.kind, "found": bool(p.sources), "sources": [{k: h[k] for k in ("n", "title", "url", "score")} for h in p.sources]})
        if p.reply is not None:
            yield _line({"type": "delta", "text": p.reply})
        else:
            for piece in chat_stream(p.messages or []):
                yield _line({"type": "delta", "text": piece})
        yield _line({"type": "done"})
    except EmbedError:
        log.exception("chat model unavailable")
        yield _line({"type": "error", "code": "model_unavailable"})
    except Exception:
        log.exception("chat failed")
        yield _line({"type": "error", "code": "internal"})


def register_chat(app: FastAPI) -> None:
    @app.post("/api/chat", include_in_schema=False)
    async def chat(request: Request) -> Response:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return problem(request, 400, "invalid_request", "The request body must be JSON.")
        raw = body.get("messages") if isinstance(body, dict) else None
        valid = (
            isinstance(raw, list)
            and 0 < len(raw) <= MAX_MESSAGES
            and all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) for m in raw)
        )
        messages: list[dict[str, str]] = raw if valid and isinstance(raw, list) else []
        question = messages[-1]["content"].strip() if messages and messages[-1]["role"] == "user" else ""
        if not question or len(question) > MAX_QUESTION:
            return problem(request, 400, "invalid_request", f"messages must end with a user message of 1 to {MAX_QUESTION} characters.")
        history = [{"role": m["role"], "content": m["content"]} for m in messages[:-1]]
        # The generator blocks on the database and the model, so the framework runs it in a thread.
        return StreamingResponse(
            _stream(request.app.state.pool, history, question),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
