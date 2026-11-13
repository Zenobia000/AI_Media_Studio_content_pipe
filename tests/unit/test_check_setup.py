"""The parts of check_setup.py that read what other programs print: versions, Ollama's model list and a .env file."""

from __future__ import annotations

import pytest

import check_setup as C


@pytest.mark.parametrize(
    ("text", "ok"),
    [("v24.22.1", True), ("v24.30.0", True), ("v25.0.0", True), ("v24.22.0", False), ("v22.12.0", False), ("not a version", False), ("24.22", False)],
)
def test_a_node_version_is_compared_part_by_part(text, ok):
    assert C.version_at_least(text, (24, 22, 1)) is ok


def test_a_two_part_version_counts_its_missing_patch_as_zero():
    assert C.version_at_least("3.12", (3, 12, 0)) is True
    assert C.version_at_least("3.11.9", (3, 12, 0)) is False


def test_ollama_model_names_lose_their_latest_tag_and_keep_other_tags():
    tags = {"models": [{"name": "bge-m3:latest"}, {"name": "qwen3.5:9b-ctx32k"}, {"name": "qwen3.5:9b"}, {"nope": 1}, "x"]}
    assert C.parse_models(tags) == {"bge-m3", "qwen3.5:9b-ctx32k", "qwen3.5:9b"}
    assert C.parse_models({}) == set()


def test_an_env_file_is_read_without_comments_or_quotes():
    env = C.parse_env('# a comment\n\nLLM_MODEL=qwen3.5:9b-ctx32k\nLLM_EXTRA_JSON={"reasoning_effort":"none"}\nKEY="quoted"\nEMPTY=\n')
    assert env["LLM_MODEL"] == "qwen3.5:9b-ctx32k" and env["KEY"] == "quoted" and env["EMPTY"] == ""
    assert env["LLM_EXTRA_JSON"] == '{"reasoning_effort":"none"}'


def test_the_models_the_check_asks_for_are_the_ones_the_code_defaults_to():
    from newsroom.rag import ask, index

    assert ask.chat_model() in C.MODELS and index.embed_model() in C.MODELS
