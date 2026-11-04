"""/api/site meta, contact, stats, changelog and item availability."""

from __future__ import annotations


def test_meta_changelog_contact(compare):
    for path in ("/api/site/meta", "/api/site/changelog", "/api/site/contact"):
        got = compare(path)
        assert got["status"] == 200 and "etag" in got["headers"]


def test_stats(compare):
    got = compare("/api/site/stats")
    assert got["status"] == 200 and got["body"]["items"] >= 10 and got["body"]["sampleSources"]


def test_availability(compare):
    items = compare("/api/v1/items?mode=all&limit=5")["body"]["items"]
    ids = ",".join(i["id"] for i in items)
    got = compare(f"/api/site/items/availability?ids={ids},nope,bad%20id")
    assert got["status"] == 200 and got["headers"]["cache-control"] == "no-store"
    assert [got["body"][i["id"]]["status"] for i in items] == ["public"] * 5
    compare("/api/site/items/availability")


def test_conditional(compare):
    first = compare("/api/site/meta")
    assert compare("/api/site/meta", headers={"if-none-match": first["headers"]["etag"]})["status"] == 304
