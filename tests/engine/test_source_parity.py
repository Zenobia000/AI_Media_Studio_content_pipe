"""The collectors' pure helpers must give what the TypeScript ones give (source_fixtures.json: AIHOT/scripts/gen-source-fixtures.ts)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from newsroom.engine.sources import dates as D

F = json.loads((Path(__file__).parent / "source_fixtures.json").read_text(encoding="utf-8"))


def iso(d: datetime | None) -> str | None:
    return None if d is None else D._iso(d)


@pytest.mark.parametrize("c", F["parseLooseDate"], ids=lambda c: f"{c['in']!r}|{c['offset']}")
def test_parse_loose_date(c):
    assert iso(D.parse_loose_date(c["in"], c["offset"])) == c["out"]


@pytest.mark.parametrize("c", F["readPublicationDate"], ids=lambda c: repr(c["in"]))
def test_read_publication_date(c):
    r = D.read_publication_date(c["in"])
    assert {"at": iso(r.published_at), "time": r.publication_time} == c["out"]


@pytest.mark.parametrize("c", F["readRelativeDate"], ids=lambda c: f"{c['in']!r}|{c['offset']}")
def test_read_relative_date(c):
    now = datetime.fromisoformat(F["now"].replace("Z", "+00:00"))
    r = D.read_relative_date(c["in"], now, c["offset"])
    assert {"at": iso(r.published_at), "time": r.publication_time} == c["out"]
