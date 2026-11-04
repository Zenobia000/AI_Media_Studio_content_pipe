"""/api/mcp: the protocol layer and every tool, against the TypeScript server on the seeded dataset."""

from __future__ import annotations

import json

import pytest

JSON = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def rpc(method, params=None, id_=1):
    message = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


def call(compare, content, headers=None, method="POST"):
    return compare("/api/mcp", method=method, headers={**JSON, **(headers or {})}, content=content)


def tool(name, arguments=None):
    return rpc("tools/call", {"name": name, **({} if arguments is None else {"arguments": arguments})})


@pytest.mark.parametrize(
    "content",
    [
        rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        rpc("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}),
        rpc("initialize", {}),
        rpc("initialize"),
        rpc("ping"),
        rpc("ping", id_="abc"),
        rpc("tools/list"),
        rpc("nope/x"),
        rpc("subscriptions/listen"),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({"jsonrpc": "1.0", "id": 1, "method": "ping"}),
        json.dumps({"id": 1, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}),
        json.dumps([json.loads(rpc("ping"))]),
        "null",
        '"x"',
        "5",
        "{bad",
        "",
        rpc("tools/call"),
        rpc("tools/call", {}),
        rpc("tools/call", {"name": 5}),
        rpc("tools/call", {"name": "zzz", "arguments": {}}),
        rpc("tools/call", {"name": "mediahot_get_latest", "arguments": None}),
        rpc("tools/call", {"name": "mediahot_get_latest", "arguments": []}),
    ],
)
def test_protocol(compare, content):
    call(compare, content)


@pytest.mark.parametrize(
    ("headers", "method", "content"),
    [
        ({"Accept": "application/json"}, "POST", rpc("ping")),
        ({"Content-Type": "text/plain"}, "POST", "hello"),
        ({"Content-Type": "text/xml"}, "POST", "<a/>"),
        ({}, "GET", None),
        ({}, "DELETE", None),
        ({}, "PUT", "{}"),
        ({}, "PATCH", "{}"),
        ({"MCP-Protocol-Version": "1999-01-01"}, "POST", rpc("ping")),
        ({"MCP-Protocol-Version": "2025-06-18"}, "POST", rpc("ping")),
        ({"Origin": "http://evil.example"}, "POST", rpc("ping")),
        ({"Origin": "http://127.0.0.1:3000"}, "POST", rpc("ping")),
        ({"Origin": "http://localhost:5173"}, "POST", rpc("ping")),
        ({"Host": "evil.example"}, "POST", rpc("ping")),
        ({"X-Forwarded-Host": "evil.example"}, "POST", rpc("ping")),
        ({"X-Forwarded-Host": "localhost:3000"}, "POST", rpc("ping")),
        ({"MCP-Session-Id": "abc"}, "POST", rpc("ping")),
    ],
)
def test_http_layer(compare, headers, method, content):
    call(compare, content, headers, method)


def test_options(compare):
    compare("/api/mcp", method="OPTIONS", headers={"Origin": "http://127.0.0.1:3000"})
    compare("/api/mcp", method="OPTIONS", headers={"Origin": "http://evil.example"})
    compare("/api/mcp", method="OPTIONS")


def test_no_content_type(compare):
    compare("/api/mcp", method="POST", headers={"Accept": "application/json, text/event-stream"}, content=rpc("ping"))


def test_invalid_arguments(compare):
    n = "mediahot_"
    for name, args in [
        ("get_latest", {"limit": "5"}),
        ("get_latest", {"limit": 1.5}),
        ("get_latest", {"limit": 0}),
        ("get_latest", {"limit": 99}),
        ("get_latest", {"window": "1h"}),
        ("get_latest", {"category": "zz"}),
        ("get_latest", {"mode": 3}),
        ("get_latest", {"limit": None}),
        ("get_latest", {"x": 1}),
        ("get_latest", {"x": 1, "y": 2}),
        ("search", {}),
        ("search", {"q": "a"}),
        ("search", {"q": 5}),
        ("search", {"q": "ab", "limit": -1, "zz": 1, "yy": 2}),
        ("search", {"q": "x" * 201}),
        ("get_daily", {"date": "2026-1-1"}),
        ("get_daily", {"date": "2026-13-45"}),
        ("get_weekly", {"week": "2026-W99"}),
        ("get_weekly", {"week": "x"}),
        ("get_monthly", {"month": "2026-13"}),
        ("get_monthly", {"month": "x"}),
        ("get_story", {}),
        ("get_story", {"public_id": ""}),
        ("get_story", {"public_id": "nope"}),
        ("get_hot_topics", {"limit": 11}),
    ]:
        call(compare, tool(n + name, args))


def test_tools(compare):
    n = "mediahot_"
    calls = [
        ("get_latest", None),
        ("get_latest", {}),
        ("get_latest", {"window": "7d", "mode": "all", "limit": 30}),
        ("get_latest", {"category": "paper"}),
        ("search", {"q": "article"}),
        ("search", {"q": "zzzz-nothing"}),
        ("search", {"q": "Body", "window": "24h", "limit": 3}),
        ("search", {"q": "a\0b"}),
        ("get_hot_topics", None),
        ("get_hot_topics", {"limit": 2}),
        ("get_daily", None),
        ("get_daily", {"date": "2001-01-01"}),
        ("get_weekly", None),
        ("get_monthly", None),
        ("get_weekly", {"week": "2001-W01"}),
        ("get_monthly", {"month": "2001-01"}),
    ]
    for name, args in calls:
        got = call(compare, tool(n + name, args))
        assert got["status"] == 200
    hot = call(compare, tool(n + "get_hot_topics", {}))["body"]
    import re

    for pid in re.findall(r"public_id=([\w-]+)", hot)[:4]:
        call(compare, tool(n + "get_story", {"public_id": pid}))
        call(compare, tool(n + "get_story", {"public_id": pid, "report_limit": 2}))
