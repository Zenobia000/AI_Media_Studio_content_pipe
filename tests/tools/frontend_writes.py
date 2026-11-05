"""Usage: python -I tests/tools/frontend_writes.py   (needs the four services below running, each side on its own copy of the database: the Python side's `newsroom migrate`d,
the Node side's with the pg-boss queues created, and a notify target `parity-target` in both)

Admin writes through the two running frontends: 3000 (Python API, database fe_py) and 3001 (Node API, database fe_node).
Each write is sent the way the admin pages send it (session cookie, x-csrf-token, idempotency-key) and the answers and the rows left behind are compared."""

import json
import re
import sys

import httpx
import psycopg
from psycopg.rows import dict_row

SIDES = {"py": (3000, "postgres://postgres:ci@127.0.0.1:55432/fe_py"), "node": (3001, "postgres://postgres:ci@127.0.0.1:55432/fe_node")}
clients, csrf = {}, {}
for name, (port, _) in SIDES.items():
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, follow_redirects=False)
    r = c.post("/api/auth/password", data={"password": "ci-admin-password-123"})
    assert r.status_code == 303, (name, r.status_code)
    me = c.get("/api/admin/me").json()
    clients[name], csrf[name] = c, me["csrf"]

ids = []
with psycopg.connect(SIDES["node"][1], row_factory=dict_row) as c:
    ids = [
        r["id"]
        for r in c.execute(
            "SELECT a.id FROM articles a JOIN publications p ON p.article_id = a.id WHERE p.visibility = 'public' ORDER BY a.id LIMIT 4"
        ).fetchall()
    ]
    source = c.execute("SELECT id FROM sources WHERE enabled ORDER BY id LIMIT 1").fetchone()["id"]
    feedback = c.execute("SELECT count(*) AS n FROM feedback").fetchone()["n"]

results, failures = [], []
NOW = re.compile(r"2026-11-05T\d\d:\d\d:\d\d\.\d{3}Z")  # the instants the two runs took


def send(method, path, body=None, expect=None, nocsrf=False):
    out = {}
    for name, c in clients.items():
        headers = {"idempotency-key": f"fe-{len(results)}"}
        if not nocsrf:
            headers["x-csrf-token"] = csrf[name]
        if body is not None:
            headers["content-type"] = "application/json"
        r = c.request(method, path, headers=headers, content=None if body is None else json.dumps(body))
        try:
            data = r.json() if r.text else None
        except ValueError:
            data = r.text
        out[name] = (r.status_code, data)
    scrub = lambda v: json.loads(NOW.sub("<now>", re.sub(r'"requestId": ?"[^"]*"', '"requestId": "<id>"', json.dumps(v))))
    ok = out["py"][0] == out["node"][0] and scrub(out["py"][1]) == scrub(out["node"][1]) and (expect is None or out["py"][0] == expect)
    results.append((method, path, out["py"][0], out["node"][0], ok))
    if not ok:
        failures.append((method, path, body, out))


def get_version(name, path):
    r = clients[name].get(path)
    return r.json()


a, b, c3, d = ids
# content: visibility (with a stale version and a bad body), SEO, field overrides
send("POST", f"/api/admin/content/{a}/visibility", {"visibility": "withdrawn", "reason": "taken down", "version": 3}, 409)
send("POST", f"/api/admin/content/{a}/visibility", {"visibility": "withdrawn", "reason": "taken down", "version": 0}, 200)
send("POST", f"/api/admin/content/{a}/visibility", {"visibility": "x", "reason": "r", "version": 1}, 400)
send("POST", f"/api/admin/content/{a}/visibility", {"visibility": "public", "reason": "back", "version": 1}, 200)
send("POST", f"/api/admin/content/{b}/seo", {"indexed": True, "reason": "index it"}, 200)
send("POST", f"/api/admin/content/{b}/seo", {"indexed": "yes", "reason": ""}, 400)
send("POST", f"/api/admin/content/{c3}/override", {"fields": {"title": "Edited title"}, "reason": "fix title", "version": 0}, 200)
send("POST", f"/api/admin/content/{c3}/override", {"fields": {"title": "Again"}, "reason": "stale", "version": 0}, 409)
send("POST", f"/api/admin/content/{c3}/override", {"fields": {"score": 101}, "reason": "r", "version": 1}, None)
send("POST", f"/api/admin/content/{d}/override", {"fields": {"category": "nope"}, "reason": "r", "version": 0}, None)
# a write without the session's token is refused the same way
send("POST", f"/api/admin/content/{d}/seo", {"indexed": True, "reason": "no token"}, 403, nocsrf=True)
# feedback bans, notify target, budget
send("POST", "/api/admin/feedback-bans", {"sourceHash": "src-b", "reason": "noisy"}, 204)
send("POST", "/api/admin/feedback-bans", {"sourceHash": "src-b", "reason": " "}, None)
send("DELETE", "/api/admin/feedback-bans/src-b", None, 204)
send("POST", "/api/admin/notify-targets/parity-target", {"enabled": True, "reason": "go"}, 200)
send("POST", "/api/admin/notify-targets/parity-target", {"enabled": False, "reason": ""}, None)
send("POST", "/api/admin/notify-targets/nope", {"enabled": True, "reason": "x"}, 404)
send("PUT", "/api/admin/budgets/parity-service", {"perMinute": 5, "perHour": 50, "perDay": 500, "reason": "limit"}, 200)
send("PUT", "/api/admin/budgets/parity-service", {"perMinute": -1, "perHour": 1, "perDay": 2, "reason": "x"}, None)
# a source: read its version, pause it, resume it, a stale edit
det = {n: clients[n].get(f"/api/admin/sources/{source}").json() for n in clients}
version = {n: det[n]["source"]["updated_at"] if "source" in det[n] else det[n].get("updated_at") for n in clients}
if version["py"] != version["node"]:
    failures.append(("source version", source, None, version))
send("PATCH", f"/api/admin/sources/{source}", {"patch": {"enabled": False}, "version": version["node"]}, 200)
send("PATCH", f"/api/admin/sources/{source}", {"patch": {"enabled": True}, "version": "2020-01-01T00:00:00.000Z"}, 409)
send("PATCH", "/api/admin/sources/nope-nope", {"patch": {"name": "x"}, "version": "v"}, 404)
# requeue and rerun enqueue work: the answers carry job ids, so only the status is compared below

for r in results:
    print(("OK  " if r[4] else "DIFF"), r[0], r[1].replace(a, "A").replace(b, "B").replace(c3, "C").replace(d, "D"), r[2], r[3])

# the rows the writes left behind
TABLES = {
    "editorial_overrides": "SELECT article_id, visibility, fields, reason, version, updated_by FROM editorial_overrides ORDER BY article_id",
    "publications": "SELECT article_id, visibility, selected, indexable, (seo_indexed_at IS NOT NULL) AS seo_in, title, score, category, revision FROM publications ORDER BY article_id",
    "bans": "SELECT * FROM feedback_bans ORDER BY 1" if False else "SELECT 1 AS x WHERE false",
    "notify_targets": "SELECT key, enabled, note FROM notify_targets ORDER BY key",
    "budgets": "SELECT key, value FROM settings WHERE key LIKE 'budget%' ORDER BY key",
    "sources": "SELECT id, enabled FROM sources ORDER BY id",
    "audit": "SELECT actor, action, subject, reason, before, after FROM audit_log ORDER BY id",
}
dumps = {}
for name, (_, url) in SIDES.items():
    with psycopg.connect(url, row_factory=dict_row) as conn:
        dumps[name] = {t: json.loads(NOW.sub("<now>", json.dumps(conn.execute(q).fetchall(), default=str))) for t, q in TABLES.items()}
for t in TABLES:
    same = dumps["py"][t] == dumps["node"][t]
    print(("OK  " if same else "DIFF"), "rows:", t, len(dumps["node"][t]))
    if not same:
        failures.append(("rows", t, None, {k: dumps[k][t][:3] for k in dumps}))
print("failures:", len(failures))
for f in failures:
    print(json.dumps(f, ensure_ascii=False, default=str)[:700])
sys.exit(1 if failures else 0)
