"""/api/v1 dailies, weeklies and monthlies on the seeded dataset."""

from __future__ import annotations

import pytest


def keys(compare, path):
    return [i[k] for i in compare(path)["body"]["items"] for k in ("date", "week", "month") if k in i]


def test_dailies_index_and_latest(compare):
    assert compare("/api/v1/dailies")["status"] == 200
    assert compare("/api/v1/dailies?limit=1")["status"] == 200
    assert compare("/api/v1/dailies/latest")["status"] == 200


def test_every_daily_by_date(compare):
    for date in keys(compare, "/api/v1/dailies"):
        assert compare(f"/api/v1/dailies/{date}")["status"] == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dailies/2020-01-01",
        "/api/v1/dailies/2026-02-30",
        "/api/v1/dailies/nope",
        "/api/v1/dailies?limit=0",
        "/api/v1/dailies?limit=181",
        "/api/v1/dailies/latest?x=1",
        "/api/v1/dailies/2020-01-01?x=1",
    ],
)
def test_daily_errors(compare, path):
    compare(path)


@pytest.mark.parametrize("path,key_ok", [("weeklies", "week"), ("monthlies", "month")])
def test_periods(compare, path, key_ok):
    index = compare(f"/api/v1/{path}")
    assert index["status"] == 200 and index["body"]["count"] >= 1
    compare(f"/api/v1/{path}?limit=1")
    compare(f"/api/v1/{path}/latest")
    for item in index["body"]["items"]:
        got = compare(f"/api/v1/{path}/{item[key_ok]}")
        assert got["status"] == 200 and got["body"]["report"]["sections"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/weeklies/2020-W01",
        "/api/v1/weeklies/2026-W54",
        "/api/v1/weeklies/nope",
        "/api/v1/weeklies?limit=61",
        "/api/v1/monthlies/2020-01",
        "/api/v1/monthlies/2026-13",
        "/api/v1/monthlies/nope",
        "/api/v1/monthlies?limit=0",
        "/api/v1/weeklies/latest?a=1",
    ],
)
def test_period_errors(compare, path):
    compare(path)


def test_conditional_request(compare):
    first = compare("/api/v1/dailies/latest")
    assert compare("/api/v1/dailies/latest", headers={"if-none-match": first["headers"]["etag"]})["status"] == 304
