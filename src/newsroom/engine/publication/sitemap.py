"""Sitemap from the same public metadata as pages: /sitemap.xml is an index of one file per section, so Search Console reports indexing section by
section. Port of the sitemap half of publication/sitemap.ts. Content identities (revisions, for change notifications) belong to the notifier and
are not computed here; the files carry only locations, dates, frequencies and priorities. One copy is kept five minutes in this process (like the
TypeScript, a copy is also saved on disk to bridge a failure after a restart)."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from newsroom.engine.config import config
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.text import escape_xml
from newsroom.engine.publication.items import ITEM_FROM
from newsroom.engine.publication.scope import evidence_condition, listed_condition, scope_params, story_report_condition
from newsroom.engine.publication.site_topics import topic_page_counts
from newsroom.engine.publication.stories import story_texts
from newsroom.engine.publication.v1 import iso

MAX_URLS = 45_000
TTL_MS = 5 * 60 * 1000
# A story of one report repeats that report's page, so the sitemap lists those that gather several.
MIN_SITEMAP_STORY_REPORTS = 2
INDEX = "sitemap.xml"

PAGES = [
    ("/", "hourly", "1"), ("/all", "hourly", "0.9"), ("/daily", "daily", "0.9"), ("/hot", "hourly", "0.9"), ("/daily/archive", "daily", "0.7"),
    ("/weekly", "weekly", "0.7"), ("/monthly", "monthly", "0.6"), ("/topics", "daily", "0.7"), ("/agent", "weekly", "0.7"),
    ("/about", "monthly", "0.5"), ("/terms", "monthly", "0.4"), ("/privacy", "monthly", "0.4"), ("/changelog", "weekly", "0.5"),
]  # fmt: skip


def discovery_entries(pool, at: datetime, urls: int) -> list[dict]:
    entries = [{"loc": loc, "changefreq": freq, "priority": prio} for loc, freq, prio in PAGES]
    with pool.connection() as conn:
        params = scope_params(at)
        reports = conn.execute(
            f"""SELECT r.kind, r.key,
                  CASE WHEN citations.revision IS NULL THEN greatest(r.updated_at, r.generated_at) END AS t
                FROM reports r LEFT JOIN LATERAL (
                  SELECT md5(string_agg(p.article_id || ':' || p.revision::text || ':' || ({listed_condition()})::text, ',' ORDER BY p.article_id)) AS revision
                  FROM publications p WHERE p.article_id IN (
                    SELECT value #>> '{{}}' FROM jsonb_path_query(r.content, '$.**.itemId') AS cited(value))
                ) citations ON true ORDER BY r.kind, r.key DESC""",
            params,
        ).fetchall()
        for r in reports:
            entries.append({"loc": f"/{r['kind']}/{r['key']}", "lastmod": r["t"], "changefreq": "monthly", "priority": "0.6"})
        for t in topic_page_counts(pool, at):
            if not t["indexable"]:
                continue
            entries.append({"loc": f"/topics/{t['slug']}", "changefreq": "daily", "priority": "0.6"})
            for p in range(2, t["pages"] + 1):
                entries.append({"loc": f"/topics/{t['slug']}/page/{p}", "changefreq": "weekly", "priority": "0.3"})
        # A story needs listed evidence of its own.
        stories = conn.execute(
            f"""SELECT st.id, st.public_id::text AS public_id
                FROM stories st JOIN LATERAL (
                  SELECT count(DISTINCT p.article_id) AS reports
                  FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                  JOIN sources s ON s.id = p.source_id
                  WHERE f.story_id = st.id AND {story_report_condition()}
                ) members ON true
                WHERE st.merged_into IS NULL AND EXISTS (
                  SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                  WHERE f.story_id = st.id AND {evidence_condition()} AND {listed_condition()})
                  AND members.reports >= %(min_reports)s
                ORDER BY st.latest_at DESC NULLS LAST, st.id DESC""",
            {**params, "min_reports": MIN_SITEMAP_STORY_REPORTS},
        ).fetchall()
        # Dated as the page's dateModified is: its latest public report, or a newer digest that still stands.
        texts = story_texts(conn, [int(s["id"]) for s in stories], at)
        for s in stories:
            text = texts.get(int(s["id"]), {})
            dates = [d for d in [(text.get("latest") or {}).get("at"), text.get("digestUpdatedAt")] if d]
            entries.append({"loc": f"/story/{s['public_id']}", "lastmod": max(dates) if dates else None, "changefreq": "daily", "priority": "0.5"})
        room = max(0, urls - len(entries))
        items = conn.execute(
            f"""SELECT p.article_id AS id {ITEM_FROM}
                WHERE {story_report_condition()} AND p.indexable
                ORDER BY p.timeline_at DESC, p.article_id DESC LIMIT %(room)s""",
            {**params, "room": room},
        ).fetchall()
    entries.extend({"loc": f"/items/{it['id']}", "changefreq": "monthly", "priority": "0.5"} for it in items)
    return entries[:urls]


def section_of(loc: str) -> str:
    import re  # noqa: PLC0415

    if loc.startswith("/story/"):
        return "stories"
    if loc.startswith("/items/"):
        return "items"
    if re.match(r"^/topics(/|$)", loc):
        return "topics"
    if re.match(r"^/(daily|weekly|monthly)(/|$)", loc):
        return "reports"
    return "pages"


def urlset(entries: list[dict]) -> str:
    blocks = []
    for e in entries:
        parts = [f"<loc>{escape_xml(site_url(e['loc']))}</loc>"]
        if e.get("lastmod"):
            parts.append(f"<lastmod>{iso(e['lastmod'])}</lastmod>")
        if e.get("changefreq"):
            parts.append(f"<changefreq>{e['changefreq']}</changefreq>")
        if e.get("priority") is not None:
            parts.append(f"<priority>{e['priority']}</priority>")
        blocks.append("<url>\n" + "\n".join(parts) + "\n</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(blocks) + "\n</urlset>\n"
    )


def build(pool, at: datetime) -> dict[str, str]:
    sections: dict[str, list[dict]] = {}
    for e in discovery_entries(pool, at, MAX_URLS):
        sections.setdefault(section_of(e["loc"]), []).append(e)
    files = {f"{name}.xml": urlset(entries) for name, entries in sections.items()}
    body = "\n".join(f"<sitemap>\n<loc>{escape_xml(site_url(f'/sitemaps/{file}'))}</loc>\n</sitemap>" for file in files)
    files[INDEX] = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</sitemapindex>\n'
    )
    return files


_lock = threading.Lock()
_last: dict[int, dict] = {}


def load_sitemap(pool) -> dict:
    """The files and when this copy expires (epoch ms); callers that overlap while it is rebuilt wait for the one rebuild."""
    with _lock:
        last = _last.get(id(pool))
        if last and last["expiresAt"] > time.time() * 1000:
            return last
        started = time.time() * 1000
        try:
            files = build(pool, datetime.now().astimezone())
        except Exception:
            if last and last["expiresAt"] > time.time() * 1000:
                return last
            # A copy saved by an earlier process bridges a failure, for the rest of its own lifetime.
            saved = _read_saved()
            if saved:
                _last[id(pool)] = saved
                return saved
            raise
        _last[id(pool)] = {"files": files, "expiresAt": started + TTL_MS}
        _save(files, started)
        return _last[id(pool)]


def _cache_file() -> Path:
    return config().data_dir / "sitemap-last.json"


def _save(files: dict[str, str], started_ms: float) -> None:
    """The file's timestamp preserves the same deadline when another process loads it after a restart."""
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(files, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.utime(path, (started_ms / 1000, started_ms / 1000))
    except OSError:
        pass


def _read_saved() -> dict | None:
    try:
        path = _cache_file()
        mtime = path.stat().st_mtime * 1000
        if mtime + TTL_MS > time.time() * 1000:
            return {"files": json.loads(path.read_text(encoding="utf-8")), "expiresAt": mtime + TTL_MS}
    except (OSError, ValueError):
        pass
    return None
