"""The PostgreSQL connection (the same database AIHOT's site uses; the migrations of the collect and fetch tables live in core/migrations)."""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("沒有設定 DATABASE_URL")
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    """A connection that returns rows as dicts and commits each statement unless used inside `with conn.transaction()`."""
    return psycopg.connect(url or database_url(), row_factory=dict_row, autocommit=True)
