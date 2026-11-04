"""Source icons ("來源圖示快取"): the face a source shows next to its reports and on the hot list. X accounts take the avatar on their latest post; 公眾號 the account avatar
on their latest article page; sites the best icon their home page declares. A source with none keeps its tinted initial. Port of sources/icons.ts."""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from newsroom.engine.dom import new_url_href
from newsroom.engine.lib.http_fetch import default_ua, guarded_fetch
from newsroom.engine.media.images import produce_image
from newsroom.engine.sources.account import X_HANDLE

BATCH = 200
RETRY_DAYS = 30  # a site without an icon rarely grows one
MP_RETRY_DAYS = 3  # 公眾號 misses are mostly WeChat turning the server away for a while
MP_PAUSE_SECONDS = 3.0


def refresh_x_avatars(pool) -> int:
    """X accounts: the avatar on the account's own latest post, kept current as avatars change."""
    with pool.connection() as conn:
        rows = conn.execute(
            """UPDATE sources s SET icon_url = x.avatar
               FROM (
                 SELECT DISTINCT ON (a.source_id) a.source_id, a.x_post->>'avatarUrl' AS avatar
                 FROM articles a JOIN sources xs ON xs.id = a.source_id AND xs.kind = 'x_search'
                 WHERE a.x_post ? 'avatarUrl'
                   AND lower((regexp_match(xs.config->>'query', %s, 'i'))[1]) = lower(nullif(a.x_post->>'handle', ''))
                 ORDER BY a.source_id, a.discovered_at DESC
               ) x
               WHERE s.id = x.source_id AND s.icon_url IS DISTINCT FROM x.avatar
               RETURNING s.id""",
            (X_HANDLE.pattern,),
        ).fetchall()
    return len(rows)


def page(url: str, max_bytes: int = 4_000_000) -> dict | None:
    """A page's HTML; 公眾號 article pages run to several megabytes, home pages rarely past four."""
    try:
        res = guarded_fetch(url, timeout_ms=15_000, max_bytes=max_bytes, headers={"user-agent": default_ua(), "accept": "text/html,*/*;q=0.8"})
        return {"html": res.text(), "url": res.url} if res.status == 200 else None
    except Exception:
        return None


_LINK = re.compile(r"<link\b[^>]*>", re.I)
_REL = re.compile(r"\brel\s*=\s*[\"']?([^\"'>]+)", re.I)
_HREF_QUOTED = re.compile(r"\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_HREF_BARE = re.compile(r"\bhref\s*=\s*([^\s>]+)", re.I)
_SIZES = re.compile(r"\bsizes\s*=\s*[\"']?([^\"'>]+)", re.I)
_SIZE = re.compile(r"(\d+)x\d+")
_SVG = re.compile(r"\.svg(\?|$)", re.I)
_ICO = re.compile(r"\.ico(\?|$)", re.I)


def icon_candidates(html: str, base: str) -> list[str]:
    """Icons a page declares, best first: touch icons (large PNGs), then sized icons, then the rest, then /favicon.ico."""
    found: list[tuple[str, int]] = []
    for tag in _LINK.findall(html):
        m = _REL.search(tag)
        rel = (m.group(1) if m else "").lower()
        if "icon" not in rel:
            continue
        hm = _HREF_QUOTED.search(tag) or _HREF_BARE.search(tag)
        href = hm.group(1) if hm else None
        if not href or href.startswith("data:"):
            continue
        sm = _SIZES.search(tag)
        size = max([0, *[int(x) for x in _SIZE.findall(sm.group(1) if sm else "")]])
        score = (1000 if "apple-touch-icon" in rel else 0) + min(size, 512) + (200 if _SVG.search(href) else 0) - (300 if _ICO.search(href) else 0)
        try:
            found.append((new_url_href(href, base), score))
        except ValueError:
            pass  # unusable href
    ordered = [h for h, _ in sorted(found, key=lambda f: -f[1])]
    return list(dict.fromkeys([*ordered, new_url_href("/favicon.ico", base)]))


def first_usable(candidates: list[str]) -> str | None:
    """The first candidate the image proxy can turn into an avatar (which also warms its cache)."""
    for url in candidates[:5]:
        try:
            produce_image(url, "avatar")
            return url
        except Exception:
            continue
    return None


def home_of(article_urls: list[str], config: dict) -> str | None:
    """The site behind a source: the host most of its recent reports live on, else its configured address."""
    origins = []
    for u in article_urls:
        p = urlsplit(u)
        if p.scheme and p.netloc:
            origins.append(f"{p.scheme}://{p.netloc}")
    counts: dict[str, int] = {}
    for o in origins:
        counts[o] = counts.get(o, 0) + 1
    top, n = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))
    if top and n >= max(1, len(origins) * 0.7):
        return top
    configured = config.get("url") if config.get("url") is not None else config.get("feedUrl") if config.get("feedUrl") is not None else ""
    p = urlsplit(re.sub(r"^https?://r\.jina\.ai/", "", str(configured), flags=re.I))
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else None


def site_icon(home: str) -> str | None:
    """The best icon a site's home page declares that the image proxy can show (warming its cache), else None."""
    p = page(home)
    return first_usable(icon_candidates(p["html"], p["url"]) if p else [new_url_href("/favicon.ico", home)])


def find_icon(kind: str, article_urls: list[str], config: dict) -> str | None:
    if kind == "mp_account":
        # WeChat answers bursts with "未知錯誤": take the account's two latest articles, slowly.
        for url in article_urls[:2]:
            time.sleep(MP_PAUSE_SECONDS)
            p = page(url, 10_000_000)
            m = re.search(r"round_head_img\s*[:=]\s*[\"']([^\"']+)[\"']", p["html"]) if p else None
            if m:
                return first_usable([re.sub(r"^http:", "https:", m.group(1))])
        return None
    home = home_of(article_urls, config)
    return site_icon(home) if home else None


def find_missing_icons(pool) -> dict:
    """Sites and 公眾號 without an icon: a batch per run, looking again after RETRY_DAYS (公眾號: MP_RETRY_DAYS)."""
    with pool.connection() as conn:
        due = conn.execute(
            """SELECT s.id, s.kind, s.config, a.urls
               FROM sources s
               LEFT JOIN LATERAL (SELECT array_agg(url) AS urls FROM (SELECT url FROM articles WHERE source_id = s.id ORDER BY discovered_at DESC LIMIT 10) r) a ON true
               WHERE s.icon_url IS NULL AND s.kind <> 'x_search'
                 AND (s.icon_checked_at IS NULL OR s.icon_checked_at < now() - make_interval(days => CASE WHEN s.kind = 'mp_account' THEN %s::int ELSE %s::int END))
               ORDER BY s.icon_checked_at NULLS FIRST, s.id
               LIMIT %s""",
            (MP_RETRY_DAYS, RETRY_DAYS, BATCH),
        ).fetchall()
    found = 0
    for s in due:
        try:
            icon = find_icon(s["kind"], s["urls"] or [], s["config"] or {})
        except Exception:
            icon = None
        if icon:
            found += 1
        with pool.connection() as conn:
            conn.execute("UPDATE sources SET icon_url = coalesce(%s, icon_url), icon_checked_at = now() WHERE id = %s", (icon, s["id"]))
    return {"checked": len(due), "found": found}


def refresh_source_icons(pool) -> dict:
    x = refresh_x_avatars(pool)
    return {"xAvatars": x, **find_missing_icons(pool)}
