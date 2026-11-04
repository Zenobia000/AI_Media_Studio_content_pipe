"""Heat signals as time series (COL-FR-005, 006, 008): Hacker News points and comments, GitHub stars, Hugging Face likes and downloads,
one value per metric per hour for an address, keyed like articles.identity_key so a signal belongs to the article it is about whichever
source brought that article. The heat grading reads the growth from them."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from newsroom.core.http import Get, default_get
from newsroom.core.urls import identity_key_for_url, site_identity_for_url

HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Point:
    identity_key: str
    metric: str
    value: float


class ExtractFailure(Exception):
    """Why nothing could be taken from an address."""


def hour_of(d: datetime) -> datetime:
    return d.replace(minute=0, second=0, microsecond=0)


def _json(get: Get, url: str, headers: dict[str, str] | None = None):
    page = get(url, {"accept": "application/json", **(headers or {})})
    if page.status != 200:
        raise ExtractFailure(f"HTTP {page.status}")
    return json.loads(page.text)


def hn_points(get: Get) -> list[Point]:
    """Front-page and recent AI stories: points and comments."""
    urls = [
        "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60",
        "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&numericFilters=points%3E20&hitsPerPage=60",
    ]
    seen: dict[str, dict] = {}
    for url in urls:
        for h in _json(get, url)["hits"]:
            if h.get("url") and isinstance(h.get("points"), (int, float)):
                seen[h["objectID"]] = {"url": h["url"], "points": h["points"], "comments": h.get("num_comments") or 0}
    out: list[Point] = []
    for h in seen.values():
        key = identity_key_for_url(h["url"])
        if key:
            out += [Point(key, "hn_points", h["points"]), Point(key, "hn_comments", h["comments"])]
    return out


def hf_points(get: Get) -> list[Point]:
    """Today's papers (upvotes) and the trending models (likes, downloads)."""
    papers = _json(get, "https://huggingface.co/api/daily_papers")
    models = _json(get, "https://huggingface.co/api/models?sort=trendingScore&limit=30")
    out = [
        Point("arxiv:" + re.sub(r"v\d+$", "", p["paper"]["id"]), "hf_upvotes", p["paper"]["upvotes"])
        for p in papers
        if isinstance(p.get("paper", {}).get("upvotes"), (int, float))
    ]
    for m in models:
        key = identity_key_for_url(f"https://huggingface.co/{m['id']}")
        if key:
            out += [Point(key, "hf_likes", m.get("likes") or 0), Point(key, "hf_downloads", m.get("downloads") or 0)]
    return out


def github_stars(conn, get: Get, token: str | None) -> list[Point]:
    """Stars of the repositories the release feeds watch."""
    feeds = conn.execute(
        "SELECT config->>'feedUrl' AS feed FROM sources WHERE enabled AND id LIKE 'gh-%' AND config->>'feedUrl' IS NOT NULL"
    ).fetchall()
    auth = {"authorization": f"Bearer {token}"} if token else {}
    out: list[Point] = []
    for row in feeds:
        repo = re.search(r"github\.com/([\w.-]+)/([\w.-]+)/releases\.atom", row["feed"])
        key = site_identity_for_url(f"https://github.com/{repo.group(1)}/{repo.group(2)}") if repo else None
        if not repo or not key:
            continue
        try:
            info = _json(get, f"https://api.github.com/repos/{repo.group(1)}/{repo.group(2)}", {"accept": "application/vnd.github+json", **auth})
            if isinstance(info.get("stargazers_count"), (int, float)):
                out.append(Point(key, "gh_stars", info["stargazers_count"]))
        except Exception:  # noqa: BLE001 — one repository that does not answer leaves the others
            continue
    return out


def store(conn, points: list[Point], at: datetime) -> None:
    for p in points:
        conn.execute(
            """INSERT INTO signal_points (identity_key, metric, value, observed_at) VALUES (%s, %s, %s, %s)
               ON CONFLICT (identity_key, metric, observed_at) DO UPDATE SET value = EXCLUDED.value""",
            (p.identity_key, p.metric, p.value, at),
        )


def collect_signals(conn, get: Get | None = None, now: datetime | None = None, github_token: str | None = None) -> dict[str, int | str]:
    """One snapshot of every signal source. A source that does not answer is reported and does not stop the others."""
    get = get or default_get
    at = hour_of(now or datetime.now(timezone.utc))
    report: dict[str, int | str] = {"hn": 0, "hf": 0, "github": 0}
    reads: list[tuple[str, Callable[[], list[Point]]]] = [
        ("hn", lambda: hn_points(get)),
        ("hf", lambda: hf_points(get)),
        ("github", lambda: github_stars(conn, get, github_token)),
    ]
    for name, read in reads:
        try:
            points = read()
            store(conn, points, at)
            report[name] = len(points)
        except Exception as e:  # noqa: BLE001
            report[name] = str(e)[:120]
    return report


_LABELS = [
    ("hn_points", "HN {} 分"),
    ("hn_comments", "{} 則留言"),
    ("gh_stars", "GitHub ★{}"),
    ("hf_upvotes", "HF {} 讚"),
    ("hf_likes", "HF {} 讚"),
    ("newsletter_mentions", "{} 份電子報提及"),
]


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def describe_signals(latest: dict[str, float]) -> str:
    """ "HN 412 分、120 則留言" for the latest values of an item, empty when there are none."""
    return "、".join(label.format(_fmt(latest[m])) for m, label in _LABELS if isinstance(latest.get(m), (int, float)))


_LATEST = "(SELECT DISTINCT ON (identity_key, metric) identity_key, metric, value FROM signal_points ORDER BY identity_key, metric, observed_at DESC)"


def latest_signals(conn, story_ids: list[int] | None = None, article_ids: list[str] | None = None):
    """The highest latest value of each metric among the articles of each story and of single articles."""
    by_story: dict[int, dict[str, float]] = {}
    by_article: dict[str, dict[str, float]] = {}
    if story_ids:
        for r in conn.execute(
            f"""SELECT ss.story_id, sp.metric, max(sp.value) AS value FROM story_signals ss JOIN articles a ON a.id = ss.article_id
                    JOIN {_LATEST} sp ON sp.identity_key = a.identity_key WHERE ss.story_id = ANY(%s) GROUP BY ss.story_id, sp.metric""",
            (story_ids,),
        ).fetchall():
            by_story.setdefault(r["story_id"], {})[r["metric"]] = r["value"]
    if article_ids:
        for r in conn.execute(
            f"""SELECT a.id, sp.metric, sp.value FROM articles a JOIN {_LATEST} sp ON sp.identity_key = a.identity_key WHERE a.id = ANY(%s)""",
            (article_ids,),
        ).fetchall():
            by_article.setdefault(r["id"], {})[r["metric"]] = r["value"]
    return by_story, by_article


def growth_per_hour(conn, identity_key: str, metric: str) -> float | None:
    """Growth per hour of a metric between its last two points, or None with fewer than two."""
    rows = conn.execute(
        "SELECT value, observed_at FROM signal_points WHERE identity_key = %s AND metric = %s ORDER BY observed_at DESC LIMIT 2",
        (identity_key, metric),
    ).fetchall()
    if len(rows) < 2:
        return None
    hours = (rows[0]["observed_at"] - rows[1]["observed_at"]) / HOUR
    return (rows[0]["value"] - rows[1]["value"]) / hours if hours > 0 else None
