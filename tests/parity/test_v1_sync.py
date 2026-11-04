"""/api/v1/selected/snapshot and /changes on the seeded dataset (the ledger the seed's publishing wrote)."""

from __future__ import annotations

import pytest


def test_snapshot_default_and_minimal(compare):
    full = compare("/api/v1/selected/snapshot", drop=("asOf", "nextPage"))
    assert full["status"] == 200 and full["body"]["count"] >= 5 and full["body"]["fields"] == "default"
    minimal = compare("/api/v1/selected/snapshot?fields=minimal", drop=("asOf", "nextPage"))
    assert minimal["body"]["fields"] == "minimal" and "summary" not in minimal["body"]["items"][0]


def test_snapshot_paging_and_projection_rules(compare, node_api, py_api):
    import httpx

    first_node = httpx.get(node_api + "/api/v1/selected/snapshot?limit=3").json()
    first_py = py_api.get("/api/v1/selected/snapshot?limit=3").json()
    assert first_node["hasMore"] and first_py["hasMore"]
    for a, b in ((first_node, first_py),):
        assert {k: v for k, v in a.items() if k not in ("asOf", "nextPage")} == {k: v for k, v in b.items() if k not in ("asOf", "nextPage")}
    # Each server continues with a token the other made, as the tokens are not tied to a server (only to the ledger).
    from urllib.parse import quote

    for token in (first_node["nextPage"], first_py["nextPage"]):
        a = httpx.get(node_api + f"/api/v1/selected/snapshot?limit=3&page={quote(token)}").json()
        b = py_api.get(f"/api/v1/selected/snapshot?limit=3&page={quote(token)}").json()
        assert {k: v for k, v in a.items() if k != "nextPage"} == {k: v for k, v in b.items() if k != "nextPage"}
    compare(f"/api/v1/selected/snapshot?fields=minimal&limit=3&page={quote(first_node['nextPage'])}")
    compare("/api/v1/selected/snapshot?page=garbage")
    compare("/api/v1/selected/snapshot?page=ax1.e30")


def test_changes(compare, node_api):
    import httpx

    cursor = httpx.get(node_api + "/api/v1/selected/snapshot?limit=1").json()["cursor"]
    got = compare(f"/api/v1/selected/changes?cursor={cursor}")
    assert got["status"] == 200 and got["body"]["count"] == 0
    # From the beginning of the ledger.
    from newsroom.engine.lib.cursor import encode_cursor

    epoch = httpx.get(node_api + "/api/v1/selected/snapshot?limit=1").json()["cursor"]
    from newsroom.engine.lib.cursor import decode_cursor

    c = decode_cursor("ax1", epoch)
    start = encode_cursor("ax1", {"k": "sync", "e": c["e"], "w": 0, "f": "default"})
    first = compare(f"/api/v1/selected/changes?cursor={start}&limit=4")
    assert first["body"]["count"] == 4 and first["body"]["hasMore"]
    compare(f"/api/v1/selected/changes?cursor={first['body']['cursor']}&limit=100")
    minimal = encode_cursor("ax1", {"k": "sync", "e": c["e"], "w": 0, "f": "minimal"})
    compare(f"/api/v1/selected/changes?cursor={minimal}")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/selected/changes",
        "/api/v1/selected/changes?cursor=",
        "/api/v1/selected/changes?cursor=garbage",
        "/api/v1/selected/changes?cursor=ax1.e30",
        "/api/v1/selected/snapshot?fields=nope",
        "/api/v1/selected/snapshot?limit=0",
        "/api/v1/selected/snapshot?limit=1001",
        "/api/v1/selected/changes?limit=101",
    ],
)
def test_sync_errors(compare, path):
    compare(path)
