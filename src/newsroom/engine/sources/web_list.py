"""Web list pages: HTML with selectors, Markdown through Jina Reader, and dated changelog sections. Port of sources/web-list.ts (the listing reads;
the detail pages' reading waits for content/extract.ts)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, quote, urlsplit

import soupsieve
from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, ProcessingInstruction, Tag
from bs4.element import CData, Declaration

from newsroom.core.urls import _form_encode, identity_key_for_url, normalize_url
from newsroom.engine.extract import is_sign_in_url
from newsroom.engine.lib.http_fetch import guarded_fetch, language_headers, require_ok
from newsroom.engine.lib.page_content import content_document
from newsroom.engine.lib.text import collapse_whitespace, strip_tags
from newsroom.engine.sanitize import resolve_url, sanitize_body
from newsroom.engine.sources.dates import PublicationDate, read_publication_date, read_relative_date
from newsroom.engine.sources.filters import allowed
from newsroom.engine.sources.json_list import JINA_PREFIX
from newsroom.engine.sources.listing_fetch import fetch_listing
from newsroom.engine.sources.rss import FetchError

NO_DATE = PublicationDate(None, None)
_VOID = {"area", "base", "basefont", "bgsound", "br", "col", "embed", "frame", "hr", "img", "input", "keygen", "link", "meta", "param", "source", "track", "wbr"}
_RAW = {"style", "script", "xmp", "iframe", "noembed", "noframes", "plaintext"}


# --- the parsed page, with the reading cheerio gives it ----------------------------------------------------------------------------------------


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html5lib", multi_valued_attributes=None)


def text_of(el: Tag | None) -> str:
    """cheerio's .text(): every text node below, script and style text included, comments not."""
    if el is None:
        return ""
    skip = (Comment, Doctype, ProcessingInstruction, Declaration, CData)
    return "".join(str(s) for s in el.descendants if isinstance(s, NavigableString) and not isinstance(s, skip))


def attr_of(el: Tag | None, name: str) -> str | None:
    value = el.get(name) if el is not None else None
    return None if value is None else str(value)


def matches(el: Tag | None, selector: str) -> bool:
    return el is not None and soupsieve.match(selector, el)


def first_within(el: Tag | None, selector: str) -> Tag | None:
    return el.select_one(selector) if el is not None else None


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("\u00a0", "&nbsp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace("\u00a0", "&nbsp;").replace('"', "&quot;")


def outer_html(node) -> str:
    """The serialisation of a node as the HTML standard writes it (what cheerio's $.html(el) gives)."""
    if isinstance(node, Comment):
        return f"<!--{node}-->"
    if isinstance(node, (Doctype, Declaration, ProcessingInstruction, CData)):
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    attrs = "".join(f' {k}="{_escape_attr(" ".join(v) if isinstance(v, list) else str(v))}"' for k, v in node.attrs.items())
    if node.name in _VOID:
        return f"<{node.name}{attrs}>"
    inner = "".join(
        str(c) if isinstance(c, NavigableString) and not isinstance(c, (Comment,)) and node.name in _RAW else _inner(c) for c in node.children
    )
    return f"<{node.name}{attrs}>{inner}</{node.name}>"


def _inner(node) -> str:
    if isinstance(node, NavigableString) and not isinstance(node, (Comment, Doctype, Declaration, ProcessingInstruction, CData)):
        return _escape_text(str(node))
    return outer_html(node)


def _offset(value) -> str:
    return "+08:00" if value is None else value


def element_date(el: Tag | None, utc_offset: str | None = None) -> PublicationDate:
    """A date rule names one element; unrelated tooltips must not hide its visible publication date."""
    off = _offset(utc_offset)
    for value in (attr_of(el, "datetime"), attr_of(el, "content"), attr_of(el, "title"), text_of(el)):
        date = read_publication_date(value, off)
        if date.published_at:
            return date
    return read_relative_date(text_of(el), datetime.now(UTC), off)


# --- addresses -----------------------------------------------------------------------------------------------------------------------------------

LISTING_PARAMS = re.compile(r"^(page|paged|cat|category|categories|tag|tags|label|labels|author|authors)$", re.I)


def listing_itself(url: str, listing: str) -> bool:
    """A link back to the listing page itself (skip links, in-page anchors such as #paper, #blog, ?page=2)."""

    def bare(s: str) -> str:
        x = urlsplit(resolve_url(s))
        if not x.scheme or not x.netloc:
            raise ValueError(f"Invalid URL: {s}")
        pairs = parse_qsl(urlsplit(normalize_url(s) or resolve_url(s)).query, keep_blank_values=True)
        query = _form_encode([(k, v) for k, v in pairs if not LISTING_PARAMS.match(k)])
        return f"{x.netloc}{re.sub(r'/$', '', x.path)}?{query}"

    return bare(url) == bare(listing)


def navigation_link(url: str, listing: str) -> bool:
    """Navigation a listing links to but that is no post: the listing itself, sign-in and registration pages, year archives, and taxonomy, author and
    pagination pages."""
    if listing_itself(url, listing) or is_sign_in_url(url):
        return True
    path = urlsplit(resolve_url(url)).path
    return bool(re.search(r"/(label|labels|tag|tags|category|categories|author|authors|page)(/|$)", path, re.I) or re.search(r"/(19|20)\d{2}(/\d{1,2})?/?$", path))


def absolute(href: str | None, base: str) -> str | None:
    """Absolute http(s) URL; a link to the listing's own https site keeps https."""
    if not href:
        return None
    full = resolve_url(href, base)
    u = urlsplit(full)
    if u.scheme not in ("http", "https") or not u.netloc or not re.match(r"^https?://", full):
        return None
    b = urlsplit(resolve_url(base))
    if u.scheme == "http" and b.scheme == "https" and u.netloc == b.netloc:
        return "https" + full[4:]
    return full


def is_call_to_action_title(title: str) -> bool:
    """A listing title that is no headline: a label that swallowed its summary, or a call to action."""
    return re.fullmatch(r"(read more|read the blog|learn more|continue reading|more|閱讀全文|閱讀更多|檢視詳情|瞭解更多)", title.strip(), re.I) is not None


def needs_title(title: str) -> bool:
    return len(title) > 100 or is_call_to_action_title(title)


# --- reading a listing ---------------------------------------------------------------------------------------------------------------------------


def fetch_listing_text(source: dict) -> tuple[str, bool, str]:
    cfg = source["config"]
    url = "" if cfg.get("url") is None else str(cfg["url"])
    if not url:
        raise FetchError("url missing")
    if url.startswith(JINA_PREFIX):
        raise NotImplementedError("reading a listing through Jina needs the paid-request receipts, which are not ported yet")
    headers = {"accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", **language_headers(cfg.get("acceptLanguage"))}
    res = require_ok(fetch_listing(url, headers=headers, timeout_ms=25_000))
    text = res.text()
    content_document(text)
    return text, False, cfg["baseUrl"] if cfg.get("baseUrl") is not None else res.url


def without_images(md: str) -> str:
    """Image destinations may contain balanced parentheses, including inside a linked card."""
    out, copied_at = [], 0
    for m in re.finditer(r"!\[[^\]]*\]\(", md):
        if m.start() < copied_at:
            continue
        depth, end = 1, m.end()
        while end < len(md) and depth:
            if md[end] == "\\":
                end += 1
            elif md[end] == "(":
                depth += 1
            elif md[end] == ")":
                depth -= 1
            end += 1
        if depth:
            continue
        out.append(md[copied_at : m.start()])
        copied_at = end
    return "".join(out) + md[copied_at:]


_MD_LINK = re.compile(r'\[([^\]]{6,1000})\]\((https?://[^)\s]+|/[^)\s]*)(?:\s+"([^"]*)")?\)')
_LINE_START = re.compile(r"^[\s>#*+_|-]*(?:\d+[.)]\s*)?[\s*_]*$")


def from_markdown(md: str, base: str, source: dict) -> list[dict]:
    cfg = source["config"]
    seen: dict[str, dict] = {}
    out: list[dict] = []
    listing = str(cfg["url"] if cfg.get("url") is not None else base).replace(JINA_PREFIX, "", 1)
    # Card links wrap an image and the text: images go first so the link text is plain; a bare image link is then left without a title and skipped.
    text = without_images(md)

    def starts_line(at: int) -> bool:
        return _LINE_START.match(re.sub(r"\[\]\([^)]*\)", "", text[text.rfind("\n", 0, at) + 1 : at])) is not None

    for m in _MD_LINK.finditer(text):
        url = absolute(m.group(2), base)
        if not url or not allowed(url, source):
            continue
        section = cfg.get("preserveUrlFragment") is True and len(urlsplit(url).fragment) > 0 and listing_itself(url, listing)
        if not section and navigation_link(url, listing):
            continue
        if cfg.get("linksStartLine") is True and not starts_line(m.start()):
            continue
        label = collapse_whitespace(re.sub(r"[*_`#]", "", m.group(1)))
        # A title attribute the card text already contains is the clean title, without dates and blurbs.
        attr = collapse_whitespace(m.group(3) or "")
        title = attr if len(attr) >= 6 and attr in label else label
        if len(title) < 6:
            continue
        previous = seen.get(url)
        if previous:
            if needs_title(previous["title"]) and not needs_title(title):
                previous["title"] = title
            continue
        candidate = {"url": url, "title": title}
        seen[url] = candidate
        out.append(candidate)
    return out


def from_html(html: str, base: str, source: dict) -> list[dict]:
    c = source["config"]
    soup = parse_html(html)
    out: list[dict] = []
    seen: set[str] = set()
    listing = str(c["url"] if c.get("url") is not None else base).replace(JINA_PREFIX, "", 1)
    # Sections of the listing page are posts only for sources that keep fragments as identity.
    sections_are_posts = c.get("preserveUrlFragment") is True
    nodes = soup.select(c["itemSelector"]) if c.get("itemSelector") else soup.select("a[href]")
    for el in nodes:
        if c.get("linkSelector"):
            link_el = el if matches(el, c["linkSelector"]) else first_within(el, c["linkSelector"])
        else:
            link_el = el if el.name == "a" else first_within(el, "a[href]")
        url = absolute(attr_of(link_el, "href"), base)
        if not url or url in seen or not allowed(url, source):
            continue
        # A card or a nav link to the site's sign-in form is no post.
        if is_sign_in_url(url):
            continue
        if not sections_are_posts and listing_itself(url, listing):
            continue
        if c.get("titleSelector"):
            title_el = el if matches(el, c["titleSelector"]) else first_within(el, c["titleSelector"])
        else:
            title_el = link_el
        title = collapse_whitespace(text_of(title_el) or attr_of(link_el, "title") or "")
        if not title:
            continue
        date = NO_DATE
        if c.get("publishedAtSelector"):
            date = element_date(first_within(el, c["publishedAtSelector"]), c.get("publishedAtUtcOffset"))
        if not date.published_at and c.get("publishedAtRegex"):
            m = re.search(c["publishedAtRegex"], outer_html(el))
            date = read_publication_date(m.group(1) if m and m.re.groups >= 1 else None, _offset(c.get("publishedAtUtcOffset")))
        seen.add(url)
        out.append({"url": url, "title": title, "published_at": date.published_at, "publication_time": date.publication_time})
    return out


_DATE_HEADING = re.compile(r"(?:[^\d:：]{1,12}[:：])?\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", re.A)


def heading_date(title: str, utc_offset: str = "+08:00") -> PublicationDate | None:
    """The day a date heading names, at midnight in the source's offset."""
    m = _DATE_HEADING.fullmatch(title)
    return read_publication_date(f"{m[1]}/{m[2]}/{m[3]}", utc_offset) if m else None


def _next_element(el: Tag) -> Tag | None:
    return el.find_next_sibling()


def from_intercom_changelog(html: str, base: str, source: dict) -> list[dict]:
    soup = parse_html(html)
    out: list[dict] = []
    for block in soup.select("article .intercom-interblocks-subheading3:has(h3[id]), article .intercom-interblocks-subheading:has(h2[id])"):
        heading = block.select_one("h2[id], h3[id]")
        date = read_publication_date(text_of(heading), source["config"].get("publishedAtUtcOffset") or "+00:00")
        if not date.published_at:
            continue
        parts: list[str] = []
        titles: list[str] = []
        nxt = _next_element(block)
        while nxt is not None and nxt.select_one("h2, h3") is None:
            parts.append(outer_html(nxt))
            paragraph = nxt.select_one("p")
            text = collapse_whitespace(text_of(paragraph))
            kids = paragraph.find_all(True, recursive=False) if paragraph is not None else []
            if text and len(kids) == 1 and kids[0].name in ("b", "strong") and text == collapse_whitespace(text_of(kids[0])):
                titles.append(text)
            nxt = _next_element(nxt)
        body_html = sanitize_body("".join(parts), base)
        body_text = strip_tags(body_html)
        if not body_text.strip():
            continue
        url = resolve_url(re.sub(r"#.*$", "", base) + "#" + quote(heading["id"], safe="!$&'()*+,;=:@/?-._~%"))
        if not allowed(url, source):
            continue
        out.append({
            "url": url, "identity_key": identity_key_for_url(url, keep_fragment=True), "title": " · ".join(titles) or collapse_whitespace(text_of(heading)),
            "published_at": date.published_at, "publication_time": date.publication_time, "body_html": body_html, "body_text": body_text, "body_status": "ok",
        })  # fmt: skip
    return out


def from_docusaurus_changelog(html: str, base: str, source: dict) -> list[dict]:
    soup = parse_html(html)
    out: list[dict] = []
    offset = source["config"].get("publishedAtUtcOffset")
    off = _offset(offset)
    # A date heading is no update itself: it dates the updates under it, up to the next h2.
    section_date: PublicationDate | None = None
    for head in soup.select("article h2[id], article h3[id], .markdown h2[id], .markdown h3[id]"):
        title = collapse_whitespace(re.sub(r"#$", "", text_of(head).replace("\u200b", "")))
        date = heading_date(title, off)
        if date is not None:
            section_date = date
            continue
        if head.name == "h2":
            section_date = None
        parts: list[str] = []
        n = _next_element(head)
        while n is not None and n.name not in ("h2", "h3"):
            parts.append(outer_html(n))
            n = _next_element(n)
        body_html = sanitize_body("".join(parts), base)
        url = f"{re.sub(r'#.*$', '', base)}#{head['id']}"
        if not allowed(re.sub(r"#.*$", "", url), source):
            continue
        dated = next((d for d in (read_publication_date(title, off), section_date, read_publication_date(strip_tags(body_html)[:80], off)) if d and d.published_at), None)
        cand = {"url": url, "identity_key": f"url:{url}", "title": title}
        if dated:
            cand.update(published_at=dated.published_at, publication_time=dated.publication_time)
        out.append({**cand, "body_html": body_html, "body_text": strip_tags(body_html), "body_status": "ok"})
    return out


# --- mimo.xiaomi.com ---------------------------------------------------------------------------------------------------------------------------

_SCRIPT_STRING = r'"(?:[^"\\]|\\.)*"'


def fetch_script(url: str) -> str:
    return require_ok(guarded_fetch(url, timeout_ms=25_000), url).text()


def script_string(literal: str) -> str:
    try:
        return json.loads(re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", literal).replace("\\'", "'"))
    except ValueError:
        return literal[1:-1]


def from_mimo_home(html: str, base: str, source: dict) -> list[dict]:
    """The homepage's post rows are a prop of its own chunk, found through the route table and the runtime's chunk map (config.adapter "mimo_home").
    A homepage that no longer looks like this fails the fetch instead of falling back to the menu."""
    route_chunks: list[str] = []
    chunk_file = None
    # Entry scripts come last; the libraries loaded before them hold neither table.
    scripts = [u for u in (absolute(m.group(1), base) for m in re.finditer(r'<script\b[^>]*\bsrc="([^"]+)"', html, re.I)) if u is not None]
    for src in reversed(scripts):
        if route_chunks and chunk_file:
            break
        js = fetch_script(src)
        route = re.search(r'\{path:"/",[^{}]*\}', js)
        if route:
            route_chunks = [m.group(1) for m in re.finditer(r'\.e\("([^"]+)"\)', route.group(0))]
        mp = re.search(r'"(static/js/async/)"\+\w+\+"\."\+\(?\{([^}]*)\}\)?\[\w+\]\+"\.js"', js)
        public = re.search(r'\b\w+\.p="([^"]*)"', js)
        if mp and public is not None:
            names = {m.group(1): m.group(2) for m in re.finditer(r'"?(\w+)"?:"(\w+)"', mp.group(2))}
            root = resolve_url(public.group(1), src)

            def chunk_file(cid, names=names, prefix=mp.group(1), root=root):  # noqa: B023
                return resolve_url(f"{prefix}{cid}.{names[cid]}.js", root) if cid in names else None

    if not route_chunks or not chunk_file:
        raise FetchError("mimo_home: no route table or chunk map in the homepage scripts")
    listing = str(source["config"]["url"] if source["config"].get("url") is not None else base)
    # The page's own chunk is the last one its route loads.
    for cid in reversed(route_chunks):
        file = chunk_file(cid)
        if not file:
            continue
        js = fetch_script(file)
        at = js.find('sectionTitle:"Blog"')
        if at < 0:
            continue
        nxt = js.find("sectionTitle:", at + 1)
        section = js[at : nxt if nxt >= 0 else None]
        out: list[dict] = []
        for row in re.finditer(r'\{(?:[^{}"]|' + _SCRIPT_STRING + r")*\}", section):

            def field(name: str, row=row.group(0)) -> str:
                m = re.search(rf"\b{name}:({_SCRIPT_STRING})", row)
                return collapse_whitespace(script_string(m.group(1))) if m else ""

            url = absolute(field("link"), base)
            title = field("title")
            if not url or not title or any(c["url"] == url for c in out) or not allowed(url, source) or listing_itself(url, listing):
                continue
            desc = field("desc")
            out.append({"url": url, "title": title, "excerpt": desc if desc and desc != title else None})
        return out
    raise FetchError("mimo_home: no Blog list in the homepage's chunks")


def fetch_web_list(source: dict) -> list[dict]:
    text, via_jina, base = fetch_listing_text(source)
    cfg = source["config"]
    mode = "mimo_home" if cfg.get("adapter") == "mimo_home" else cfg["parseMode"] if cfg.get("parseMode") is not None else "markdown" if via_jina else "html"
    if mode == "mimo_home":
        out = from_mimo_home(text, base, source)
    elif mode == "markdown":
        out = from_markdown(text, base, source)
    elif mode == "docusaurus_changelog":
        out = from_docusaurus_changelog(text, base, source)
    elif mode == "intercom_changelog":
        out = from_intercom_changelog(text, base, source)
    else:
        out = from_html(text, base, source)
    if not out:
        raise FetchError(f"no items matched ({mode})")
    return out


def fetch_detail(url: str, source: dict, need: dict) -> dict:
    """What a listing's detail pages add (config.detail): the date, title and summary its rules find, and the body for a source that reads whole."""
    raise NotImplementedError("detail pages need the article reader (content/extract.ts), which is not ported yet")
