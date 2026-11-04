"""The packs as data, and the engine's configuration."""

from __future__ import annotations

import pytest
import regex

from newsroom.engine import config as C
from newsroom.engine import pack


def walk(v):
    if isinstance(v, regex.Pattern):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from walk(x)
    elif isinstance(v, list):
        for x in v:
            yield from walk(x)


def test_every_pack_loads_and_its_regular_expressions_compile():
    for name in ("site", "models", "taxonomy", "selection", "wording"):
        assert pack.load(name)
    patterns = list(walk(pack.load("taxonomy")))
    assert len(patterns) >= 10 and all(isinstance(p, regex.Pattern) for p in patterns)


def test_the_wording_helpers_give_what_the_site_file_evaluated():
    s = pack.site()
    assert pack.subject_after("每週", "行業綜合回顧") == s["REPORTS"]["descriptions"]["weekly"]
    assert pack.subject_after("每月", "行業盤點") == s["REPORTS"]["descriptions"]["monthly"]
    assert pack.subject_after("每日", "精選") == s["CARDS"]["site"]["kicker"]
    assert pack.with_subject("日報") == s["CARDS"]["daily"]["kicker"]


def test_categories_and_site_basics():
    t = pack.load("taxonomy")
    assert t["CATEGORIES"] and all("key" in c and "label" in c for c in t["CATEGORIES"])
    s = pack.site()
    assert s["SITE"]["defaultUrl"].startswith("http") and s["EDITION_TIMES"] == {"daily": "08:00", "weekly": "10:00", "monthly": "10:30"}


def test_config_reads_the_environment_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://x/y")
    monkeypatch.setenv("SITE_URL", "https://example.com///")
    monkeypatch.setenv("API_PORT", "4000")
    monkeypatch.setenv("COLLECT_ENABLED", "true")
    monkeypatch.setenv("MODEL_CALLS_ENABLED", "yes")
    monkeypatch.setenv("INDEXNOW_KEY", "a" * 32)
    monkeypatch.setenv("ADMIN_EMAILS", " A@x.com, ,b@x.com")
    monkeypatch.delenv("AIHOT_CREDENTIALS_DIR", raising=False)
    c = C.config()
    assert (c.database_url, c.site_url, c.api_port, c.index_now_key) == ("postgres://x/y", "https://example.com", 4000, "a" * 32)
    assert c.model_calls_enabled is False  # only an explicit "true" turns a switch on
    assert c.admin_emails == ("a@x.com", "b@x.com") and c.environment_name == "development"
    monkeypatch.setenv("API_PORT", "x")
    with pytest.raises(ValueError):
        C.config()


def test_credentials_come_from_the_environment_then_the_group_file(monkeypatch, tmp_path):
    (tmp_path / "models.env").write_text("# c\nLLM_KEY=from-file\nexport OTHER='quoted'\nEMPTY=\n", encoding="utf-8")
    monkeypatch.setenv("AIHOT_CREDENTIALS_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    C._group.cache_clear()
    assert C.credential("models", "LLM_KEY") == "from-file" and C.credential("models", "OTHER") == "quoted"
    assert C.credential("models", "EMPTY") is None and C.credential("collectors", "LLM_KEY") is None
    monkeypatch.setenv("LLM_KEY", "from-env")
    assert C.credential("models", "LLM_KEY") == "from-env"


def test_production_refuses_missing_or_placeholder_secrets(monkeypatch):
    monkeypatch.delenv("NODE_ENV", raising=False)
    C.assert_production_secrets([("auth", "SESSION_SECRET")])  # not production: nothing to check
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.delenv("AIHOT_CREDENTIALS_DIR", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "changeme")
    monkeypatch.setenv("DEV_AUTH_ROLE", "admin")
    with pytest.raises(RuntimeError, match=r"SESSION_SECRET.*DEV_AUTH_ROLE"):
        C.assert_production_secrets([("auth", "SESSION_SECRET")])
    monkeypatch.delenv("DEV_AUTH_ROLE")
    monkeypatch.setenv("SESSION_SECRET", "a-long-enough-secret")
    C.assert_production_secrets([("auth", "SESSION_SECRET")])
