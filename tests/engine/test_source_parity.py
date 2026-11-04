"""The collectors' pure helpers must give what the TypeScript ones give (source_fixtures.json: AIHOT/scripts/gen-source-fixtures.ts)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from newsroom.engine.sources import dates as D

F = json.loads((Path(__file__).parent / "source_fixtures.json").read_text(encoding="utf-8"))


def iso(d: datetime | None) -> str | None:
    return None if d is None else D._iso(d)


@pytest.mark.parametrize("c", F["parseLooseDate"], ids=lambda c: f"{c['in']!r}|{c['offset']}")
def test_parse_loose_date(c):
    assert iso(D.parse_loose_date(c["in"], c["offset"])) == c["out"]


@pytest.mark.parametrize("c", F["readPublicationDate"], ids=lambda c: repr(c["in"]))
def test_read_publication_date(c):
    r = D.read_publication_date(c["in"])
    assert {"at": iso(r.published_at), "time": r.publication_time} == c["out"]


@pytest.mark.parametrize("c", F["readRelativeDate"], ids=lambda c: f"{c['in']!r}|{c['offset']}")
def test_read_relative_date(c):
    now = datetime.fromisoformat(F["now"].replace("Z", "+00:00"))
    r = D.read_relative_date(c["in"], now, c["offset"])
    assert {"at": iso(r.published_at), "time": r.publication_time} == c["out"]


# --- listing helpers: json_list, filters, config_keys ---------------------------------------------------------------------------------------------
from newsroom.engine.sources import config_keys as CK  # noqa: E402
from newsroom.engine.sources import filters as FL  # noqa: E402
from newsroom.engine.sources import json_list as JL  # noqa: E402

DOC = {"a": {"b": [{"c": "x"}, {"c": 5}], "n": 1.5, "big": JL.json_parse("12345678901234567890"), "z": 0, "e": "", "t": True, "nul": None}, "k.1": 2, "list": [1, [2, 3]]}


def source_with(config: dict, kind: str = "rss") -> dict:
    return {"id": "s", "kind": kind, "config": config}


@pytest.mark.parametrize("c", F["getPath"], ids=lambda c: repr(c["in"]))
def test_get_path(c):
    assert JL.get_path(DOC, c["in"]) == JL.json_parse(json.dumps(c["out"]))


@pytest.mark.parametrize("c", F["renderTemplate"], ids=lambda c: repr(c["in"]))
def test_render_template(c):
    assert JL.render_template(c["in"], DOC) == c["out"]


@pytest.mark.parametrize("c", F["toDate"], ids=lambda c: repr(c["in"]))
def test_to_date(c):
    v, unit, off = c["in"]
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        v = int(v)
    r = JL.to_date(v, unit, off)
    assert {"at": iso(r.published_at), "time": r.publication_time} == c["out"]


def cands():
    return [{**c, "categories": c["categories"]} for c in F["cands"]]


@pytest.mark.parametrize("i", range(len(F["admitListing"])))
def test_admit_listing(i):
    c = F["admitListing"][i]
    assert [x["url"] for x in FL.admit_listing(cands(), source_with(c["config"]))] == c["out"]


@pytest.mark.parametrize("c", F["noiseFiltered"], ids=lambda c: f"{c['index']}|{json.dumps(c['config'])[:50]}")
def test_noise_filtered(c):
    assert FL.noise_filtered(cands()[c["index"]], source_with(c["config"])) is c["out"]


@pytest.mark.parametrize("c", F["window"], ids=lambda c: str(c["after"]))
def test_publication_window(c):
    items = [{"id": 1, "published_at": "2026-11-01T00:00:00Z"}, {"id": 2, "published_at": "2026-11-04T09:00:00Z"}, {"id": 3, "published_at": None},
             {"id": 4, "published_at": "2026-11-04T10:00:00Z"}, {"id": 5, "published_at": "2026-11-04T10:31:00Z"}, {"id": 6, "published_at": "2026-11-02T00:00:00Z"}]  # fmt: skip
    for i in items:
        i["published_at"] = datetime.fromisoformat(i["published_at"].replace("Z", "+00:00")) if i["published_at"] else None
    now = datetime.fromisoformat(F["windowNow"].replace("Z", "+00:00"))
    assert [x["id"] for x in FL.filter_publication_window(items, c["after"], now)] == c["out"]


@pytest.mark.parametrize("i", range(len(F["unsupportedConfig"])))
def test_unsupported_config(i):
    c = F["unsupportedConfig"][i]
    assert CK.unsupported_config(c["kind"], c["config"]) == c["out"], c["config"]
