"""/api/v1/hot-topics and /api/v1/stories/:id on the seeded dataset."""

from __future__ import annotations

import httpx
import pytest


def test_hot_topics(compare):
    got = compare("/api/v1/hot-topics")
    assert got["status"] == 200 and got["body"]["count"] >= 3
    assert all(i["rank"] == n + 1 for n, i in enumerate(got["body"]["items"]))


def test_hot_topics_rejects_parameters(compare):
    assert compare("/api/v1/hot-topics?x=1")["status"] == 400


def test_every_story_in_the_ranking(compare):
    hot = compare("/api/v1/hot-topics")
    for item in hot["body"]["items"]:
        public_id = item["links"]["story"].rsplit("/", 1)[1]
        got = compare(f"/api/v1/stories/{public_id}")
        assert got["status"] == 200 and got["body"]["story"]["reportCount"] >= 1


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/stories/00000000-0000-0000-0000-000000000000",
        "/api/v1/stories/not-a-uuid",
        "/api/v1/stories/" + "a" * 129,
        "/api/v1/stories/x?y=1",
    ],
)
def test_missing_and_bad_stories(compare, path):
    compare(path)


def test_a_merged_story_redirects(compare, node_api, database_url):
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute("SELECT public_id::text FROM stories ORDER BY id LIMIT 2").fetchall()
        a, b = rows[0][0], rows[1][0]
        conn.execute(
            "INSERT INTO story_aliases (public_id, story_id) SELECT %s::uuid, id FROM stories WHERE public_id = %s::uuid ON CONFLICT DO NOTHING",
            (a, b),
        )
        try:
            assert compare(f"/api/v1/stories/{a}")["status"] in (200, 308)
        finally:
            conn.execute("DELETE FROM story_aliases WHERE public_id = %s::uuid", (a,))
    assert httpx.get(node_api + "/api/health").status_code == 200
