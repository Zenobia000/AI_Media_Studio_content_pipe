"""/api/site/items/:id (and /original) for every kind of item the seeded dataset holds."""

from __future__ import annotations

import psycopg
import pytest


@pytest.fixture(scope="module")
def ids(request):
    import os

    with psycopg.connect(os.environ["PARITY_DATABASE_URL"]) as conn:
        return [r[0] for r in conn.execute("SELECT article_id FROM publications ORDER BY article_id")]


def test_every_item(compare, ids):
    assert ids
    seen, bodies = set(), set()
    for item_id in ids:
        for suffix in ("", "/original"):
            got = compare(f"/api/site/items/{item_id}{suffix}")
            seen.add(got["status"])
            body = got["body"].get("body") if got["status"] == 200 else None
            if body:
                bodies.add((body["zhKind"], got["body"]["bodyLanguage"], bool(got["body"]["outline"])))
    assert seen == {200, 404}
    assert len(bodies) >= 4, bodies


@pytest.mark.parametrize("path", ["/api/site/items/nope", "/api/site/items/bad%20id", "/api/site/items/" + "x" * 81])
def test_missing(compare, path):
    compare(path)


def test_markdown_export(compare, ids):
    statuses = set()
    for item_id in ids:
        got = compare(f"/items/{item_id}/markdown")
        statuses.add(got["status"])
    assert statuses == {200, 404}
    compare("/items/bad%20id/markdown")
