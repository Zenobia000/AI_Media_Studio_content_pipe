"""Text embeddings through receipts, used only for recall. Any OpenAI-compatible /embeddings endpoint (EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL);
with a DashScope key and nothing else set, Aliyun text-embedding-v4 at 1024 dimensions. Without either, recall compares the texts themselves. Port of
providers/embeddings.ts."""

from __future__ import annotations

import json
import os

import httpx

from newsroom.engine.config import config, credential
from newsroom.engine.lib.ids import sha256
from newsroom.engine.lib.text import js_slice
from newsroom.engine.receipts import (
    CallOutcome,
    ModelOutputError,
    ModelUnavailableError,
    ReceiptRequest,
    assert_accepted,
    complete_receipt,
    paid_request,
    reject_received_response,
)  # fmt: skip


def _own() -> bool:
    return bool(credential("models", "EMBEDDING_API_KEY"))


def embedding_model() -> str:
    return os.environ.get("EMBEDDING_MODEL") or ("text-embedding-3-small" if _own() else "text-embedding-v4")


def embedding_dims() -> int:
    """Requested dimensions, when the provider takes the parameter (0 leaves it to the model)."""
    raw = os.environ.get("EMBEDDING_DIMS")
    if raw is None:
        return 0 if _own() else 1024
    try:
        return int(float(raw.strip() or 0))
    except ValueError:
        return 0


def embeddings_available() -> bool:
    """Embeddings are paid model calls: MODEL_CALLS_ENABLED=false switches them off like every other call."""
    return config().model_calls_enabled and bool(credential("models", "EMBEDDING_API_KEY") or credential("models", "DASHSCOPE_API_KEY"))


def _valid_vector(v, dims: int) -> bool:
    """A vector of the configured size; a stored one of another size (EMBEDDING_DIMS changed) is computed again."""
    if not isinstance(v, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return False
    return len(v) == dims if dims > 0 else len(v) >= 1


def _embed_batch(pool, texts: list[str], subject: str) -> tuple[list[list[float]], int]:
    if not config().model_calls_enabled:
        raise ModelUnavailableError("Model calls are disabled (MODEL_CALLS_ENABLED=false)")
    own, model, dims = _own(), embedding_model(), embedding_dims()
    base = (
        (credential("models", "EMBEDDING_BASE_URL") or "https://api.openai.com/v1")
        if own
        else (credential("models", "DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1")
    )
    key = credential("models", "EMBEDDING_API_KEY") if own else credential("models", "DASHSCOPE_API_KEY")
    if not key:
        raise ModelUnavailableError("EMBEDDING_API_KEY (or DASHSCOPE_API_KEY) missing")
    service = "embedding" if own else "dashscope"

    def call(permit=None) -> CallOutcome:
        body = {"model": model, "input": texts, **({"dimensions": dims} if dims > 0 else {}), "encoding_format": "float"}
        res = httpx.post(
            f"{base.rstrip('/')}/embeddings", headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
            content=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(), timeout=60,
        )  # fmt: skip
        text = res.content.decode("utf-8", "replace")
        assert_accepted(service, res.status_code, text)
        try:
            response = json.loads(text)
        except ValueError:
            response = {"unparsable": js_slice(text, 0, 20000)}
        usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else None
        return CallOutcome(response=response, usage=usage, cost=None)

    receipt = paid_request(
        pool,
        ReceiptRequest(service=service, model=model, purpose="embedding", subject=subject, identity={"model": model, "dims": dims, "texts": [sha256(t) for t in texts]}, request_summary={"count": len(texts)}),
        call,
    )  # fmt: skip
    try:
        response = receipt["response"]
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError(f"data must be a list of {len(texts)} embeddings")
        for item in data:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("index"), int)
                or isinstance(item.get("index"), bool)
                or item["index"] < 0
                or not _valid_vector(item.get("embedding"), dims)
            ):
                raise ValueError("an embedding is not a vector of the configured size")
        data = sorted(data, key=lambda item: item["index"])
        if any(item["index"] != i for i, item in enumerate(data)):
            raise ValueError("Embedding indices do not match inputs")
        return [item["embedding"] for item in data], receipt["receiptId"]
    except Exception as error:
        reject_received_response(pool, receipt["receiptId"], f"unusable embeddings: {str(error)[:500]}")
        raise ModelOutputError(f"Embedding response is unusable for {subject}: {str(error)[:300]}", receipt["receiptId"]) from error


def ensure_embeddings(pool, items: list[dict]) -> dict[str, list[float]]:
    """The stored embeddings of report texts ({"id", "text"}), computing and storing the missing ones."""
    out: dict[str, list[float]] = {}
    if not items:
        return out
    model, dims = embedding_model(), embedding_dims()
    hashes = {item["id"]: sha256(item["text"]) for item in items}
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT ref_id, text_hash, vector FROM embeddings WHERE kind = 'article' AND model = %s AND ref_id = ANY(%s)",
            (model, [i["id"] for i in items]),
        ).fetchall()
    have = {r["ref_id"]: r for r in rows}
    missing = []
    for item in items:
        stored = have.get(item["id"])
        vector = stored["vector"] if stored and stored["text_hash"] == hashes[item["id"]] else None
        if vector is not None and _valid_vector(vector, dims):
            out[item["id"]] = vector
        else:
            missing.append(item)
    for i in range(0, len(missing), 10):
        batch = missing[i : i + 10]
        vectors, receipt_id = _embed_batch(pool, [js_slice(b["text"], 0, 2000) for b in batch], f"article:{batch[0]['id']}")
        with pool.connection() as conn, conn.transaction():
            for item, v in zip(batch, vectors, strict=True):
                conn.execute(
                    """INSERT INTO embeddings (kind, ref_id, model, text_hash, vector) VALUES ('article', %s, %s, %s, %s)
                       ON CONFLICT (kind, ref_id, model) DO UPDATE SET text_hash = EXCLUDED.text_hash, vector = EXCLUDED.vector, created_at = now()""",
                    (item["id"], model, hashes[item["id"]], v),
                )
            complete_receipt(conn, receipt_id)
        for item, v in zip(batch, vectors, strict=True):
            out[item["id"]] = v
    return out
