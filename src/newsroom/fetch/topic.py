"""fetch (FET-FR-001, FET-FR-002): the text of every source of a topic as sources/<source_id>.md (IF-004), and the official source behind a
report (an official blog, a repository, a paper) added to the topic when the report links to it. Nothing is blocked while one source has
text; a topic with none is blocked and says how a person can paste it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from newsroom.collect.topics import OpenTopicError, refresh_status, source_id_of
from newsroom.core.http import Get
from newsroom.core.urls import normalize_url, site_identity_for_url
from newsroom.core.workspace import taipei_iso, workspace_dir, write_atomic
from newsroom.fetch.attempts import Attempt, record_attempts
from newsroom.fetch.extract import Extracted, ExtractFailure, extract_source, word_count

MAX_OFFICIAL = 3


@dataclass
class FetchResult:
    topic_id: str
    fetched: list[dict] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # sources that already had their text and were not read again
    failed: list[dict] = field(default_factory=list)
    blocked: bool = False  # the topic has no source text at all: a person has to paste it (E_MISSING_SOURCE)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def registrable(host: str) -> str:
    """Two labels from the right: "blogs.nvidia.com" and "nvidia.com" are one site."""
    return ".".join(host.removeprefix("www.").split(".")[-2:])


def official_sites(conn) -> set[str]:
    """The sites the engine reads as first-hand (its T1 and T1.5 feeds): a report linking to one of them points at the official source."""
    rows = conn.execute("SELECT config->>'feedUrl' AS feed FROM sources WHERE tier IN ('T1', 'T1_5') AND kind = 'rss'").fetchall()
    return {h for r in rows if r["feed"] for h in [registrable(_host(r["feed"]))] if h and h != "github.com"}


def official_links(links: list[str], report_url: str, sites: set[str]) -> list[str]:
    """Up to three addresses in a report that are official: a repository, a paper, or a page of an official site."""
    own = registrable(_host(report_url))
    if own in sites:
        return []
    found: dict[str, str] = {}
    for link in links:
        url = normalize_url(link)
        if not url or url == normalize_url(report_url):
            continue
        site = site_identity_for_url(url)
        host = registrable(_host(url))
        official = bool(site and (site.startswith("github:") or site.startswith("arxiv:"))) or (
            host in sites and host != own and len(urlsplit(url).path) > 1
        )
        # Identity from the normalized address; the page is read at the address the report gave (a site may need its "www").
        if official and source_id_of(url) not in found:
            found[source_id_of(url)] = link
        if len(found) >= MAX_OFFICIAL:
            break
    return list(found.values())


def _quote(value: str | None) -> str:
    return "null" if value is None else json.dumps(value, ensure_ascii=False)


def _write_source(directory: Path, source_id: str, url: str, x: Extracted, now: datetime) -> None:
    front = "\n".join(
        [
            "---",
            "schema_version: 1",
            f"source_id: {source_id}",
            f"url: {url}",
            f"canonical_url: {normalize_url(url) or url}",
            f"kind: {x.kind}",
            f"publisher: {_quote(x.publisher)}",
            f"author: {_quote(x.author)}",
            f"published_at: {x.published_at or 'null'}",
            f"fetched_at: {taipei_iso(now)}",
            f"fetch_method: {x.method}",
            f"language: {x.language}",
            f"word_count: {word_count(x.markdown)}",
            "---",
        ]
    )
    write_atomic(directory / f"{source_id}.md", f"{front}\n\n{x.markdown}\n")


def fetch_topic(conn, topic_id: str, get: Get | None = None, force: bool = False, now: datetime | None = None) -> FetchResult:
    now = now or datetime.now(timezone.utc)
    if not conn.execute("SELECT topic_id FROM topics WHERE topic_id = %s", (topic_id,)).fetchone():
        raise OpenTopicError(f"找不到 topic {topic_id}")
    directory = workspace_dir() / "topics" / topic_id / "sources"
    result = FetchResult(topic_id)
    sites = official_sites(conn)
    rows = conn.execute("SELECT source_id, url FROM topic_sources WHERE topic_id = %s ORDER BY source_id", (topic_id,)).fetchall()
    links: list[tuple[str, list[str]]] = []

    def read(source_id: str, url: str, official: bool) -> bool:
        if not force and (directory / f"{source_id}.md").exists():
            result.kept.append(source_id)
            return True
        try:
            x = extract_source(url, get)
            _write_source(directory, source_id, url, x, now)
            result.fetched.append({"sourceId": source_id, "url": url, "method": x.method, "wordCount": word_count(x.markdown), "official": official})
            if not official and x.kind == "article":
                links.append((url, x.links))
            return True
        except ExtractFailure as e:
            result.failed.append({"sourceId": source_id, "url": url, "reason": str(e)})
            return False

    for r in rows:
        if r["url"]:
            read(r["source_id"], r["url"], False)

    # FET-FR-002: the official source a report links to joins the topic.
    known = {r["source_id"] for r in rows}
    for report, link_list in links:
        for url in official_links(link_list, report, sites):
            sid = source_id_of(url)
            if sid in known:
                continue
            known.add(sid)
            if not read(sid, url, True):
                continue
            conn.execute(
                "INSERT INTO topic_sources (topic_id, source_id, url) VALUES (%s, %s, %s) ON CONFLICT (topic_id, source_id) DO NOTHING",
                (topic_id, sid, url),
            )

    record_attempts(conn, [*(Attempt(0, f["url"], True) for f in result.fetched), *(Attempt(0, f["url"], False, f["reason"]) for f in result.failed)])
    result.blocked = not (directory.exists() and any(p.suffix == ".md" for p in directory.iterdir()))
    if result.failed:
        notes_file = workspace_dir() / "topics" / topic_id / "notes.md"
        notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "# 備註\n"
        for f in result.failed:
            if f["url"] in notes:
                continue
            notes = f"{notes.rstrip()}\n\n- 原文擷取失敗：{f['url']}（{f['reason']}）。可以把原文貼進 sources/manual-1.md 後再執行 fetch。\n"
        write_atomic(notes_file, notes)
    refresh_status(conn, topic_id)
    return result
