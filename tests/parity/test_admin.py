"""/api/auth and /api/admin: the same requests to the TypeScript server and the Python app, each on its own copy of one database (the admin routes
write), must answer the same and leave the same rows."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, PORT, clone_template
from tests.parity.test_publish import _admin

PASSWORD = ENV["ADMIN_PASSWORD"]
STARTED = datetime.now(UTC)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _mask(value):
    """Instants this run made are "NOW": the two servers do not share a clock reading."""
    if isinstance(value, str) and ISO.match(value) and datetime.fromisoformat(value.replace("Z", "+00:00")) >= STARTED:
        return "NOW"
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items() if k != "requestId"}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


class Side:
    def __init__(self, name, send):
        self.name, self.send, self.cookie, self.csrf = name, send, None, None

    def request(self, method, path, json_body=None, form=None, headers=None, csrf=True):
        h = {**(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        if csrf and self.csrf and method not in ("GET", "HEAD"):
            h["X-CSRF-Token"] = self.csrf
        kwargs = {}
        if callable(json_body):  # a body that differs per side (each database stamps its own versions)
            json_body = json_body(self.name)
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body)
            h["Content-Type"] = "application/json"
        if form is not None:
            kwargs["data"] = form
        return self.send(method, path, headers=h, **kwargs)


@pytest.fixture(scope="module")
def sides(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["adm_node", "adm_py"])
    urls = {"node": urls["adm_node"], "py": urls["adm_py"]}
    port = PORT + 2
    env = {
        **os.environ,
        **ENV,
        "DATABASE_URL": urls["node"],
        "API_PORT": str(port),
        "AIHOT_DATA_DIR": os.path.join(os.environ.get("TEMP", "/tmp"), "adm-node-data"),
    }
    proc = subprocess.Popen(["node", "apps/api/src/main.ts"], cwd=AIHOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    node_base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(f"{node_base}/api/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("the TypeScript API did not start")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = urls["py"]
    os.environ["AIHOT_DATA_DIR"] = os.path.join(os.environ.get("TEMP", "/tmp"), "adm-py-data")
    from newsroom.api.app import build_app

    with TestClient(build_app(), base_url=f"http://127.0.0.1:{port + 1}", follow_redirects=False) as client:
        node = Side("node", lambda m, p, **k: httpx.request(m, node_base + p, follow_redirects=False, timeout=30, **k))
        py = Side("py", lambda m, p, **k: client.request(m, p, **k))
        yield {"node": node, "py": py, "urls": urls}
    if previous:
        os.environ["DATABASE_URL"] = previous
    proc.terminate()
    proc.wait(10)
    admin.close()


def both(sides, method, path, **kw):
    a = sides["node"].request(method, path, **kw)
    b = sides["py"].request(method, path, **kw)
    return a, b


def same(sides, method, path, status=None, **kw):
    a, b = both(sides, method, path, **kw)
    assert a.status_code == b.status_code, f"{method} {path}: {a.status_code} {a.text[:200]} vs {b.status_code} {b.text[:200]}"
    if status:
        assert a.status_code == status, f"{method} {path} {kw.get('json_body')}: {a.status_code} {a.text[:200]}"
    ctype = a.headers.get("content-type", "")
    if "json" in ctype:
        assert _mask(b.json()) == _mask(a.json()), f"{method} {path}\nTS: {a.text[:600]}\nPY: {b.text[:600]}"
    else:
        assert b.text == a.text, f"{method} {path}"
    for h in ("cache-control", "location"):
        assert a.headers.get(h) == b.headers.get(h), f"{method} {path} header {h}: {a.headers.get(h)!r} vs {b.headers.get(h)!r}"
    assert a.headers.get("content-type", "").split(";")[0] == b.headers.get("content-type", "").split(";")[0], path
    return a, b


def cookie_of(res):
    raw = res.headers.get_list("set-cookie")[0] if hasattr(res.headers, "get_list") else res.headers["set-cookie"]
    return raw


def attrs_of(set_cookie: str) -> list[str]:
    return [p.strip() for p in set_cookie.split(";")[1:]]


def test_sign_in_and_guard(sides):
    # Refusals before and after signing in.
    same(sides, "GET", "/api/admin/me", 401)
    same(sides, "GET", "/api/auth/options", 200)
    a, b = both(sides, "POST", "/api/auth/password", form={"password": "wrong password", "return": "/admin/feedback"})
    assert (
        (a.status_code, a.headers["location"])
        == (b.status_code, b.headers["location"])
        == (303, "/admin/login?return=%2Fadmin%2Ffeedback&error=wrong")
    )
    for ret in ("https://evil.example/x", "//evil.example", "/admin/a b?x=中"):
        a, b = both(sides, "POST", "/api/auth/password", form={"password": PASSWORD, "return": ret})
        assert (a.status_code, a.headers["location"]) == (b.status_code, b.headers["location"]), ret
        assert attrs_of(cookie_of(a)) == attrs_of(cookie_of(b))
    for side, res in zip(
        (sides["node"], sides["py"]), both(sides, "POST", "/api/auth/password", form={"password": PASSWORD, "return": "/admin"}), strict=True
    ):
        side.cookie = cookie_of(res).split(";")[0]
    a, b = both(sides, "GET", "/api/admin/me")
    assert a.status_code == b.status_code == 200
    assert {k: v for k, v in a.json().items() if k != "csrf"} == {k: v for k, v in b.json().items() if k != "csrf"}
    sides["node"].csrf, sides["py"].csrf = a.json()["csrf"], b.json()["csrf"]
    assert len(a.json()["csrf"]) == len(b.json()["csrf"])
    # A write without the token, and the login redirect with a live session.
    same(sides, "POST", "/api/admin/notify-targets/x", 403, json_body={"enabled": True, "reason": "r"}, csrf=False)
    same(sides, "GET", "/api/auth/login?return=/admin/feedback", 302)
    same(sides, "POST", "/api/admin/feedback-bans", 400, headers={"Content-Type": "application/json"})


def test_logout_ends_the_session(sides):
    both(sides, "POST", "/api/auth/password", form={"password": PASSWORD, "return": "/admin"})
    fresh = {}
    for name, res in zip(("node", "py"), both(sides, "POST", "/api/auth/password", form={"password": PASSWORD, "return": "/admin"}), strict=True):
        fresh[name] = cookie_of(res).split(";")[0]
    for name in ("node", "py"):
        side = sides[name]
        out = side.send("POST", "/api/auth/logout", headers={"Cookie": fresh[name]})
        assert out.status_code == 303 and out.headers["location"] == "/"
        assert "Max-Age=0" in cookie_of(out)
        assert side.send("GET", "/api/admin/me", headers={"Cookie": fresh[name]}).status_code == 401


def seed_feedback(sides):
    sql = """INSERT INTO feedback (content, email, page_url, source_hash, status, kind, idea, note, created_at, updated_at) VALUES
      ('Search is slow', 'a@example.com', '/search', 'src-a', 'new', NULL, NULL, NULL, '2026-11-04 05:00:00+00', '2026-11-04 05:00:00+00'),
      ('Please add dark mode', NULL, '/', 'src-b', 'new', 'suggestion', 'dark mode', NULL, '2026-11-04 05:10:00+00', '2026-11-04 05:10:00+00'),
      ('Dark mode please', 'b@example.com', '/hot', 'src-c', 'triaged', 'suggestion', 'dark mode', 'seen', '2026-11-04 05:20:00+00', '2026-11-04 05:20:00+00'),
      ('Buy cheap stuff', NULL, NULL, 'src-spam', 'spam', NULL, NULL, NULL, '2026-11-04 05:30:00+00', '2026-11-04 05:30:00+00'),
      ('The about page has a typo', 'c@example.com', 'https://x.example/about', 'src-a', 'resolved', 'bug', NULL, 'fixed', '2026-11-04 05:40:00+00', '2026-11-04 05:40:00+00')"""
    for url in sides["urls"].values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(sql)
            c.execute(
                "INSERT INTO feedback_bans (source_hash, reason, created_by, expires_at) VALUES ('src-spam', 'spam', 'admin:1', '2027-01-01 00:00:00+00') ON CONFLICT DO NOTHING"
            )


def test_feedback(sides):
    seed_feedback(sides)
    listing = same(sides, "GET", "/api/admin/feedback", 200)[0].json()
    assert listing["rows"] and listing["bans"] and listing["ideas"]
    for query in ("?status=new", "?kind=suggestion", "?idea=dark%20mode", "?q=typo", "?q=example.com&page=1", "?page=2", "?status=nope"):
        same(sides, "GET", "/api/admin/feedback" + query, 200)
    row = next(r for r in listing["rows"] if r["content"] == "Search is slow")
    fid, version = row["id"], row["updated_at"]
    same(sides, "PATCH", f"/api/admin/feedback/{fid}", 200, json_body={"status": "triaged", "kind": "bug", "note": "looking", "version": version})
    same(sides, "PATCH", f"/api/admin/feedback/{fid}", 409, json_body={"status": "resolved", "version": version})  # stale
    a, b = same(sides, "GET", "/api/admin/feedback?q=slow", 200)
    versions = {"node": a.json()["rows"][0]["updated_at"], "py": b.json()["rows"][0]["updated_at"]}

    def with_version(**fields):
        return lambda side: {**fields, "version": versions[side]}

    same(sides, "PATCH", f"/api/admin/feedback/{fid}", 500, json_body=with_version(status="bogus"))
    same(sides, "PATCH", f"/api/admin/feedback/{fid}", 500, json_body=with_version(idea="x"))  # only a suggestion has an idea
    same(sides, "PATCH", f"/api/admin/feedback/{fid}", 500, json_body=with_version(kind="suggestion", idea="y" * 41))
    same(
        sides,
        "PATCH",
        f"/api/admin/feedback/{fid}",
        200,
        json_body=with_version(kind="suggestion", idea=" night mode ", note=None),
    )
    same(sides, "PATCH", "/api/admin/feedback/99999", 404, json_body={"status": "new", "version": version})
    same(sides, "POST", "/api/admin/feedback-bans", 204, json_body={"sourceHash": "src-b", "reason": "noisy"})
    same(sides, "POST", "/api/admin/feedback-bans", 500, json_body={"sourceHash": "src-b", "reason": " "})
    with_email = next(r for r in listing["rows"] if r["email"])
    same(sides, "POST", "/api/admin/feedback-bans", 204, json_body={"feedbackId": with_email["id"], "reason": "abuse"})
    no_email = next(r for r in listing["rows"] if not r["email"])
    same(sides, "POST", "/api/admin/feedback-bans", 400, json_body={"feedbackId": no_email["id"], "reason": "abuse"})
    same(sides, "GET", "/api/admin/feedback", 200)
    same(sides, "DELETE", "/api/admin/feedback-bans/src-b", 204)
    same(sides, "DELETE", "/api/admin/feedback-bans/never-banned", 204)
    same(sides, "GET", f"/api/admin/feedback/{fid}/screenshot", 404)
    same(sides, "POST", f"/api/admin/feedback/{fid}/erase", 500, json_body={"reason": ""})
    same(sides, "POST", f"/api/admin/feedback/{fid}/erase", 200, json_body={"reason": "sender asked"})
    same(sides, "POST", "/api/admin/feedback/99999/erase", 404, json_body={"reason": "x"})
    same(sides, "GET", "/api/admin/feedback", 200)
    same(sides, "GET", "/api/admin/audit", 200)
    same(sides, "GET", "/api/admin/audit?action=feedback", 200)
    same(sides, "GET", f"/api/admin/audit?subject=feedback:{fid}", 200)


def test_settings(sides):
    keys = same(sides, "GET", "/api/admin/settings", 200)[0].json()
    assert "contact" in keys and "budgets" in keys
    with psycopg.connect(sides["urls"]["node"], autocommit=True, row_factory=dict_row) as c:
        targets = [r["key"] for r in c.execute("SELECT key FROM notify_targets").fetchall()]
    for url in sides["urls"].values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(
                "INSERT INTO notify_targets (key, purpose, kind, enabled) VALUES ('parity-target', 'content', 'log', false) ON CONFLICT DO NOTHING"
            )
    same(sides, "POST", "/api/admin/notify-targets/parity-target", 200, json_body={"enabled": True, "reason": "go"})
    same(sides, "POST", "/api/admin/notify-targets/parity-target", 200, json_body={"enabled": False, "reason": "stop"})
    same(sides, "POST", "/api/admin/notify-targets/parity-target", 500, json_body={"enabled": True, "reason": ""})
    same(sides, "POST", "/api/admin/notify-targets/nope", 404, json_body={"enabled": True, "reason": "x"})
    del targets
    same(sides, "PUT", "/api/admin/budgets/parity-service", 200, json_body={"perMinute": 5, "perHour": 50, "perDay": 500, "reason": "limit"})
    same(sides, "PUT", "/api/admin/budgets/parity-service", 200, json_body={"perMinute": 0, "perHour": 1, "perDay": 2, "reason": "again"})
    same(sides, "PUT", "/api/admin/budgets/parity-service", 500, json_body={"perMinute": -1, "perHour": 1, "perDay": 2, "reason": "x"})
    same(sides, "PUT", "/api/admin/budgets/parity-service", 500, json_body={"perMinute": 1.5, "perHour": 1, "perDay": 2, "reason": "x"})
    same(sides, "PUT", "/api/admin/budgets/parity-service", 500, json_body={"perMinute": 1, "perHour": 1, "perDay": 2, "reason": ""})
    png = io.BytesIO()
    Image.effect_noise((200, 200), 40).convert("RGB").save(png, "PNG")
    image = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
    same(sides, "POST", "/api/admin/settings/contact-qr", 200, json_body={"slot": "wechatQr", "image": image})
    small = io.BytesIO()
    Image.new("RGB", (50, 50)).save(small, "PNG")
    same(
        sides,
        "POST",
        "/api/admin/settings/contact-qr",
        500,
        json_body={"slot": "feishuQr", "image": "data:image/png;base64," + base64.b64encode(small.getvalue()).decode()},
    )
    same(sides, "POST", "/api/admin/settings/contact-qr", 500, json_body={"slot": "nope", "image": image})
    same(sides, "POST", "/api/admin/settings/contact-qr", 400, json_body={"slot": "wechatQr", "image": "not a data url"})
    same(sides, "GET", "/api/admin/settings", 200)
    same(sides, "GET", "/api/admin/audit?action=budget", 200)
    # What the writes left behind.
    dumps = {}
    for name, url in sides["urls"].items():
        with psycopg.connect(url, row_factory=dict_row) as c:
            dumps[name] = {
                "feedback": [
                    dict(r)
                    for r in c.execute(
                        "SELECT id, content, email, page_url, screenshot_key, status, kind, idea, note FROM feedback ORDER BY id"
                    ).fetchall()
                ],
                "bans": [dict(r) for r in c.execute("SELECT source_hash, reason, created_by FROM feedback_bans ORDER BY source_hash").fetchall()],
                "audit": [
                    dict(r)
                    for r in c.execute(
                        "SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action NOT LIKE 'auth.%' ORDER BY id"
                    ).fetchall()
                ],
                "settings": [dict(r) for r in c.execute("SELECT key, value FROM settings WHERE key = 'contact_qr'").fetchall()],
            }
    assert _mask(dumps["py"]) == _mask(dumps["node"])
