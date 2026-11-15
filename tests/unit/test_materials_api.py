"""Who may start a download from the 素材區 (api/routes/materials.py): nobody has to log in, but the request has to carry the header only the site's pages send."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from newsroom.api.routes import materials as route
from newsroom.collect import picks


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.state.pool = object()

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # the app's own middleware gives every request one, which the error responses carry
        request.state.request_id = "test"
        return await call_next(request)

    route.register_materials(app)
    started: list[tuple] = []
    monkeypatch.setattr(picks, "start", lambda pool, article_id, kinds: started.append((article_id, kinds)) or "started")
    c = TestClient(app)
    c.started = started  # type: ignore[attr-defined]
    return c


def post(client, headers=None, body=None):
    return client.post("/api/materials/abc/fetch", json=body if body is not None else {"kinds": ["photo"]}, headers=headers or {})


def test_a_download_needs_the_header_the_sites_pages_send_and_nothing_else(client):
    assert post(client).status_code == 403
    assert post(client, {"x-materials-request": "0"}).status_code == 403
    assert client.started == []
    ok = post(client, {"x-materials-request": "1"})
    assert ok.status_code == 202 and ok.json() == {"state": "queued", "wanted": ["photo"]}
    assert client.started == [("abc", ["photo"])]


def test_a_kind_that_does_not_exist_is_a_bad_request(client):
    assert post(client, {"x-materials-request": "1"}, {"kinds": ["audio"]}).status_code == 400
    assert client.started == []


def test_with_no_kinds_named_all_three_are_asked_for(client):
    assert post(client, {"x-materials-request": "1"}, {}).json()["wanted"] == ["article", "photo", "video"]


class _OnePool:
    from contextlib import contextmanager

    @contextmanager
    def connection(self):
        yield None


def test_a_downloaded_file_is_sent_with_its_type_and_anything_else_is_not_found(client, monkeypatch, tmp_path):
    client.app.state.pool = _OnePool()
    files = {"shot.png": b"png-bytes", "text.md": "# 標題".encode(), "page.html": b"<script>1</script>"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(picks, "pick_file", lambda conn, article_id, path: tmp_path / path if article_id == "abc" and path in files else None)
    shot = client.get("/api/materials/abc/file", params={"path": "shot.png"})
    assert shot.status_code == 200 and shot.content == b"png-bytes" and shot.headers["content-type"] == "image/png"
    assert shot.headers["x-content-type-options"] == "nosniff"
    text = client.get("/api/materials/abc/file", params={"path": "text.md"})
    assert text.headers["content-type"] == "text/plain; charset=utf-8" and text.text == "# 標題"
    for params in ({"path": "page.html"}, {"path": "missing.png"}, {}):  # a type that is not shown, a file that is not the pick's, no path
        assert client.get("/api/materials/abc/file", params=params).status_code == 404, params
    assert client.get("/api/materials/other/file", params={"path": "shot.png"}).status_code == 404
