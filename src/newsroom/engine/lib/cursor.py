"""Opaque cursors: prefix + base64url(JSON). Bound to the query that produced them via a hash. Port of lib/cursor.ts."""

from __future__ import annotations

import base64
import json

from newsroom.engine.lib.ids import short_hash, stable_json


class InvalidCursorError(Exception):
    pass


def encode_cursor(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{prefix}.{base64.urlsafe_b64encode(raw).rstrip(b'=').decode()}"


def decode_cursor(prefix: str, cursor: str) -> dict:
    if not cursor.startswith(f"{prefix}."):
        raise InvalidCursorError("unknown cursor format")
    try:
        body = cursor[len(prefix) + 1 :]
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except Exception as e:  # noqa: BLE001
        raise InvalidCursorError("malformed cursor") from e


def query_binding(query) -> str:
    return short_hash(stable_json(query), 12)
