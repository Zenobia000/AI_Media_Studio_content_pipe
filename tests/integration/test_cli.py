"""The tool surface (`newsroom <tool>`, IF-020): what a tool prints with --json and the exit codes (0 done, 2 bad input), run in this process."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from newsroom import __version__
from newsroom.cli import app

run = CliRunner().invoke


def test_the_version_and_the_list_of_tools():
    assert run(app, ["--version"]).output.strip() == __version__
    listed = run(app, ["--help"]).output
    assert all(tool in listed for tool in ("add-source", "open-topic", "fetch-topic", "fetch-media", "assets-topic", "write-inbox", "run-scheduler", "ask"))


def test_init_makes_a_workspace_once_and_says_what_comes_next(tmp_path):
    first = run(app, ["init", str(tmp_path / "ws"), "--json"])
    out = json.loads(first.output)
    assert first.exit_code == 0 and out["ok"] is True and out["tool"] == "init" and out["data"]["created"] and any("NEWSROOM_HOME" in n for n in out["next"])
    again = json.loads(run(app, ["init", str(tmp_path / "ws"), "--json"]).output)
    assert again["data"]["created"] == [] and again["data"]["existing"]


@pytest.mark.usefixtures("conn")
class TestWithADatabase:
    def test_bad_input_is_exit_2_and_says_why(self):
        bad_url = run(app, ["add-source", "not a url"])
        assert bad_url.exit_code == 2 and bad_url.output.strip()
        assert run(app, ["set-title-zh"]).exit_code == 2
        assert run(app, ["source-stats", "--days", "0"]).exit_code == 2

    def test_a_tool_that_cannot_do_what_was_asked_answers_in_the_json_shape(self):
        res = run(app, ["open-topic", "cand_that-does-not-exist", "--json"])
        out = json.loads(res.output)
        assert res.exit_code == 2 and out["ok"] is False and out["tool"] == "open-topic" and out["error_code"] and out["message"] and out["blocked"] is False

    def test_the_tools_that_read_answer_in_json(self):
        stats = run(app, ["source-stats", "--json"])
        out = json.loads(stats.output)
        assert stats.exit_code == 0 and out["ok"] is True and out["data"]["days"] == 14 and isinstance(out["data"]["sources"], list)
        assert isinstance(json.loads(run(app, ["set-title-zh", "--list"]).output), list)
