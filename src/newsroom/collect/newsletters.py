"""Newsletters (COL-FR-019): an issue holds many stories, so each link in it becomes an article of its own. A link that is already an article
(an official blog the feed brought) stays one article; what is added is that a newsletter mentioned it, kept as a heat signal
("被 2 份電子報提及"). Like add-source, the articles are stored without being queued for AIHOT's model analysis (see collect/materials.py)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from newsroom.collect.materials import upsert_manual_material
from newsroom.collect.signals import Point, hour_of, store
from newsroom.core.http import Get
from newsroom.core.urls import identity_key_for_url
from newsroom.fetch.extract import ExtractFailure, extract_source

LINKS_SOURCE = "newsletter-links"
MAX_LINKS = 30
ISSUE_AGE_DAYS = 3
MAX_ISSUES = 10
MIN_TITLE = 8
# Where a newsletter links that is no story: social profiles, sign-up pages, its own pages.
SKIP_HOSTS = re.compile(r"(^|\.)(twitter\.com|x\.com|linkedin\.com|facebook\.com|instagram\.com|t\.me|discord\.gg)$", re.I)
SKIP_TEXT = re.compile(r"sponsor|advertis|unsubscribe|subscribe|sign up|preferences|view in browser|careers|^read more$|^learn more$", re.I)
SKIP_PATH = re.compile(r"unsubscribe|preferences|subscribe|advertise|sponsor|careers|jobs", re.I)
# Pictures and clips are not stories; neither is a link whose text names no story ("image", "Hugging Face", a bare address).
SKIP_MEDIA_HOST = re.compile(r"(^|\.)(i\.redd\.it|v\.redd\.it|preview\.redd\.it|imgur\.com)$", re.I)
SKIP_FILE = re.compile(r"\.(png|jpe?g|gif|webp|svg|mp4|webm|mov|pdf)$", re.I)
GENERIC_TEXT = re.compile(
    r"^(image|images|video|link|links|here|source|paper|github|website|site|demo|code|blog|post|thread|tweet|hugging face|reddit|twitter|x)$", re.I
)
BARE_ADDRESS = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", re.I)
_LINK = re.compile(r"(?<!!)\[([^\]]{3,300})\]\((https?:[^)\s]+)\)")


@dataclass
class MentionedLink:
    url: str
    title: str


def story_links(markdown: str, issue_url: str) -> list[MentionedLink]:
    """The story links of an issue's Markdown: the sponsors, the social links and the newsletter's own pages left out."""
    issue_host = re.sub(r"^www\.", "", urlsplit(issue_url).hostname or "")
    found: dict[str, MentionedLink] = {}
    for m in _LINK.finditer(markdown):
        title = re.sub(r"^[*_`\s]+|[*_`\s]+$", "", re.sub(r"\s+", " ", m.group(1)))
        if len(title) < MIN_TITLE or GENERIC_TEXT.match(title) or BARE_ADDRESS.match(title):
            continue
        try:
            url = urlsplit(m.group(2))
            host = re.sub(r"^www\.", "", url.hostname or "")
        except ValueError:
            continue
        if (
            not host
            or SKIP_HOSTS.search(host)
            or SKIP_MEDIA_HOST.search(host)
            or SKIP_FILE.search(url.path)
            or host == issue_host
            or host.endswith(f".{issue_host}")
            or SKIP_TEXT.search(title)
            or SKIP_PATH.search(url.path)
        ):
            continue
        if len(url.path) < 2 and not url.query:  # a site's front page is no story
            continue
        key = identity_key_for_url(m.group(2))
        if key and key not in found:
            found[key] = MentionedLink(m.group(2), title[:300])
        if len(found) >= MAX_LINKS:
            break
    return list(found.values())


@dataclass
class SplitResult:
    issues: int = 0
    links: int = 0
    created: int = 0  # articles that were not there before
    failed: list[dict] = field(default_factory=list)


def split_newsletters(conn, get: Get | None = None, now: datetime | None = None) -> SplitResult:
    """Splits the issues of newsletter sources that have not been split yet."""
    now = now or datetime.now(timezone.utc)
    result = SplitResult()
    issues = conn.execute(
        """SELECT a.id, a.url FROM articles a JOIN sources s ON s.id = a.source_id
           WHERE '電子報' = ANY(s.tags) AND s.id <> %s AND a.discovered_at > %s
             AND NOT EXISTS (SELECT 1 FROM newsletter_issues i WHERE i.article_id = a.id)
           ORDER BY a.discovered_at DESC LIMIT %s""",
        (LINKS_SOURCE, now - timedelta(days=ISSUE_AGE_DAYS), MAX_ISSUES),
    ).fetchall()
    if not issues:
        return result
    conn.execute(
        """INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health, tags)
           VALUES (%s, '電子報提及', 'external', '{}'::jsonb, 'T2', 'editorial', 1440, true, 'ok', %s) ON CONFLICT (id) DO NOTHING""",
        (LINKS_SOURCE, ["電子報"]),
    )
    for issue in issues:
        try:
            page = extract_source(issue["url"], get)
            links = story_links(page.markdown, issue["url"])
        except ExtractFailure as e:
            result.failed.append({"url": issue["url"], "reason": str(e)})  # tried again on the next run, until the issue is old
            continue
        for link in links:
            if upsert_manual_material(conn, LINKS_SOURCE, link.url, link.title, via="newsletter").created:
                result.created += 1
            conn.execute(
                "INSERT INTO newsletter_mentions (issue_article_id, identity_key) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (issue["id"], identity_key_for_url(link.url)),
            )
        conn.execute(
            "INSERT INTO newsletter_issues (article_id, links) VALUES (%s, %s) ON CONFLICT (article_id) DO NOTHING", (issue["id"], len(links))
        )
        result.issues += 1
        result.links += len(links)
    record_mentions(conn, now)
    return result


def record_mentions(conn, now: datetime) -> int:
    """How many issues mention each address, as a heat signal for this hour."""
    rows = conn.execute("SELECT identity_key, count(*)::int AS n FROM newsletter_mentions GROUP BY identity_key").fetchall()
    store(conn, [Point(r["identity_key"], "newsletter_mentions", r["n"]) for r in rows], hour_of(now))
    return len(rows)
