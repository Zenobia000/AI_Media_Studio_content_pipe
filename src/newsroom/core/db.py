"""The PostgreSQL connection (the same database AIHOT's site uses; the migrations of the collect and fetch tables live in core/migrations)."""

from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.adapt import Loader
from psycopg.rows import dict_row


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("沒有設定 DATABASE_URL")
    return url


class _FloatNumeric(Loader):
    """numeric comes back as a float, as the TypeScript engine read it (int8 is already an int): ids and scores in this schema stay far
    below 2^53, and a JSON response must carry numbers, not strings."""

    def load(self, data):
        return float(bytes(data).decode())


def connect(url: str | None = None) -> psycopg.Connection[dict[str, Any]]:
    """A connection that returns rows as dicts and commits each statement unless used inside `with conn.transaction()`."""
    conn = psycopg.connect(url or database_url(), row_factory=dict_row, autocommit=True)
    conn.adapters.register_loader("numeric", _FloatNumeric)
    return conn
