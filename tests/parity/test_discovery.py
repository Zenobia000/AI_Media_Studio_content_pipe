"""sitemap.xml, its section files and llms.txt on the seeded dataset."""

from __future__ import annotations

import re

import httpx


def test_sitemap_and_sections(compare, node_api):
    index = compare("/sitemap.xml", drop=(), aged=True)
    assert index["status"] == 200
    files = re.findall(r"/sitemaps/([\w.-]+)", index["body"])
    assert files
    for f in files:
        assert compare(f"/sitemaps/{f}", aged=True)["status"] == 200
    assert compare("/sitemaps/nope.xml")["status"] == 404
    assert httpx.get(node_api + "/sitemap.xml").status_code == 200


def test_llms(compare):
    got = compare("/llms.txt")
    assert got["status"] == 200 and "MCP" in got["body"]
