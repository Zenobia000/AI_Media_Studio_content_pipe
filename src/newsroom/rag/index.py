"""Embeds the analysed articles and searches them. Vectors go in AIHOT's `embeddings` table (kind 'article', model = RAG_EMBED_MODEL), which event
grouping reads only for its own model, so the two do not mix. No pgvector: the vectors are compared in memory, which is enough for thousands of articles.

The text of an article is its Chinese title and summary from the latest model analysis that judged it relevant. An article whose text did not change
is not embedded again."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np

from newsroom.engine.publication.scope import listed_condition, scope_params

log = logging.getLogger("newsroom.rag")
EmbedFn = Callable[[list[str]], list[list[float]]]
BATCH = 16


class EmbedError(RuntimeError):
    """The embedding server could not be reached or answered with something else than vectors."""


def embed_model() -> str:
    return os.environ.get("RAG_EMBED_MODEL") or "bge-m3"


def embed_url() -> str:
    return (os.environ.get("RAG_EMBED_URL") or "http://127.0.0.1:11434").rstrip("/")


def embed(texts: list[str]) -> list[list[float]]:
    """Vectors from an Ollama server (/api/embed), in the order of `texts`."""
    try:
        res = httpx.post(f"{embed_url()}/api/embed", json={"model": embed_model(), "input": texts}, timeout=300)
        res.raise_for_status()
        vectors = res.json()["embeddings"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise EmbedError(f"embedding failed ({embed_url()}, model {embed_model()}): {e}") from e
    if len(vectors) != len(texts):
        raise EmbedError(f"asked for {len(texts)} vectors, got {len(vectors)}")
    return vectors


def _unit(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


def article_texts(conn) -> list[dict[str, Any]]:
    """The articles to index: the latest model analysis of each, when it judged the article relevant and wrote a title and summary."""
    return conn.execute(
        """SELECT a.id, a.title, a.url, an.title_zh, an.summary_zh
           FROM (SELECT DISTINCT ON (article_id) * FROM analyses WHERE origin = 'model' ORDER BY article_id, id DESC) an
           JOIN articles a ON a.id = an.article_id
           WHERE an.relevance = 'pass' AND an.title_zh IS NOT NULL AND an.summary_zh IS NOT NULL
           ORDER BY a.id"""
    ).fetchall()


def _text(row: dict[str, Any]) -> str:
    return f"{row['title_zh']}\n{row['summary_zh']}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def index_articles(conn, embed_fn: EmbedFn = embed, limit: int | None = None) -> dict[str, int]:
    """Embeds the articles that have no vector yet or whose text changed; `limit` caps how many are embedded in this run."""
    model = embed_model()
    rows = article_texts(conn)
    have = {r["ref_id"]: r["text_hash"] for r in conn.execute("SELECT ref_id, text_hash FROM embeddings WHERE kind = 'article' AND model = %s", (model,)).fetchall()}
    todo = [r for r in rows if have.get(r["id"]) != _hash(_text(r))]
    if limit is not None:
        todo = todo[:limit]
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        vectors = _unit(embed_fn([_text(r) for r in batch]))
        for row, vector in zip(batch, vectors, strict=True):
            conn.execute(
                """INSERT INTO embeddings (kind, ref_id, model, text_hash, vector) VALUES ('article', %s, %s, %s, %s)
                   ON CONFLICT (kind, ref_id, model) DO UPDATE SET text_hash = EXCLUDED.text_hash, vector = EXCLUDED.vector, created_at = now()""",
                (row["id"], model, _hash(_text(row)), [float(x) for x in vector]),
            )
    return {"candidates": len(rows), "embedded": len(todo), "already_indexed": len(rows) - len([r for r in rows if have.get(r["id"]) != _hash(_text(r))])}


def index_pending(pool, limit: int = 200) -> dict[str, int | str]:
    """The worker's timer: embeds the articles that have no vector yet, at most `limit` a run. A server that is not up is not a failed run: the articles wait
    for the next one, and the reason is in the result."""
    try:
        with pool.connection() as conn:
            done = index_articles(conn, limit=limit)
    except EmbedError as e:
        log.warning("rag index skipped: %s", e)
        return {"skipped": str(e)}
    return {"embedded": done["embedded"], "candidates": done["candidates"]}


def search(conn, query: str, k: int = 5, embed_fn: EmbedFn = embed, now: datetime | None = None) -> list[dict[str, Any]]:
    """The `k` indexed articles closest to `query`, best first, with cosine similarity as `score`. Only articles listed publicly at `now` are searched:
    an article that is withdrawn, not eligible or not yet released is indexed but is never found."""
    stored = conn.execute(
        f"""SELECT e.ref_id, e.vector, a.title, a.url, an.title_zh, an.summary_zh
           FROM embeddings e JOIN articles a ON a.id = e.ref_id
           JOIN publications p ON p.article_id = a.id
           LEFT JOIN LATERAL (SELECT title_zh, summary_zh FROM analyses WHERE article_id = a.id AND origin = 'model' ORDER BY id DESC LIMIT 1) an ON true
           WHERE e.kind = 'article' AND e.model = %(model)s AND {listed_condition()}""",
        {**scope_params(now or datetime.now(UTC)), "model": embed_model()},
    ).fetchall()
    if not stored:
        return []
    scores = _unit([r["vector"] for r in stored]) @ _unit(embed_fn([query]))[0]
    best = np.argsort(-scores)[: max(k, 0)]
    return [
        {
            "article_id": stored[i]["ref_id"],
            "score": round(float(scores[i]), 4),
            "title": stored[i]["title_zh"] or stored[i]["title"],
            "summary": stored[i]["summary_zh"],
            "url": stored[i]["url"],
        }
        for i in best
    ]
