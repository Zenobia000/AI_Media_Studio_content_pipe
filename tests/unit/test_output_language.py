"""The line asking a model for Traditional Chinese (engine/output_language.py): off unless asked for, and only for the steps that write Chinese for readers."""

from __future__ import annotations

import pytest

from newsroom.engine import output_language as O


def opts(purpose, system="You write JSON."):
    return {"purpose": purpose, "system": system, "user": "u"}


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_TRADITIONAL", raising=False)
    given = opts("understand_article")
    assert O.apply(given) is given
    monkeypatch.setenv("LLM_TRADITIONAL", "false")
    assert O.apply(given) is given


@pytest.mark.parametrize(
    "purpose", ["understand_article", "summarize_article", "mend_wording", "story_digest", "translate_body", "translate_quoted", "report_period"]
)
def test_added_for_the_steps_that_write_chinese(monkeypatch, purpose):
    monkeypatch.setenv("LLM_TRADITIONAL", "true")
    given = opts(purpose)
    out = O.apply(given)
    assert out["system"] == given["system"] + O.NOTE and "繁體中文" in out["system"]
    assert given["system"] == "You write JSON."  # the caller's options are not changed


@pytest.mark.parametrize("purpose", ["score_article", "prefilter_article", "structure_article", "group_article", "group_story", "embedding"])
def test_not_added_for_the_others(monkeypatch, purpose):
    monkeypatch.setenv("LLM_TRADITIONAL", "true")
    given = opts(purpose)
    assert O.apply(given) is given


def test_a_call_without_a_system_prompt_is_left_alone(monkeypatch):
    monkeypatch.setenv("LLM_TRADITIONAL", "true")
    given = {"purpose": "summarize_article", "system": "", "user": "u"}
    assert O.apply(given) is given
