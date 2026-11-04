"""/api/admin/content: the same requests to the TypeScript server and the Python app, each on its own copy of one database, must answer the same and leave the
same rows - the search and the chain of an item, visibility and SEO decisions, manual corrections with their version checks and validation, and reruns."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.test_admin import both, same, sides  # noqa: F401  (the fixture)
from tests.parity.test_admin_sources import admin  # noqa: F401  (signs in, creates the Python queue)


def rows(url: str, query: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(query).fetchall()


@pytest.fixture(scope="module")
def ids(admin):  # noqa: F811
    r = rows(admin["urls"]["node"], "SELECT a.id, a.title, a.url FROM articles a JOIN publications p ON p.article_id = a.id WHERE p.visibility = 'public' ORDER BY a.id LIMIT 6")
    assert len(r) == 6
    return r


def post(admin, path, body, status=None, headers=None):
    a, b = both(admin, "POST", path, json_body=body, headers=headers)
    assert a.status_code == b.status_code, (path, body, a.status_code, a.text[:300], b.status_code, b.text[:300])
    if status:
        assert a.status_code == status, (path, body, a.text[:300])
    ja, jb = (r.json() if r.content else None for r in (a, b))
    if isinstance(ja, dict):
        ja.pop("requestId", None), jb.pop("requestId", None)
    assert ja == jb, f"{path} {body}\nTS: {a.text[:500]}\nPY: {b.text[:500]}"
    return a


def test_search_and_chain(admin, ids):
    same(admin, "GET", "/api/admin/content?q=", 200)
    same(admin, "GET", f"/api/admin/content?q={ids[0]['id']}", 200)
    same(admin, "GET", f"/api/admin/content?q={ids[0]['url']}", 200)
    same(admin, "GET", f"/api/admin/content?q={ids[0]['url']}%3Futm_source%3Dx", 200)
    same(admin, "GET", f"/api/admin/content?q={ids[1]['title'].split()[0]}", 200)
    same(admin, "GET", "/api/admin/content?q=zzzz-no-such", 200)
    for r in ids:
        same(admin, "GET", f"/api/admin/content/{r['id']}", 200)
    same(admin, "GET", "/api/admin/content/nope-nothing", 404)


def test_visibility_and_seo(admin, ids):
    a = ids[0]["id"]
    for body in ({}, {"visibility": "x", "reason": "r", "version": 0}, {"visibility": "public", "reason": "  ", "version": 0}, {"visibility": "public", "reason": "r", "version": -1},
                 {"visibility": "public", "reason": "r", "version": 1.5}, {"visibility": "public", "reason": "r", "version": "0"}):
        post(admin, f"/api/admin/content/{a}/visibility", body, 400)
    post(admin, f"/api/admin/content/{a}/visibility", {"visibility": "withdrawn", "reason": "wrong version", "version": 3}, 409)
    post(admin, f"/api/admin/content/{a}/visibility", {"visibility": "withdrawn", "reason": "taken down", "version": 0}, 200)
    post(admin, f"/api/admin/content/{a}/visibility", {"visibility": "summary-only", "reason": "summary", "version": 1}, 200)
    post(admin, f"/api/admin/content/{a}/visibility", {"visibility": "public", "reason": "back", "version": 2}, 200)
    post(admin, "/api/admin/content/nope-nothing/visibility", {"visibility": "public", "reason": "r", "version": 0}, 400)
    b = ids[1]["id"]
    post(admin, f"/api/admin/content/{b}/seo", {"indexed": True, "reason": "index it"}, 200)
    post(admin, f"/api/admin/content/{b}/seo", {"indexed": False, "reason": "not now"}, 200)
    post(admin, f"/api/admin/content/{b}/seo", {"indexed": "yes", "reason": ""}, 400)
    post(admin, "/api/admin/content/nope-nothing/seo", {"indexed": True, "reason": "r"}, 404)
    q = "SELECT article_id, visibility, reason, version, updated_by FROM editorial_overrides ORDER BY article_id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT article_id, visibility, selected, indexable, (seo_indexed_at IS NOT NULL) AS seo_in, (seo_excluded_at IS NOT NULL) AS seo_out, revision FROM publications ORDER BY article_id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)


def test_override(admin, ids):
    a = ids[2]["id"]
    bad = [
        {}, {"fields": {}, "reason": "r"}, {"fields": {"title": ""}, "reason": "r", "version": 0}, {"fields": {"title": "x" * 301}, "reason": "r", "version": 0},
        {"fields": {"category": "nope"}, "reason": "r", "version": 0}, {"fields": {"tags": ["a"] * 21}, "reason": "r", "version": 0}, {"fields": {"score": 101}, "reason": "r", "version": 0},
        {"fields": {"score": -1}, "reason": "r", "version": 0}, {"fields": {"score": "x"}, "reason": "r", "version": 0}, {"fields": {"selected": 1}, "reason": "r", "version": 0},
        {"fields": {"bogus": 1}, "reason": "r", "version": 0}, {"fields": {"title": "T"}, "reason": " ", "version": 0}, {"fields": "x", "reason": "r", "version": 0},
        {"fields": {}, "reason": "r", "version": 0, "clear": "x"}, {"fields": {}, "reason": "r", "version": 0, "clear": [1]},
    ]
    for body in bad:
        post(admin, f"/api/admin/content/{a}/override", body, 400)
    category = admin["node"].request("GET", f"/api/admin/content/{a}").json()["publication"]["category"]
    from newsroom.engine.taxonomy import category_keys

    other = next(k for k in category_keys() if k != category)
    post(admin, f"/api/admin/content/{a}/override", {"fields": {"title": "Corrected title"}, "reason": "typo", "version": 5}, 409)
    post(admin, f"/api/admin/content/{a}/override", {"fields": {"title": "Corrected title", "summary": "A summary", "score": 80.5, "silent": True}, "reason": "typo", "version": 0}, 200)
    post(admin, f"/api/admin/content/{a}/override", {"fields": {"category": other, "tags": ["x", "y"], "selected": True}, "reason": "reclassify", "version": 1}, 200)
    post(admin, f"/api/admin/content/{a}/override", {"fields": {"score": None}, "clear": ["title", "silent"], "reason": "clear some", "version": 2}, 200)
    post(admin, f"/api/admin/content/{a}/override", {"clear": ["summary"], "reason": "clear one", "version": 3}, 200)
    post(admin, "/api/admin/content/nope-nothing/override", {"fields": {}, "reason": "r", "version": 0}, 400)
    q = "SELECT article_id, fields, visibility, reason, version, updated_by FROM editorial_overrides ORDER BY article_id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT article_id, title, summary, category, tags, score, selected, visibility, revision FROM publications ORDER BY article_id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action LIKE 'content.%' ORDER BY id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT name, singleton_key FROM jobs WHERE name = 'events.digest' ORDER BY singleton_key"
    py_jobs = rows(admin["urls"]["py"], q)
    node_jobs = rows(admin["urls"]["node"], "SELECT name, singleton_key FROM pgboss.job WHERE name = 'events.digest' ORDER BY singleton_key")
    assert py_jobs == node_jobs


def test_rerun(admin, ids):
    a = ids[3]["id"]
    same(admin, "POST", f"/api/admin/content/{a}/rerun", 400, json_body={"step": "extract"})  # no request id
    same(admin, "POST", f"/api/admin/content/{a}/rerun", 400, json_body={"step": "nope"}, headers={"Idempotency-Key": "request-0001"})
    same(admin, "POST", f"/api/admin/content/{a}/rerun", 400, json_body={}, headers={"Idempotency-Key": "request-0001"})
    same(admin, "POST", "/api/admin/content/nope-nothing/rerun", 404, json_body={"step": "analyze"}, headers={"Idempotency-Key": "request-0002"})
    results = []
    for step, key in (("extract", "request-0003"), ("analyze", "request-0004"), ("analyze", "request-0004"), ("analyze", "request-0005")):
        pair = both(admin, "POST", f"/api/admin/content/{a}/rerun", json_body={"step": step}, headers={"Idempotency-Key": key})
        assert pair[0].status_code == pair[1].status_code == 200, (step, pair[0].text[:200], pair[1].text[:200])
        results.append((pair[0].json()["jobId"] is None, pair[1].json()["jobId"] is None))
    assert all(x == y for x, y in results)
    q = "SELECT identity_key, processing_state, processing_attempts, body_status, processing_queued_at IS NOT NULL AS queued, processing_attempt_tag FROM articles ORDER BY identity_key"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT actor, action, subject, reason, request_id, after - 'jobId' AS after FROM audit_log WHERE action LIKE 'content.rerun.%' ORDER BY id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)


def test_a_correction_moves_the_item_in_the_reports(admin, ids):
    import json

    from newsroom.engine.reports_correct import section_of, section_order
    from newsroom.engine.taxonomy import category_keys

    article = ids[4]["id"]
    category = rows(admin["urls"]["node"], f"SELECT category FROM publications WHERE article_id = '{article}'")[0]["category"]
    other = next(k for k in category_keys() if section_of(k) != section_of(category))
    first, second = section_order()[0], section_order()[1]
    daily = {"sections": [{"label": section_of(category), "items": [{"itemId": article, "role": "官方", "firstParty": True}, {"itemId": ids[5]["id"]}]}, {"label": first if first != section_of(category) else second, "items": []}], "metrics": {"modelsReleased": 0}}
    weekly = {"themes": [{"heading": section_of(category), "summary": "S", "storyRefs": [{"itemId": article}]}, {"heading": "Hand-written heading", "storyRefs": [{"itemId": ids[5]["id"]}]}]}
    for url in admin["urls"].values():
        with psycopg.connect(url, autocommit=True) as c:
            for kind, key, content in (("daily", "t-2026-11-01", daily), ("weekly", "t-2026-W44", weekly)):
                c.execute(
                    "INSERT INTO reports (kind, key, window_start, window_end, content, generated_at, revision) VALUES (%s, %s, now() - interval '1 day', now(), %s, now(), 1)",
                    (kind, key, json.dumps(content)),
                )
    version = admin["node"].request("GET", f"/api/admin/content/{article}").json()["override"]
    post(admin, f"/api/admin/content/{article}/override", {"fields": {"category": other}, "reason": "move it", "version": version["version"] if version else 0}, 200)
    q = "SELECT kind, key, content, revision FROM reports WHERE key LIKE 't-%' ORDER BY key"
    after = rows(admin["urls"]["node"], q)
    assert rows(admin["urls"]["py"], q) == after and all(r["revision"] == 2 for r in after)
    q = "SELECT revision, content, reason FROM report_revisions ORDER BY report_id, revision"
    revisions = rows(admin["urls"]["node"], q)
    assert rows(admin["urls"]["py"], q) == revisions and any("classification" in r["reason"] for r in revisions)
