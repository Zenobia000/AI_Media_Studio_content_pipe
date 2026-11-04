"""/api/v1/agent: the guide and every Markdown answer, on the seeded dataset."""

from __future__ import annotations

import pytest


def test_guide(compare):
    got = compare("/api/v1/agent")
    assert got["status"] == 200 and "回答提示" not in got["body"] or got["status"] == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agent/latest",
        "/api/v1/agent/latest?window=7d",
        "/api/v1/agent/latest?mode=all&limit=30",
        "/api/v1/agent/latest?category=nope",
        "/api/v1/agent/latest?window=1h",
        "/api/v1/agent/latest?x=1",
        "/api/v1/agent/search?q=article",
        "/api/v1/agent/search?q=zzzz-nothing",
        "/api/v1/agent/search?q=a",
        "/api/v1/agent/search",
        "/api/v1/agent/search?q=Body&window=24h&limit=3",
        "/api/v1/agent/hot",
        "/api/v1/agent/hot?limit=2",
        "/api/v1/agent/hot?limit=0",
        "/api/v1/agent/daily",
        "/api/v1/agent/daily/2001-01-01",
        "/api/v1/agent/daily/2026-13-01",
        "/api/v1/agent/weekly",
        "/api/v1/agent/monthly",
        "/api/v1/agent/weekly/2026-W99",
        "/api/v1/agent/weekly/2001-W01",
        "/api/v1/agent/monthly/2001-01",
        "/api/v1/agent/monthly/bad",
        "/api/v1/agent/stories/nope",
        "/api/v1/agent/nothing",
    ],
)
def test_answers(compare, path):
    compare(path)


def test_dated_and_story_answers(compare):
    hot = compare("/api/v1/agent/hot")
    assert hot["status"] == 200
    import re

    for pid in re.findall(r"/stories/([\w-]+)", hot["body"])[:5]:
        assert compare(f"/api/v1/agent/stories/{pid}")["status"] == 200
        compare(f"/api/v1/agent/stories/{pid}?limit=2")
    daily = compare("/api/v1/agent/daily")
    assert daily["status"] == 200
    date = re.search(r"日報 · (\d{4}-\d{2}-\d{2})", daily["body"]).group(1)
    compare(f"/api/v1/agent/daily/{date}")
    for kind in ("weekly", "monthly"):
        got = compare(f"/api/v1/agent/{kind}")
        key = re.search(r"· (\d{4}-[\w]+)\n", got["body"]).group(1)
        compare(f"/api/v1/agent/{kind}/{key}")
