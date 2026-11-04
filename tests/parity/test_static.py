"""The site's public files, icons and contact codes."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/robots.txt",
        "/.well-known/security.txt",
        "/manifest.webmanifest",
        "/openapi-v1.json",
        "/favicon.ico",
        "/icon.png",
        "/icon-192.png",
        "/apple-icon.png",
        "/logo.svg",
        "/contact/nope.png",
        "/contact/bad..name",
        "/contact/x.exe",
    ],
)
def test_files(compare, path):
    compare(path)


def test_the_openapi_document_is_filled_in_and_cached(compare):
    got = compare("/openapi-v1.json")
    assert got["status"] == 200 and "{{" not in str(got["body"]) and got["headers"]["access-control-allow-origin"] == "*"
    assert compare("/openapi-v1.json", headers={"if-none-match": got["headers"]["etag"]})["status"] == 304


def test_robots_is_filled_in(compare):
    got = compare("/robots.txt")
    assert got["status"] == 200 and "{{" not in got["body"]
