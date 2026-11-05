"""Structured output for local models (engine/structured.py): off unless asked for, and the schemas hold the same rules the answer check enforces."""

from __future__ import annotations

import pytest

from newsroom.engine import structured as S
from newsroom.engine import vocabulary as V
from newsroom.engine.analyze import SchemaError, prefilter_schema, score_schema, understand_schema


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT", raising=False)
    assert S.response_format("score_article") is None
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT", "false")
    assert S.response_format("understand_article") is None


def test_on_only_for_the_steps_that_have_a_schema(monkeypatch):
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT", "true")
    for step in ("prefilter_article", "score_article", "understand_article"):
        fmt = S.response_format(step)
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["name"] == step and fmt["json_schema"]["strict"] is True
    assert S.response_format("summarize_article") is None  # no schema: the model is asked for json_object as before


def test_the_schemas_say_what_the_answer_check_demands():
    item_types = S.schema_for("understand_article")["properties"]["itemType"]["enum"]
    assert item_types == list(V.item_types())
    understand_ok = {"itemType": item_types[0], "authorRole": "observer", "tags": [], "editorialJudgment": "", "titleZh": "標題", "summaryZh": "摘要"}
    assert understand_schema(understand_ok)["itemType"] == item_types[0]
    for wrong in ("article", "文章"):
        with pytest.raises(SchemaError):
            understand_schema({**understand_ok, "itemType": wrong})
    score = S.schema_for("score_article")["properties"]["attentionScore"]
    assert (score["type"], score["minimum"], score["maximum"]) == ("integer", 0, 100)
    assert score_schema({"attentionScore": 100}) == {"attentionScore": 100}
    for wrong in (0.85, 101, -1):
        with pytest.raises(SchemaError):
            score_schema({"attentionScore": wrong})
    labels = S.schema_for("prefilter_article")["properties"]["label"]["enum"]
    assert all(prefilter_schema({"label": label, "reason": "x"})["label"] == label for label in labels)
