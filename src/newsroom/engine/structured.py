"""Structured output for models that can constrain what they write (Ollama, vLLM, llama.cpp servers: OpenAI's `response_format` of type `json_schema`).

A small local model often ignores the prompt's rules for a value (an item type it made up, a score on a scale of 0 to 1) and its answer is then refused by the
schema check. With LLM_STRUCTURED_OUTPUT=true the same rules are sent to the server as a JSON Schema, and it cannot write anything else. It is opt-in: the original
sent only `json_object`, and a hosted model that follows the prompt needs no help. Only the steps whose answers are small and fixed are covered."""

from __future__ import annotations

import os

from newsroom.engine import vocabulary as V


def enabled() -> bool:
    return os.environ.get("LLM_STRUCTURED_OUTPUT") == "true"


def schema_for(purpose: str) -> dict | None:
    """The JSON Schema of a step's answer, or None for a step this does not cover."""
    if purpose == "prefilter_article":
        return {
            "type": "object",
            "properties": {"label": {"type": "string", "enum": ["PASS", "BLOCK", "UNKNOWN"]}, "reason": {"type": "string"}},
            "required": ["label", "reason"],
        }
    if purpose == "score_article":
        return {"type": "object", "properties": {"attentionScore": {"type": "integer", "minimum": 0, "maximum": 100}}, "required": ["attentionScore"]}
    if purpose == "understand_article":
        return {
            "type": "object",
            "properties": {
                "itemType": {"type": "string", "enum": list(V.item_types())},
                "authorRole": {"type": "string", "enum": ["principal", "observer", "relayer"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "editorialJudgment": {"type": "string"},
                "titleZh": {"type": "string"},
                "summaryZh": {"type": "string"},
            },
            "required": ["itemType", "authorRole", "tags", "editorialJudgment", "titleZh", "summaryZh"],
        }
    return None


def response_format(purpose: str) -> dict | None:
    """The `response_format` to send for a step, when structured output is on and the step has a schema."""
    schema = schema_for(purpose) if enabled() else None
    return {"type": "json_schema", "json_schema": {"name": purpose, "strict": True, "schema": schema}} if schema else None
