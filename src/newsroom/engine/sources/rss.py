"""RSS 2.0 / Atom / RDF feeds. Port of sources/rss.ts.

A candidate is what a fetcher found on a listing, before identity and timeline rules are applied: a dict with the Material fields (snake_case,
`url`, `title`, `published_at`, `publication_time`, `excerpt`, `body_html`, `body_text`, `body_status`, `media`, `raw`, …) and `categories`."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import cast

from newsroom.engine.lib.http_fetch import language_headers, require_ok
from newsroom.engine.lib.ids import stable_json
from newsroom.engine.lib.text import collapse_whitespace, escape_xml, strip_tags
from newsroom.engine.lib.video_url import is_video_page_url
from newsroom.engine.materials import Material, identity_key_for
from newsroom.engine.sanitize import resolve_url
from newsroom.engine.sources import xmlparse
from newsroom.engine.sources.dates import js_parse, read_publication_date
from newsroom.engine.sources.listing_fetch import fetch_listing

STOP_PATHS = (("feed", "entry", "title"), ("feed", "entry", "summary"), ("feed", "entry", "content"))


class FetchError(Exception):
    pass


def reads_whole(source: dict) -> bool:
    """Whether a source's posts are wanted whole, not only as the feed gives them: the news judges an editorial source's, a module reads a used one's."""
    return source["participation_mode"] == "editorial" or len(source.get("uses") or []) > 0


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, list):
        return text(v[0]) if v else ""
    if isinstance(v, dict):
        if "#cdata" in v:
            return text(v["#cdata"])
        if "#text" in v:
            return text(v["#text"])
    return ""


def atom_plain_text(v) -> str | None:
    """Atom defaults to text: XML entities have already been decoded, and literal markup is not HTML."""
    typ = v.get("@type") if isinstance(v, dict) else None
    return text(v) if typ is None or typ == "text" else None


def arr(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _strict_url(href: str, base: str) -> str:
    out = resolve_url(href, base)
    if out == href and not re.match(r"^[a-z][a-z\d+.-]*:", href, re.I):
        raise ValueError(f"Invalid URL: {href}")
    return out


def media_description(entry: dict) -> str | None:
    """Media RSS descriptions describe the video; even a long description is not a transcript."""
    groups = arr(entry.get("media:group"))
    containers = [*[c for g in groups for c in arr(g.get("media:content"))], *arr(entry.get("media:content")), *groups, entry]
    for container in containers:
        if not isinstance(container, dict):
            continue
        description = container.get("media:description")
        value = text(description)
        if not value:
            continue
        shown = strip_tags(value) if isinstance(description, dict) and description.get("@type") == "html" else value
        return collapse_whitespace(shown)[:2000] or None
    return None


def parse_date(v: str) -> datetime | None:
    if not v:
        return None
    t = js_parse(v)
    if t is not None:
        return t
    cleaned = re.sub(r"\s+", " ", re.sub(r"星期[一二三四五六日天]", "", v)).strip()  # RFC 822 variants with Chinese weekday or odd zones
    return js_parse(cleaned)


def atom_link(links, base: str) -> str:
    items = arr(links)
    alt = next((link for link in items if isinstance(link, dict) and (not link.get("@rel") or link.get("@rel") == "alternate")), None)
    link = alt if alt is not None else (items[0] if items else None)
    href = link if isinstance(link, str) else (link or {}).get("@href")
    if not href:
        return ""
    link_base = _strict_url(link.get("@xml:base") or "", base) if isinstance(link, dict) and link.get("@xml:base") else base
    return _strict_url(href, link_base)


def images_from(html: str, base: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r'<img\b[^>]*\bsrc="([^"]+)"', html, re.I):
        try:
            out.append({"kind": "image", "url": _strict_url(m.group(1), base)})
        except ValueError:
            pass  # ignore bad urls
        if len(out) >= 6:
            break
    return out


# Feed text of a source read whole that only teases the article: short and ending in a "read more" mark. Treated as a summary, so extraction
# fetches the page before the article is judged.
TEASER_BELOW = 1200
TEASER_MARKS = [
    re.compile(r"\bappeared first on\b", re.I), re.compile(r"\bread (?:the )?full (?:story|article)\b", re.I), re.compile(r"\bcontinue reading\b", re.I),
    re.compile(r"\bread more\b", re.I), re.compile(r"…\s*$"), re.compile(r"\[\s*(?:…|\.\.\.)\s*\]\s*$"),
]  # fmt: skip


def is_teaser(t: str) -> bool:
    t = t.strip()
    return len(t) < TEASER_BELOW and any(m.search(t) for m in TEASER_MARKS)


def feed_text(body_html: str | None, summary_text: str | None, source: dict, has_page: bool = True, plain_body: str | None = None) -> dict:
    """The body and excerpt of a feed entry: its text when it is the article, else no body (a summary, or a teaser that stands in as the excerpt
    when the entry has none) until extraction reads the entry's page; an entry without a page has none."""
    body_text = collapse_whitespace(plain_body if plain_body is not None else strip_tags(body_html)) if body_html else None
    teaser = bool(body_text) and reads_whole(source) and is_teaser(cast(str, body_text))
    excerpt: str | None
    if summary_text is not None:
        excerpt = collapse_whitespace(summary_text)[:2000]
    else:
        excerpt = collapse_whitespace(cast(str, body_text)) if teaser else None
    if body_text and (len(body_text) > 280 or source["config"].get("summaryIsBody") is True) and not teaser:
        return {"excerpt": excerpt, "body_html": body_html, "body_text": body_text, "body_status": "ok"}
    return {"excerpt": excerpt, "body_html": None, "body_text": None, "body_status": "pending" if has_page else "none"}


def _validator(config_hash: str, response_url: str, etag, last_modified) -> dict:
    return {"configHash": config_hash, "responseUrl": response_url, "etag": etag, "lastModified": last_modified}


def fetch_rss(source: dict, force: bool = False) -> dict:
    """Reads a source's feed: {"candidates", "validator", "notModified"}."""
    from newsroom.engine.sanitize import sanitize_body  # noqa: PLC0415

    cfg = source["config"]
    url = str(cfg.get("feedUrl") or "")
    if not url:
        raise FetchError("feedUrl missing")
    # Config changes can alter parsing/filtering even when the upstream bytes did not change.
    config_hash = hashlib.sha256(stable_json(cfg).encode()).hexdigest()
    cursor = (source.get("cursor") or {}).get("rss")
    previous = cursor if not force and cursor and cursor.get("configHash") == config_hash else None
    plain = {"accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8", **language_headers(cfg.get("acceptLanguage"))}
    headers = dict(plain)
    if previous and previous.get("etag"):
        headers["if-none-match"] = previous["etag"]
    if previous and previous.get("lastModified"):
        headers["if-modified-since"] = previous["lastModified"]
    res = fetch_listing(url, headers=headers, timeout_ms=25_000)
    # A redirect may have changed destinations, whose ETag namespace is unrelated to the old one.
    if res.status == 304 and previous and res.url != previous.get("responseUrl"):
        res = fetch_listing(url, headers=plain, timeout_ms=25_000)
    etag = res.headers.get("etag") or (previous.get("etag") if res.status == 304 and previous else None)
    last_modified = res.headers.get("last-modified") or (previous.get("lastModified") if res.status == 304 and previous else None)
    validator = _validator(config_hash, res.url, etag, last_modified)
    if res.status == 304 and previous and (previous.get("etag") or previous.get("lastModified")) and res.url == previous.get("responseUrl"):
        return {"candidates": [], "validator": validator, "notModified": True}
    require_ok(res)
    try:
        doc = xmlparse.parse(res.text(), STOP_PATHS)
    except Exception as parse_error:
        raise FetchError(f"feed parse error: {str(parse_error)[:200]}") from None
    summary_is_body = cfg.get("summaryIsBody") is True
    offset = cfg.get("publishedAtUtcOffset") or "+08:00"
    out: list[dict] = []

    rss = doc.get("rss")
    channel = (rss or {}).get("channel") if isinstance(rss, dict) else None
    rdf = doc.get("rdf:RDF")
    if channel or rdf:
        items = arr((channel or {}).get("item") if channel else (rdf or {}).get("item") if isinstance(rdf, dict) else None)
        for it in items:
            if not isinstance(it, dict):
                continue
            guid = text(it.get("guid"))
            # A site-relative link resolves against the fetched document, as Atom links do; absolute ones stay as given.
            href = text(it.get("link"))
            if href and not re.match(r"^[a-z][a-z\d+.-]*:", href, re.I):
                try:
                    page = _strict_url(href, res.url)
                except ValueError:
                    page = None
            else:
                page = href
            page = page or (guid if _normalizable(guid) else "")
            enclosures = [e for e in arr(it.get("enclosure")) if isinstance(e, dict)]
            # An episode with no page (no <link>, a guid that is no address) links to its audio or video file, which a browser plays.
            media_file = ""
            if not page and guid:
                media_file = next((e.get("@url", "") for e in enclosures if re.match(r"^(audio|video)/", e.get("@type") or "")), "")
            link = page or media_file or guid
            title = collapse_whitespace(strip_tags(text(it.get("title"))))
            if not link or not title:
                continue
            content_encoded = text(it.get("content:encoded"))
            description = text(it.get("description"))
            video = is_video_page_url(link)
            video_excerpt = media_description(it) if video else None
            body_html_raw = content_encoded or (description if summary_is_body and not video else "")
            body_html = sanitize_body(body_html_raw, link) if body_html_raw else None
            enclosure = next((e for e in enclosures if re.match(r"^image/", e.get("@type") or "")), None)
            media = [
                *([{"kind": "image", "url": enclosure.get("@url")}] if enclosure else []),
                *(images_from(body_html_raw, link) if body_html_raw else []),
            ]
            date_text = text(it.get("pubDate")) or text(it.get("dc:date")) or text(it.get("published"))
            date_text = re.sub(r"星期[一二三四五六日天]", "", date_text, count=1)
            pub = read_publication_date(date_text, offset)
            cand = {
                "url": link, "title": title, "author": text(it.get("dc:creator")) or text(it.get("author")) or None,
                "published_at": pub.published_at, "publication_time": pub.publication_time,
                **feed_text(body_html, strip_tags(description) if description else None, source, not media_file and not video),
                "media": media[:6], "categories": [c for c in (text(c) for c in arr(it.get("category"))) if c], "raw": {"guid": guid or None},
            }  # fmt: skip
            if media_file:
                cand["identity_key"] = identity_key_for(Material(source_id=source["id"], url=guid, title=title, via="fetch"))
            if video_excerpt:
                cand["excerpt"] = video_excerpt
            out.append(cand)
        return {"candidates": out, "validator": validator, "notModified": False}

    feed = doc.get("feed")
    if isinstance(feed, dict):
        # XML Base is inherited; redirects determine the document's base, not the configured URL.
        feed_base = _strict_url(feed.get("@xml:base") or "", res.url) if feed.get("@xml:base") else res.url
        for e in arr(feed.get("entry")):
            if not isinstance(e, dict):
                continue
            entry_base = _strict_url(e.get("@xml:base") or "", feed_base) if e.get("@xml:base") else feed_base
            entry_url = atom_link(e.get("link"), entry_base)
            plain_title = atom_plain_text(e.get("title"))
            title = collapse_whitespace(plain_title if plain_title is not None else strip_tags(text(e.get("title"))))
            if not entry_url or not title:
                continue
            summary = (
                (atom_plain_text(e.get("summary")) if atom_plain_text(e.get("summary")) is not None else strip_tags(text(e.get("summary"))))
                if text(e.get("summary"))
                else None
            )
            video = is_video_page_url(entry_url)
            video_excerpt = media_description(e) if video else None
            body = e.get("content") if text(e.get("content")) else (e.get("summary") if summary_is_body and not video else None)
            plain_body = atom_plain_text(body)
            body_html_raw = text(body) if plain_body is None else escape_xml(plain_body)
            body_html = sanitize_body(body_html_raw, entry_url) if body_html_raw else None
            authors = arr(e.get("author"))
            name = text(authors[0].get("name")) if authors and isinstance(authors[0], dict) else ""
            pub = read_publication_date(text(e.get("published")), offset)
            cand = {
                "url": entry_url, "title": title, "author": name or None,
                # Atom updated describes a revision, not the original publication.
                "published_at": pub.published_at, "publication_time": pub.publication_time, "source_updated_at": parse_date(text(e.get("updated"))),
                **feed_text(body_html, summary, source, not video, plain_body),
                "media": images_from(body_html_raw, entry_url) if body_html_raw else [],
                "categories": [c for c in ((c.get("@term") if isinstance(c, dict) and c.get("@term") is not None else text(c)) for c in arr(e.get("category"))) if c],
                "raw": {"id": text(e.get("id")) or None},
            }  # fmt: skip
            if video_excerpt:
                cand["excerpt"] = video_excerpt
            out.append(cand)
        return {"candidates": out, "validator": validator, "notModified": False}
    raise FetchError("not an RSS/Atom document")


def _normalizable(url: str) -> bool:
    from newsroom.core.urls import normalize_url  # noqa: PLC0415

    return bool(url) and normalize_url(url) is not None
