"""Shared fixtures. Database tests write rows, so they refuse to run unless DATABASE_URL names a throwaway database ending in _test or
_ci; without one they are skipped. The database is brought up from AIHOT's engine migrations plus ours (the same two sets of SQL the
site uses)."""

from __future__ import annotations

import itertools
import os
import re
from pathlib import Path

import pytest

from newsroom.core import db as dbmod
from newsroom.core.migrate import apply_dir

ENGINE_MIGRATIONS = Path(__file__).resolve().parent.parent / "AIHOT" / "database" / "migrations"
_counter = itertools.count(1)


def _database_name() -> str | None:
    url = os.environ.get("DATABASE_URL")
    return url.rsplit("/", 1)[-1].split("?")[0] if url else None


@pytest.fixture(scope="session")
def conn():
    name = _database_name()
    if not name or not re.search(r"_(test|ci)$", name):
        pytest.skip("DATABASE_URL must name a throwaway database ending in _test or _ci")
    c = dbmod.connect()
    if ENGINE_MIGRATIONS.exists():
        apply_dir(c, ENGINE_MIGRATIONS)
    apply_dir(c)
    yield c
    c.close()


@pytest.fixture()
def tag() -> str:
    """A string unique to this test run, for ids that must not collide with other tests' rows."""
    return f"{os.getpid()}x{next(_counter)}"
