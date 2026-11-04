"""/api/site hot, story pages, followups and reading groups on the seeded dataset."""

from __future__ import annotations

import pytest


def test_hot(compare):
    got = compare("/api/site/hot")
    assert got["status"] == 200 and len(got["body"]["entries"]) >= 3
    first = got["body"]["entries"][0]
    assert first["participants"] and len(first["spark"]) == 25


def test_every_hot_story_page_and_its_followups(compare):
    for entry in compare("/api/site/hot")["body"]["entries"]:
        pid = entry["story"]["publicId"]
        page = compare(f"/api/site/stories/{pid}")
        assert page["status"] == 200 and page["body"]["developments"] and page["body"]["timeline"]
        follow = compare(f"/api/site/stories/{pid}/followups")
        assert follow["status"] in (200, 404)


@pytest.mark.parametrize(
    "path",
    [
        "/api/site/stories/00000000-0000-0000-0000-000000000000",
        "/api/site/stories/nope",
        "/api/site/stories/nope/followups",
        "/api/site/groups/nope/reports",
        "/api/site/groups/00000000-0000-0000-0000-000000000000/reports",
    ],
)
def test_missing(compare, path):
    assert compare(path)["status"] == 404


def test_group_reports(compare):
    page = compare(f"/api/site/stories/{compare('/api/site/hot')['body']['entries'][0]['story']['publicId']}")
    fact = page["body"]["developments"][0]["factId"]
    got = compare(f"/api/site/groups/{fact}/reports")
    assert got["status"] == 200 and got["body"]["reports"]
    compare(f"/api/site/groups/{fact}/reports?channel=x")
    assert compare(f"/api/site/groups/{fact}/reports?channel=nope")["status"] == 400
