"""The API skeleton: health, redirects, the 404 and error formats."""

from __future__ import annotations

import pytest


def test_health(compare):
    got = compare("/api/health", drop=("ms",))
    assert got["body"]["ok"] is True and got["headers"]["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/nope", "/nope", "/.well-known/openid-configuration", "/api/mcp/.well-known/oauth-protected-resource"])
def test_unknown_paths(compare, path):
    compare(path)


@pytest.mark.parametrize("path", ["/about/", "/all//", "/about/?utm=1", "/rss", "/rss.xml?x=1", "/atom.xml", "/feed", "/privacy/"])
def test_redirects(compare, path):
    got = compare(path)
    assert got["status"] == 301


def test_a_method_the_route_does_not_have_is_not_found(compare):
    compare("/api/health", method="POST")
