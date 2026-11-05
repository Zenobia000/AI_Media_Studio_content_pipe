"""IndexNow for added, changed and removed discovery URLs, and the complete inventory it works from (publication/sitemap.ts loadDiscoveryEntries without limits). The saved
identities describe only accepted notifications, so a failed request, a dry run or a batch boundary cannot lose changes. Port of operations/indexnow.ts.

One difference in value, not in meaning: a topic page's content identity is hashed from this engine's own seat records, so its fingerprint differs from the TypeScript
engine's for the same page; switching engines therefore resubmits the topic pages once."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from psycopg.types.json import Jsonb

from newsroom.engine import pack
from newsroom.engine.config import REPO_ROOT, config
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.ids import sha256, stable_json
from newsroom.engine.publication import site_topics
from newsroom.engine.publication.items import ITEM_FROM
from newsroom.engine.publication.scope import evidence_condition, listed_condition, scope_params, selected_condition, story_report_condition
from newsroom.engine.publication.site_topics import company_brands, topic_index
from newsroom.engine.publication.sitemap import PAGES
from newsroom.engine.publication.stories import latest_hot_ranking
from newsroom.engine.publication.v1 import iso
from newsroom.engine.site import load_changelog

MAX_URLS = 10_000  # IndexNow acknowledges at most 10,000 changed URLs per request


def _plain(value):
    """What JSON.stringify would write: instants as ISO strings, numbers as numbers."""
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def load_discovery_entries(pool, at: datetime) -> list[dict]:
    """One inventory for change notifications: the complete one, with a content identity (`revision`) for each address."""
    params = scope_params(at)
    site = pack.site()
    entries: list[dict] = []
    with pool.connection() as conn:
        activity = conn.execute(
            f"""SELECT md5(string_agg(p.article_id || ':' || p.revision::text, ',' ORDER BY p.timeline_at DESC, p.article_id) FILTER (WHERE {selected_condition()})) AS selected,
                       md5(string_agg(p.article_id || ':' || p.revision::text, ',' ORDER BY p.timeline_at DESC, p.article_id) FILTER (WHERE {listed_condition()})) AS listed
                FROM publications p WHERE {listed_condition()}""",
            params,
        ).fetchone()
        reports = conn.execute(
            f"""SELECT r.kind, r.key, CASE WHEN citations.revision IS NULL THEN greatest(r.updated_at, r.generated_at) END AS t,
                  md5(concat_ws('|', r.revision::text, r.updated_at::text, citations.revision)) AS revision
                FROM reports r LEFT JOIN LATERAL (
                  SELECT md5(string_agg(p.article_id || ':' || p.revision::text || ':' || ({listed_condition()})::text, ',' ORDER BY p.article_id)) AS revision
                  FROM publications p WHERE p.article_id IN (SELECT value #>> '{{}}' FROM jsonb_path_query(r.content, '$.**.itemId') AS cited(value))
                ) citations ON true ORDER BY r.kind, r.key DESC""",
            params,
        ).fetchall()
        hot = latest_hot_ranking(conn)
    reports_json = [{"kind": r["kind"], "key": r["key"], "t": iso(r["t"]) if r["t"] else None, "revision": r["revision"]} for r in reports]

    def report_revision(kind: str) -> str:
        return sha256(stable_json([r for r in reports_json if r["kind"] == kind]))

    index = topic_index(pool, at)
    brands = company_brands()
    topics = []
    for t in site_topics.topics():
        seats = index["bySlug"][t["slug"]]
        topics.append({
            "slug": t["slug"], "pages": max(1, math.ceil(len(seats) / site_topics.TOPIC_PAGE_SIZE)),
            "indexable": site_topics.is_indexable(len(seats), site_topics._recent_count(seats, index["at"])),
            "revision": sha256(stable_json(_plain([t, seats, [], brands.get(t["slug"])]))),
        })  # fmt: skip
    pages = {
        "/": activity["selected"] or "", "/all": activity["listed"] or "", "/daily": report_revision("daily"), "/hot": sha256(stable_json(_plain((hot or {}).get("entries") or []))),
        "/daily/archive": report_revision("daily"), "/weekly": report_revision("weekly"), "/monthly": report_revision("monthly"),
        "/topics": sha256(stable_json([{"slug": t["slug"], "pages": t["pages"], "indexable": t["indexable"], "revision": t["revision"]} for t in topics])),
        "/agent": sha256(stable_json([site["SITE"], site["ACCESS"], site["AGENT"]])), "/about": sha256(stable_json(site["ABOUT"])),
        "/terms": sha256(stable_json([site["POLICY"]["terms"], (REPO_ROOT / "site" / "pages" / "terms.md").read_text(encoding="utf-8")])),
        "/privacy": sha256(stable_json([site["POLICY"]["privacy"], (REPO_ROOT / "site" / "pages" / "privacy.md").read_text(encoding="utf-8")])),
        "/changelog": sha256(stable_json(load_changelog())),
    }  # fmt: skip
    for loc, _freq, _prio in PAGES:
        entries.append({"loc": loc, "revision": pages[loc]})
    for r in reports:
        entries.append({"loc": f"/{r['kind']}/{r['key']}", "lastmod": r["t"], "revision": r["revision"]})
    for t in topics:
        if not t["indexable"]:
            continue
        entries.append({"loc": f"/topics/{t['slug']}", "revision": t["revision"]})
        for p in range(2, t["pages"] + 1):
            entries.append({"loc": f"/topics/{t['slug']}/page/{p}", "revision": t["revision"]})
    # A story needs listed evidence of its own. Its identity follows current readable reports as well as the digest, so a correction or regrouping is visible even when
    # latest_at does not advance.
    with pool.connection() as conn:
        stories = conn.execute(
            f"""SELECT st.id, st.public_id::text AS public_id, md5(concat_ws('|', st.title, st.summary, st.digest, st.digest_updated_at::text, st.version::text, members.revision)) AS revision
                FROM stories st JOIN LATERAL (
                  SELECT md5(string_agg(concat_ws(':', p.article_id, p.revision::text, fa.role, f.updated_at::text, s.name), ',' ORDER BY p.article_id, f.id)) AS revision,
                         count(DISTINCT p.article_id) AS reports
                  FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id JOIN sources s ON s.id = p.source_id
                  WHERE f.story_id = st.id AND {story_report_condition()}
                ) members ON true
                WHERE st.merged_into IS NULL AND EXISTS (
                  SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                  WHERE f.story_id = st.id AND {evidence_condition()} AND {listed_condition()})
                ORDER BY st.latest_at DESC NULLS LAST, st.id DESC""",
            params,
        ).fetchall()
        items = conn.execute(
            f"""SELECT p.article_id AS id,
                  md5(concat_ws('|', p.revision::text, a.content_hash, tr.title, tr.body_html, tr.body_text, tr.complete::text, qt.text_zh, s.name, s.icon_url, st.public_id::text, st.title)) AS revision
                {ITEM_FROM}
                WHERE {story_report_condition()} AND p.indexable
                ORDER BY p.timeline_at DESC, p.article_id DESC""",
            params,
        ).fetchall()
    # Dated by the content's identity, not an invented modification date: without a dedicated public modification clock, omitting lastmod is more accurate than guessing.
    for s in stories:
        entries.append({"loc": f"/story/{s['public_id']}", "lastmod": None, "revision": s["revision"]})
    for it in items:
        entries.append({"loc": f"/items/{it['id']}", "revision": it["revision"]})
    return entries


def submit_index_now(pool, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        state = conn.execute("SELECT value FROM settings WHERE key = 'indexnow.watermark'").fetchone()
    # A time-only watermark cannot prove what was submitted. The first inventory establishes that baseline in bounded batches; no dual tracking or guessed historical
    # state is kept.
    acknowledged = (state["value"] if state else {}).get("fingerprints") or {}
    current = {
        e["loc"]: sha256(stable_json([e.get("revision"), iso(e["lastmod"]) if e.get("lastmod") else None])) for e in load_discovery_entries(pool, now)
    }
    # Disappearing from discovery calls for another fetch (withdrawal, redirect or a changed indexing decision). It does not claim that the URL was deleted, or that the
    # engine removed it from results.
    changes = sorted(loc for loc in {*acknowledged, *current} if acknowledged.get(loc) != current.get(loc))
    sent = changes[:MAX_URLS]
    urls = [site_url(loc) for loc in sent]
    status = "disabled" if urls else "empty"
    http_status = None
    cfg = config()
    key = cfg.index_now_key
    if urls and cfg.index_now_submit_enabled and key:
        from urllib.parse import urlsplit

        res = httpx.post(
            os.environ.get("INDEXNOW_ENDPOINT") or "https://api.indexnow.org/indexnow", timeout=30, headers={"content-type": "application/json; charset=utf-8"},
            content=json.dumps({"host": urlsplit(cfg.site_url).netloc, "key": key, "keyLocation": site_url(f"/{key}.txt"), "urlList": urls}, ensure_ascii=False, separators=(",", ":")).encode(),
        )  # fmt: skip
        http_status = res.status_code
        status = "sent" if res.is_success else "failed"
    if status == "sent":
        fingerprints = dict(acknowledged)
        for loc in sent:
            if loc not in current:
                fingerprints.pop(loc, None)
            else:
                fingerprints[loc] = current[loc]
        with pool.connection() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_by) VALUES ('indexnow.watermark', %s, 'worker')
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                (Jsonb({"fingerprints": fingerprints, "acceptedAt": iso(now)}),),
            )
    return {"status": status, "httpStatus": http_status, "urls": len(urls), "more": len(changes) > len(sent), "sample": urls[:3]}
