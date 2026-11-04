"""URL identity must equal AIHOT's TypeScript version (url_fixtures.json was produced by running lib/url.ts on these addresses)."""

import json
from pathlib import Path

import pytest

from newsroom.core.urls import identity_key_for_url, normalize_url

CASES = json.loads((Path(__file__).parent / "url_fixtures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["u"] for c in CASES])
def test_same_as_the_typescript_version(case):
    assert normalize_url(case["u"]) == case["n"]
    assert identity_key_for_url(case["u"]) == case["k"]
    assert identity_key_for_url(case["u"], keep_fragment=True) == case["kf"]
