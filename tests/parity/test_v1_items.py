"""/api/v1/items on the seeded dataset (AIHOT/scripts/seed-parity.ts)."""

from __future__ import annotations

import pytest

PATHS = [
    "/api/v1/items",
    "/api/v1/items?limit=3",
    "/api/v1/items?mode=all",
    "/api/v1/items?mode=all&limit=5",
    "/api/v1/items?mode=all&window=24h",
    "/api/v1/items?mode=all&by=published&limit=7",
    "/api/v1/items?mode=selected&category=ai-models",
    "/api/v1/items?mode=all&category=paper",
    "/api/v1/items?q=Event",
    "/api/v1/items?q=%E4%B8%AD%E6%96%87&mode=all",
    "/api/v1/items?q=announced",
    "/api/v1/items?q=zzzznomatch",
    "/api/v1/items?mode=all&limit=100",
]
BAD = [
    "/api/v1/items?mode=nope",
    "/api/v1/items?window=1h",
    "/api/v1/items?limit=0",
    "/api/v1/items?limit=101",
    "/api/v1/items?limit=x",
    "/api/v1/items?q=a",
    "/api/v1/items?category=nope",
    "/api/v1/items?unknown=1",
    "/api/v1/items?mode=all&mode=all",
    "/api/v1/items?cursor=",
    "/api/v1/items?cursor=garbage",
    "/api/v1/items?cursor=it3.e30",
]


@pytest.mark.parametrize("path", PATHS)
def test_items(compare, path):
    got = compare(path)
    assert got["status"] == 200 and "etag" in got["headers"]


@pytest.mark.parametrize("path", BAD)
def test_bad_requests(compare, path):
    got = compare(path)
    assert got["status"] == 400


def test_the_dataset_has_something_to_compare(compare):
    got = compare("/api/v1/items?mode=all&limit=100")
    assert len(got["body"]["items"]) >= 10
    assert any(i["selected"] for i in got["body"]["items"])


def test_paging_with_a_cursor(compare, node_api):
    import httpx

    first = compare("/api/v1/items?mode=all&limit=4")
    cursor = first["body"]["page"]["nextCursor"]
    assert first["body"]["page"]["hasMore"] and cursor
    second = compare(f"/api/v1/items?mode=all&limit=4&cursor={cursor}")
    assert [i["id"] for i in second["body"]["items"]] != [i["id"] for i in first["body"]["items"]]
    # A cursor belongs to its own query.
    other = compare(f"/api/v1/items?mode=all&limit=4&window=24h&cursor={cursor}")
    assert other["status"] == 400
    assert httpx.get(node_api + "/api/health").status_code == 200


def test_conditional_request(compare):
    first = compare("/api/v1/items?mode=all&limit=3")
    again = compare("/api/v1/items?mode=all&limit=3", headers={"if-none-match": first["headers"]["etag"]})
    assert again["status"] == 304
