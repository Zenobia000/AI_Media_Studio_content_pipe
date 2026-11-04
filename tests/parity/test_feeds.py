"""RSS feeds on the seeded dataset."""

from __future__ import annotations

import pytest

from newsroom.engine.taxonomy import public_api_category_keys


@pytest.mark.parametrize("path", ["/feed.xml", "/feed/full.xml", "/feed/all.xml", "/feed/daily.xml", "/feed/weekly.xml", "/feed/monthly.xml"])
def test_feed(compare, path):
    got = compare(path)
    assert got["status"] == 200
    if path == "/feed/full.xml":
        assert "content:encoded" in got["body"]


def test_category_feeds(compare):
    for key in public_api_category_keys():
        assert compare(f"/feed/category/{key}.xml")["status"] == 200
        assert compare(f"/feed/full/category/{key}.xml")["status"] == 200
    for path in ["/feed/category/nope.xml", "/feed/category/ai", "/feed.xml?x=1"]:
        compare(path)
