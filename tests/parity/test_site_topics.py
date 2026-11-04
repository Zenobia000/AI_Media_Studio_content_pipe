"""/api/site/topics: the directory and every topic's pages on the seeded dataset."""

from __future__ import annotations


def test_directory_and_pages(compare):
    directory = compare("/api/site/topics")
    assert directory["status"] == 200 and directory["body"]["topics"]
    busy = 0
    for t in directory["body"]["topics"]:
        page = compare(f"/api/site/topics/{t['slug']}")
        assert page["status"] == 200
        busy += bool(page["body"]["items"])
    assert busy


def test_edges(compare):
    slug = compare("/api/site/topics")["body"]["topics"][0]["slug"]
    for query in ["page=0", "page=x", "page=99", "page=1.5", "page=", "page=2"]:
        compare(f"/api/site/topics/{slug}?{query}")
    assert compare("/api/site/topics/nope")["status"] == 404
