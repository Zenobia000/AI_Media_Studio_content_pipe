"""Ids and hashes: port of packages/backend/src/lib/ids.ts."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
import uuid

_ALPHABET = string.ascii_lowercase + string.digits


def new_article_id() -> str:
    """25 lowercase alphanumerics starting with a letter (like cuid2): matches ^[a-zA-Z0-9_-]{1,80}$."""
    return secrets.choice(string.ascii_lowercase) + "".join(secrets.choice(_ALPHABET) for _ in range(24))


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_short_id(nbytes: int = 9) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode()


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def short_hash(value: str, length: int = 16) -> str:
    return sha256(value)[:length]


def _sorted(value):
    if isinstance(value, dict):
        return {k: _sorted(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted(v) for v in value]
    return value


def stable_json(value) -> str:
    """Stable JSON (sorted keys, no spaces) for hashing request identities and payloads, like JSON.stringify with sorted keys."""
    return json.dumps(_sorted(value), ensure_ascii=False, separators=(",", ":"))
