"""/api/site timeline, pool (with search) and search suggestions on the seeded dataset."""

from __future__ import annotations

from urllib.parse import quote

import pytest

TIMELINES = [
    "/api/site/timeline",
    "/api/site/timeline?limit=3",
    "/api/site/timeline?category=ai-models",
    "/api/site/timeline?channel=x",
    "/api/site/timeline?channel=firstParty",
    "/api/site/timeline?channel=news&limit=100",
    "/api/site/timeline?tag=AI",
    "/api/site/timeline?limit=0",
    "/api/site/timeline?limit=abc",
]
POOLS = [
    "/api/site/pool",
    "/api/site/pool?page=2",
    "/api/site/pool?page=99",
    "/api/site/pool?category=paper",
    "/api/site/pool?channel=firstParty",
    "/api/site/pool?tag=AI",
    "/api/site/pool?q=Event",
    "/api/site/pool?q=Event&tab=relevance",
    "/api/site/pool?q=announced&tab=relevance",
    "/api/site/pool?q=" + quote("中文"),
    "/api/site/pool?q=" + quote("中文") + "&tab=relevance",
    "/api/site/pool?q=zzzznomatch",
    "/api/site/pool?q=ev",
    "/api/site/pool?q=Event+announced&channel=news",
]


@pytest.mark.parametrize("path", TIMELINES)
def test_timeline(compare, path):
    got = compare(path, drop=("X-Accel-Expires",))
    assert got["status"] == 200


@pytest.mark.parametrize(
    "path",
    ["/api/site/timeline?channel=nope", "/api/site/timeline?category=nope", "/api/site/timeline?cursor=garbage", "/api/site/timeline?cursor=tl2.e30"],
)
def test_timeline_errors(compare, path):
    assert compare(path)["status"] == 400


def test_timeline_has_cards_groups_and_day_counts(compare):
    got = compare("/api/site/timeline")
    assert got["body"]["cards"] and got["body"]["dayCounts"]
    assert got["body"]["hot"] and len(got["body"]["hot"]) >= 3


def test_timeline_paging(compare):
    first = compare("/api/site/timeline?limit=2")
    cursor = first["body"]["nextCursor"]
    assert cursor
    second = compare(f"/api/site/timeline?limit=2&cursor={cursor}")
    assert second["body"]["cards"][0]["key"] != first["body"]["cards"][0]["key"] and second["body"]["hot"] is None
    assert compare(f"/api/site/timeline?limit=2&category=paper&cursor={cursor}")["status"] == 400


@pytest.mark.parametrize("path", POOLS)
def test_pool(compare, path):
    got = compare(path, drop=("freshness",))
    assert got["status"] == 200


def test_pool_has_items(compare):
    assert len(compare("/api/site/pool", drop=("freshness",))["body"]["items"]) >= 10


def test_suggestions(compare):
    got = compare("/api/site/search/suggestions")
    assert got["status"] == 200 and got["body"]["topics"] and got["body"]["hot"]
