"""start_dev.py and stop_dev.py: what the flags and AIHOT/.env make of the services' environment, and which processes stop_dev takes for its own.
Nothing is started or stopped here: the database, the services and taskkill are replaced."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

import start_dev as S
import stop_dev as T

windows = pytest.mark.skipif(os.name != "nt", reason="the scripts refuse to run anywhere else")


def test_the_env_file_gives_its_values_but_not_the_ones_the_script_decides(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        '# a comment\n\nLLM_MODEL=qwen3.5:9b-ctx32k\nLLM_EXTRA_JSON={"reasoning_effort":"none"}\nQUOTED="a b"\nEMPTY=\n'
        "DATABASE_URL=postgres://elsewhere/x\nCOLLECT_ENABLED=false\nAPI_PORT=9\nnot a line\n",
        encoding="utf-8",
    )
    assert S.dotenv(path) == {"LLM_MODEL": "qwen3.5:9b-ctx32k", "LLM_EXTRA_JSON": '{"reasoning_effort":"none"}', "QUOTED": "a b"}
    assert S.dotenv(tmp_path / "missing") == {}


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:11434/v1"])
def test_a_local_model_gets_the_local_defaults_and_keeps_what_the_env_file_set(url):
    env = {"LLM_BASE_URL": url, "ANALYZE_CONCURRENCY": "1"}
    S.local_model_defaults(env)
    assert env == {**S.LOCAL_MODEL_DEFAULTS, "LLM_BASE_URL": url, "ANALYZE_CONCURRENCY": "1"}


def test_a_cloud_model_gets_none_of_them_unless_structured_output_was_asked_for():
    env = {"LLM_BASE_URL": "https://api.example.com/v1"}
    S.local_model_defaults(env)
    assert env == {"LLM_BASE_URL": "https://api.example.com/v1"}
    asked = {"LLM_BASE_URL": "https://api.example.com/v1", "LLM_STRUCTURED_OUTPUT": "true"}
    S.local_model_defaults(asked)
    assert asked["LLM_TRADITIONAL"] == "true"
    off = {"LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_STRUCTURED_OUTPUT": "false"}
    S.local_model_defaults(off)
    assert "LLM_TRADITIONAL" not in off


@pytest.fixture()
def started(monkeypatch, tmp_path):
    """main() with the database, the services and the output loop replaced; gives what it was asked to do."""
    seen = SimpleNamespace(databases=[], prepared=[], launched=[], followed=[], file={})

    class Proc:
        def __init__(self, command, cwd=None, env=None, **kw):
            seen.launched.append((command, env, kw))
            self.pid = 4000 + len(seen.launched)

    monkeypatch.setattr(S, "database", seen.databases.append)
    monkeypatch.setattr(S, "prepare", lambda env: seen.prepared.append(env["DATABASE_URL"]))
    monkeypatch.setattr(S.subprocess, "Popen", Proc)
    monkeypatch.setattr(S, "follow", lambda procs: seen.followed.append([title for title, _ in procs]))
    monkeypatch.setattr(S, "PIDS", tmp_path / "data" / "dev-pids.json")
    monkeypatch.setattr(S, "dotenv", lambda path: dict(seen.file))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    def run(*flags: str):
        monkeypatch.setattr(sys, "argv", ["start_dev.py", *flags])
        S.main()
        return seen.launched[-1][1]

    seen.run = run
    return seen


@windows
def test_without_a_key_the_services_start_with_model_calls_off_and_nothing_from_the_env_file(started, capsys):
    started.file = {"LLM_MODEL": "some-model"}
    env = started.run()
    assert env["MODEL_CALLS_ENABLED"] == "false" and env["COLLECT_ENABLED"] == "true" and "LLM_MODEL" not in env
    assert env["DATABASE_URL"] == f"{S.DB_HOST}/dev" and started.databases == ["dev"] and started.prepared == [f"{S.DB_HOST}/dev"]
    assert [command[-1] for command, _, _ in started.launched] == ["3101", "worker", "dev"]  # the API, the worker, the website
    assert started.followed == [["MediaHOT API", "MediaHOT worker", "MediaHOT 網站"]]
    pids = json.loads(S.PIDS.read_text(encoding="utf-8"))
    assert set(pids) == {"MediaHOT API", "MediaHOT worker", "MediaHOT 網站", "start_dev"} and pids["start_dev"] == os.getpid()
    assert "沒有 LLM_API_KEY" in capsys.readouterr().out


@windows
def test_a_local_model_in_the_env_file_turns_model_calls_on_with_the_local_defaults(started):
    started.file = {"LLM_API_KEY": "ollama", "LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_MODEL": "qwen3.5:9b-ctx32k"}
    env = started.run()
    assert env["MODEL_CALLS_ENABLED"] == "true" and env["LLM_MODEL"] == "qwen3.5:9b-ctx32k"
    assert {k: env[k] for k in S.LOCAL_MODEL_DEFAULTS} == S.LOCAL_MODEL_DEFAULTS
    assert env["API_PORT"] == "3101" and env["RAG_INDEX_ENABLED"] == "true"


@windows
def test_the_flags_turn_collection_and_models_off_and_pick_the_database(started):
    started.file = {"LLM_API_KEY": "key", "LLM_BASE_URL": "https://api.example.com/v1"}
    env = started.run("--no-collect", "--no-models", "--db", "seeded")
    assert env["COLLECT_ENABLED"] == "false" and env["MODEL_CALLS_ENABLED"] == "false" and "LLM_API_KEY" not in env
    assert env["DATABASE_URL"].endswith("/seeded") and started.prepared == []  # the seeded database is left as it was made
    cloud = started.run()
    assert cloud["MODEL_CALLS_ENABLED"] == "true" and "LLM_STRUCTURED_OUTPUT" not in cloud


@windows
def test_separate_windows_are_recorded_without_the_script_itself(started):
    started.run("--separate")
    assert all(command[:2] == ["cmd", "/k"] for command, _, _ in started.launched) and started.followed == []
    assert "start_dev" not in json.loads(S.PIDS.read_text(encoding="utf-8"))


@windows
@pytest.mark.parametrize("flags", [("--models",), ("--models", "--no-models")])
def test_asking_for_models_without_a_key_or_with_no_models_ends_before_anything_starts(started, flags):
    with pytest.raises(SystemExit):
        started.run(*flags)
    assert started.launched == [] and started.databases == []


def _processes(monkeypatch, rows) -> None:
    listing = SimpleNamespace(stdout=json.dumps(rows).encode("utf-8"))
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: listing)


def test_a_scan_takes_only_the_development_processes_of_the_known_programs(monkeypatch):
    rows = [
        {"ProcessId": 11, "Name": "python.exe", "CommandLine": r"C:\Python312\python.exe -m uvicorn --factory newsroom.api.app:build_app --port 3101"},
        {"ProcessId": 12, "Name": "node.exe", "CommandLine": r"node  E:\repo\AIHOT\apps\web\node_modules\.bin\react-router dev"},
        {"ProcessId": 13, "Name": "Python.EXE", "CommandLine": "newsroom worker"},
        {"ProcessId": 14, "Name": "Code.exe", "CommandLine": "code --folder newsroom worker notes.md"},  # another program that only mentions it
        {"ProcessId": 15, "Name": "python.exe", "CommandLine": "python stop_dev.py --scan newsroom worker"},  # this script
        {"ProcessId": os.getpid(), "Name": "python.exe", "CommandLine": "newsroom worker"},  # this process
        {"ProcessId": 16, "Name": "python.exe", "CommandLine": "python -m pytest tests"},
        {"ProcessId": 17, "Name": "node.exe", "CommandLine": None},
    ]
    _processes(monkeypatch, rows)
    assert [pid for pid, _ in T.scan()] == [11, 12, 13]
    _processes(monkeypatch, rows[0])  # PowerShell prints one process as an object, not a list
    assert [pid for pid, _ in T.scan()] == [11]


def test_the_recorded_processes_are_stopped_and_the_record_removed(monkeypatch, tmp_path, capsys):
    record = tmp_path / "dev-pids.json"
    monkeypatch.setattr(T, "PIDS", record)
    killed: list[int] = []
    monkeypatch.setattr(T, "kill_tree", lambda pid: killed.append(pid) or pid != 2)
    assert T.recorded() == 0 and killed == []  # nothing was recorded
    record.write_text(json.dumps({"MediaHOT API": 1, "MediaHOT worker": 2, "start_dev": 3}), encoding="utf-8")
    assert T.recorded() == 2 and killed == [1, 2, 3] and not record.exists()
    out = capsys.readouterr().out
    assert "已停止：MediaHOT API（1）" in out and "已經不在：MediaHOT worker（2）" in out
