"""The article reader (engine/readability.py) must read pages as @mozilla/readability under linkedom does (AIHOT/scripts/readability-run.ts)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from newsroom.engine import dom as D
from newsroom.engine.readability import Readability
from tests.engine.reader_pages import PAGES, random_pages

PAGES = {**PAGES, **random_pages(200, seed=2026)}

AIHOT = Path(__file__).parents[2] / "AIHOT"
KEYS = ["title", "byline", "dir", "lang", "content", "textContent", "length", "excerpt", "siteName", "publishedTime"]


@pytest.fixture(scope="module")
def node_results(tmp_path_factory):
    if not shutil.which("node") or not (AIHOT / "node_modules").exists():
        pytest.skip("needs Node with the AIHOT dependencies installed")
    path = tmp_path_factory.mktemp("reader") / "pages.json"
    path.write_text(json.dumps([{"html": h} for h in PAGES.values()]), encoding="utf-8")
    out = subprocess.run(["node", "scripts/readability-run.ts", str(path)], cwd=AIHOT, env={**os.environ, "TZ": "UTC"}, check=True, capture_output=True, timeout=300)
    return dict(zip(PAGES, json.loads(out.stdout.decode().strip().splitlines()[-1]), strict=True))


def python_read(html: str) -> dict | None:
    try:
        return Readability(D.parse_html(html), char_threshold=200).parse()
    except Exception as e:  # the same failure the TypeScript one reports as an error entry
        return {"error": f"{type(e).__name__}: {e}"}


@pytest.mark.parametrize("name", list(PAGES))
def test_reads_like_readability(node_results, name):
    node, py = node_results[name], python_read(PAGES[name])
    if node is None or py is None:
        assert node is None and py is None, (node and node.get("title"), py and py.get("title"))
        return
    assert ("error" in node) == ("error" in py), (node, py)
    if "error" in node:
        return
    for key in KEYS:
        assert py.get(key) == node.get(key), f"{name}.{key}:\n py   {str(py.get(key))[:600]!r}\n node {str(node.get(key))[:600]!r}"
