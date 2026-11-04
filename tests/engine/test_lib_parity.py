"""The Python helpers must give what the TypeScript ones give (fixtures.json was produced by running them on these inputs)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from newsroom.engine import time as T
from newsroom.engine.lib import text as X
from newsroom.engine.lib.cursor import InvalidCursorError, decode_cursor, encode_cursor, query_binding
from newsroom.engine.lib.ids import new_article_id, sha256, short_hash, stable_json
from newsroom.engine.lib.image_url import is_non_article_image
from newsroom.engine.lib.video_url import is_video_page_url

F = json.loads((Path(__file__).parent / "fixtures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("c", F["time"]["instants"], ids=lambda c: c["i"])
def test_instants(c):
    assert (T.beijing_date(c["i"]), T.beijing_time(c["i"]), T.beijing_iso(c["i"]), T.beijing_day(c["i"]), T.beijing_stamp(c["i"])) == (
        c["date"],
        c["time"],
        c["iso"],
        c["day"],
        c["stamp"],
    )


@pytest.mark.parametrize("c", F["time"]["dates"], ids=lambda c: c["d"])
def test_dates(c):
    assert T.is_valid_date(c["d"]) is c["valid"]
    if c["valid"]:
        assert T.iso_week_label(c["d"]) == c["week"] and T.beijing_weekday(c["d"]) == c["weekday"]
        assert T.add_days(c["d"], 10) == c["plus"] and T.add_days(c["d"], -45) == c["minus"]
        assert T.beijing_midnight(c["d"]).isoformat().replace("+00:00", "") == c["midnight"].replace("Z", "").replace(".000", "")
        assert T.beijing_at(c["d"], "13:40").isoformat().replace("+00:00", "") == c["at"].replace("Z", "").replace(".000", "")
        assert T.month_day(c["d"], "2026-01-01") == c["md"]


@pytest.mark.parametrize("c", F["time"]["weeks"], ids=lambda c: c["w"])
def test_weeks(c):
    assert T.iso_week_range(c["w"]) == c["range"]


@pytest.mark.parametrize("c", F["time"]["months"], ids=lambda c: c["m"])
def test_months(c):
    assert T.month_range(c["m"]) == c["range"]


@pytest.mark.parametrize("c", F["text"], ids=lambda c: repr(c["s"])[:30])
def test_text(c):
    s = c["s"]
    assert X.collapse_whitespace(s) == c["collapse"]
    assert X.strip_tags(s) == c["strip"]
    assert X.strip_tag_markup(s) == c["markup"]
    assert X.decode_entities(s) == c["decode"]
    assert X.escape_xml(s + "\u0001\u000b") == c["xml"]
    assert math.isclose(X.cjk_ratio(s), c["ratio"]) and X.guess_language(s) == c["guess"]
    assert (X.is_chinese_text(s), X.is_chinese_text(s, "en-US"), X.is_chinese_text(s, "zh-TW"), X.is_chinese_text(s, "und")) == (
        c["zh"],
        c["zhEn"],
        c["zhZh"],
        c["zhUnd"],
    )
    assert X.truncate(s, 8) == c["trunc"] and X.truncate(s, 1) == c["trunc3"]


def test_stable_json_hashes_and_cursors():
    payloads = [{"a": 1, "b": "中文"}, {"z": [1, {"y": 2, "x": 3}], "a": None}, {}]
    assert [stable_json(p) for p in payloads] == F["misc"]["stable"]
    assert [[short_hash(s), short_hash(s, 6), sha256(s)] for s in ("abc", "中文", "")] == F["misc"]["hash"]
    assert [[encode_cursor("p", p), query_binding(p)] for p in payloads] == F["misc"]["cursors"]
    for p in payloads:
        assert decode_cursor("p", encode_cursor("p", p)) == p
    for bad in ("q.abc", "p.@@@", "p." + "e30"[:0]):
        with pytest.raises(InvalidCursorError):
            decode_cursor("p", bad)


def test_ids_are_the_site_shape():
    ids = {new_article_id() for _ in range(50)}
    assert len(ids) == 50 and all(len(i) == 25 and i[0].isalpha() and i.islower() for i in ids)


@pytest.mark.parametrize("u,expected", F["misc"]["video"], ids=lambda v: str(v)[:30])
def test_video_urls(u, expected):
    assert is_video_page_url(u) is expected


@pytest.mark.parametrize("args,expected", F["misc"]["image"], ids=lambda v: str(v)[:30])
def test_image_urls(args, expected):
    assert is_non_article_image(*args) is expected
