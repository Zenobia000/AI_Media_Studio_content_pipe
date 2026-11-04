"""/api/site report pages: index, latest page, navigation, months and every report's detail on the seeded dataset."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("kind", ["daily", "weekly", "monthly"])
def test_index_latest_and_every_report(compare, kind):
    index = compare(f"/api/site/reports/{kind}")
    assert index["status"] == 200
    compare(f"/api/site/reports/{kind}/latest-page")
    for entry in index["body"]["items"][:6]:
        key = entry["key"]
        assert compare(f"/api/site/reports/{kind}/{key}")["status"] == 200
        compare(f"/api/site/reports/{kind}/navigation/{key}")
    if kind == "daily" and index["body"]["items"]:
        compare(f"/api/site/reports/daily/months/{index['body']['items'][0]['key'][:7]}")


@pytest.mark.parametrize(
    "path",
    [
        "/api/site/reports/yearly",
        "/api/site/reports/yearly/latest-page",
        "/api/site/reports/daily/nope",
        "/api/site/reports/daily/2001-01-01",
        "/api/site/reports/weekly/navigation/bad",
        "/api/site/reports/daily/months/2024-13",
        "/api/site/reports/daily/months/2024-05",
    ],
)
def test_edges(compare, path):
    compare(path)
