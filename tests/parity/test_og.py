"""/og/*: the cache keys, headers and 404s word for word; the pictures by format and size (satori and Pillow draw differently)."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image


def check(node_api, py_api, path):
    node, py = httpx.get(node_api + path, timeout=60), py_api.get(path)
    assert node.status_code == py.status_code, path
    if node.status_code != 200:
        assert (node.text, node.headers.get("cache-control")) == (py.text, py.headers.get("cache-control"))
        return node.status_code
    for header in ("etag", "cache-control", "content-type"):
        assert node.headers.get(header) == py.headers.get(header), (path, header)
    a, b = Image.open(io.BytesIO(node.content)), Image.open(io.BytesIO(py.content))
    assert (a.format, a.size) == (b.format, b.size), path
    assert py_api.get(path, headers={"If-None-Match": py.headers["etag"]}).status_code == 304
    return 200


def test_static_and_missing(node_api, py_api, compare):
    paths = [
        "/og/site.png",
        "/og/pages/topics.png",
        "/og/pages/nope.png",
        "/og/pages/site",
        "/og/items/nope.png",
        "/og/items/x",
        "/og/topics/nope.png",
        "/og/stories/nope.png",
        "/og/posters/nope.png",
        "/og/reports/yearly/2026.png",
        "/og/reports/daily/1999-01-01.png",
    ]
    paths += [f"/og/topics/{t['slug']}.png" for t in compare("/api/site/topics")["body"]["topics"][:4]]
    for path in paths:
        check(node_api, py_api, path)


@pytest.mark.parametrize("page", ["all", "hot", "daily", "weekly", "monthly", "agent", "about"])
def test_page_cards(node_api, py_api, page):
    check(node_api, py_api, f"/og/pages/{page}.png")


def test_content_cards(node_api, py_api, compare):
    seen = set()
    for card in compare("/api/site/timeline")["body"]["cards"][:12]:
        item_id = card["item"]["id"]
        seen.add(check(node_api, py_api, f"/og/items/{item_id}.png"))
        seen.add(check(node_api, py_api, f"/og/posters/{item_id}.png"))
    assert 200 in seen
    for kind in ("daily", "weekly", "monthly"):
        for entry in compare(f"/api/site/reports/{kind}")["body"]["items"][:2]:
            assert check(node_api, py_api, f"/og/reports/{kind}/{entry['key']}.png") == 200
    for entry in compare("/api/site/hot")["body"]["entries"][:3]:
        assert check(node_api, py_api, f"/og/stories/{entry['story']['publicId']}.png") == 200
