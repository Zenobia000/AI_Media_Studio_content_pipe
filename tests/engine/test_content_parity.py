"""The Python body helpers must give what the TypeScript ones give (content_fixtures.json: AIHOT/scripts/gen-content-fixtures.ts)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("IMG_PROXY_SIGN_SECRET", "ci-img-secret-0123456789abcdef")
os.environ.setdefault("SITE_URL", "http://127.0.0.1:3931")

from newsroom.engine import content as C  # noqa: E402

F = json.loads((Path(__file__).parent / "content_fixtures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("c", F["textToHtml"], ids=lambda c: repr(c["in"])[:40])
def test_text_to_html(c):
    assert C.text_to_html(c["in"]) == c["out"]


@pytest.mark.parametrize("c", F["proxyBodyImages"], ids=lambda c: f"{c['in'][:40]}|{c['absolute']}")
def test_proxy_body_images(c, monkeypatch):
    monkeypatch.setattr(C.time, "time", lambda: F["generatedAtMs"] / 1000)
    assert C.proxy_body_images(c["in"], c["absolute"]) == c["out"]


@pytest.mark.parametrize("c", F["normalizeVideos"], ids=lambda c: c["in"][:40])
def test_normalize_videos(c):
    root = C._parse(c["in"])
    C.normalize_videos(root)
    assert C._serialize(root) == c["out"]


@pytest.mark.parametrize("c", F["sanitizeBody"], ids=lambda c: c["in"][:50])
def test_sanitize_body(c):
    from newsroom.engine.sanitize import sanitize_body

    assert sanitize_body(c["in"], c["base"]) == c["out"]


@pytest.mark.parametrize("c", F["bodyToMarkdown"], ids=lambda c: c["in"][:50])
def test_body_to_markdown(c):
    from newsroom.engine.markdown import body_to_markdown

    assert body_to_markdown(c["in"], c["base"]) == c["out"]
