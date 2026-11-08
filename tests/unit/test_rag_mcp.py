"""The semantic-search MCP server (/api/mcp-rag) without a model or a database: what it lists, what it refuses, what it returns, and that the original
/api/mcp still lists its own seven tools. The search is replaced by a fake."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from newsroom.api.app import build_app
from newsroom.engine.publication.llms import MCP_TOOL_SUFFIXES
from newsroom.rag import mcp as rag_mcp
from newsroom.rag.index import EmbedError

HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
TOOL = rag_mcp.tool_name()


class _Pool:
    @contextmanager
    def connection(self):
        yield None


def _hit(title: str, score: float) -> dict:
    return {"article_id": title, "score": score, "title": title, "summary": f"{title}的摘要", "url": f"https://example.com/{title}"}


def _call(args: dict) -> dict:
    return rag_mcp.handle_message(_Pool(), {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": TOOL, "arguments": args}})["result"]  # type: ignore[index]


def test_it_lists_one_read_only_tool_with_a_required_query():
    tools = rag_mcp.handle_message(_Pool(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]  # type: ignore[index]
    assert [t["name"] for t in tools] == [TOOL]
    assert tools[0]["inputSchema"]["required"] == ["q"] and tools[0]["annotations"]["readOnlyHint"] is True


def test_results_come_back_best_first_with_a_score_and_loosely_related_ones_are_left_out(monkeypatch):
    monkeypatch.setattr(rag_mcp, "search", lambda conn, q, limit: [_hit("甲", 0.72), _hit("乙", 0.55), _hit("丙", 0.3)])
    res = _call({"q": "影片模型"})
    assert [i["title"] for i in res["structuredContent"]["items"]] == ["甲", "乙"]
    assert res["structuredContent"]["items"][0]["rank"] == 1 and "isError" not in res
    assert "1. 甲（相似度 0.72）" in res["content"][0]["text"]


def test_nothing_near_is_an_empty_result_not_an_error(monkeypatch):
    monkeypatch.setattr(rag_mcp, "search", lambda *a: [_hit("離題", 0.2)])
    res = _call({"q": "今天天氣"})
    assert res["structuredContent"]["items"] == [] and "isError" not in res


def test_limit_goes_to_the_search_and_defaults_to_five(monkeypatch):
    seen = []
    monkeypatch.setattr(rag_mcp, "search", lambda conn, q, limit: seen.append(limit) or [])
    _call({"q": "任何問題"})
    _call({"q": "任何問題", "limit": 3})
    assert seen == [5, 3]


def test_a_model_that_is_off_is_a_tool_error(monkeypatch):
    def down(*a):
        raise EmbedError("no server")

    monkeypatch.setattr(rag_mcp, "search", down)
    res = _call({"q": "任何問題"})
    assert res["isError"] is True and res["structuredContent"]["error"]["code"] == "model_unavailable"


@pytest.mark.parametrize("args", [{}, {"q": "a"}, {"q": "x" * 201}, {"q": "好", "limit": 11}, {"q": "好", "extra": 1}])
def test_bad_arguments_are_refused_before_any_search(args, monkeypatch):
    monkeypatch.setattr(rag_mcp, "search", lambda *a: pytest.fail("the search ran"))
    assert _call(args)["isError"] is True


def test_an_unknown_tool_is_a_protocol_error():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mediahot_get_latest", "arguments": {}}}
    assert rag_mcp.handle_message(_Pool(), msg)["error"]["code"] == -32602  # type: ignore[index]


def _post(client: TestClient, path: str, method: str, params: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}
    res = client.post(path, headers={**HEADERS, "host": "127.0.0.1"}, content=json.dumps(body))
    return res, json.loads(res.text.split("data: ", 1)[1])


def test_both_servers_answer_over_http_each_with_its_own_tools():
    app = build_app("postgres://unused/unused")
    app.state.pool = _Pool()
    client = TestClient(app)
    original, answer = _post(client, "/api/mcp", "tools/list")
    assert original.status_code == 200 and len(answer["result"]["tools"]) == len(MCP_TOOL_SUFFIXES) and TOOL not in [t["name"] for t in answer["result"]["tools"]]
    semantic, answer = _post(client, "/api/mcp-rag", "tools/list")
    assert semantic.status_code == 200 and [t["name"] for t in answer["result"]["tools"]] == [TOOL]
    assert _post(client, "/api/mcp-rag", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}})[1]["result"]["serverInfo"]["name"].endswith("-semantic")


def test_the_second_server_keeps_the_hosts_check():
    client = TestClient(build_app("postgres://unused/unused"))
    res = client.post("/api/mcp-rag", headers={**HEADERS, "host": "evil.example"}, content="{}")
    assert res.status_code == 421
