"""Reading an article page: the source's rules, the page's publication date, and the article found by Readability. Port of the page-reading half of
content/extract.ts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import regex
from bs4 import Comment, NavigableString, Tag

from newsroom.engine import dom as D
from newsroom.engine._readability_p1 import js_trim, jsre
from newsroom.engine.lib.page_content import UnavailablePageError, assert_page_content, content_document
from newsroom.engine.lib.text import strip_tags
from newsroom.engine.lib.video_url import is_video_page_url
from newsroom.engine.readability import Readability
from newsroom.engine.sanitize import sanitize_body
from newsroom.engine.sources.dates import PublicationDate, read_publication_date

MIN_BODY_CHARS = 200
_DAY_MS = 86_400_000


def _off(value) -> str:
    return "+08:00" if value is None else value


def _text_of(n) -> str:
    return D.text_content(n)


def settle_streamed_content(document: D.Document) -> None:
    """Restore only completed React streaming boundaries, without evaluating any page script."""
    call_re = jsre(r'\$RC\("(B:[\w:.-]+)","(S:[\w:.-]+)"\)\s*;?\s*$')
    runtime_re = jsre(r"^\$R[BCV]=")
    for script in D.select(document.soup, "script:not([src])"):
        typ = (D.get_attribute(script, "type") or "").lower() if D.has_attribute(script, "type") else None
        if typ and typ not in ("text/javascript", "application/javascript"):
            continue
        text = _text_of(script)
        # React emits a standalone completion call, or appends it to its runtime definition. Do not interpret occurrences inside data, comments,
        # documentation strings or arbitrary programs.
        call = call_re.search(text)
        if not call:
            continue
        before = js_trim(text[: call.start()])
        if before and not (runtime_re.search(before) and "$RC=function(" in before and before.endswith("};")):
            continue
        boundary = document.get_element_by_id(call.group(1))
        segment = document.get_element_by_id(call.group(2))
        start = boundary.previous_sibling if boundary is not None else None
        parent = boundary.parent if boundary is not None else None
        if (
            boundary is None or boundary.name != "template" or segment is None or not D.has_attribute(segment, "hidden") or parent is None
            or start is None or D.node_type(start) != 8 or str(start) not in ("$?", "$~") or D.contains(segment, boundary)
        ):  # fmt: skip
            continue
        end = boundary.next_sibling
        depth = 0
        while end is not None:
            if D.node_type(end) == 8:
                marker = str(end)
                if marker == "/$":
                    if depth == 0:
                        break
                    depth -= 1
                elif marker in ("$", "$?", "$!", "$~"):
                    depth += 1
            end = end.next_sibling
        if end is None:
            continue
        fallback = boundary.next_sibling
        D.remove(boundary)
        while fallback is not None and fallback is not end:
            nxt = fallback.next_sibling
            D.remove(fallback)
            fallback = nxt
        while D.first_child(segment) is not None:
            D.insert_before(parent, D.first_child(segment), end)
        D.remove(segment)
        start.replace_with(Comment("$"))


def settle_inline_visibility(document: D.Document) -> None:
    """Readability leaves out what an inline style hides, but compares style.display and style.visibility literally: a style that sets either is reduced
    to what a browser applies (names and keywords in any case, an important declaration over a later plain one, else the last)."""
    important_re = jsre(r"!\s*important$")
    for el in D.select(document.soup, "[style]"):
        style = D.get_attribute(el, "style") or ""
        if not regex.search(r"display|visibility", style, regex.I):
            continue
        applied: dict[str, tuple[str, bool]] = {}
        for declaration in style.split(";"):
            colon = declaration.find(":")
            name = js_trim(declaration[:colon]).lower() if colon >= 0 else js_trim(declaration[:-1]).lower()
            if colon < 0 or name not in ("display", "visibility"):
                continue
            raw = js_trim(declaration[colon + 1 :]).lower()
            important = important_re.search(raw) is not None
            value = js_trim(important_re.sub("", raw))
            if value and (important or not (applied.get(name) or ("", False))[1]):
                applied[name] = (value, important)
        D.set_attribute(el, "style", ";".join(f"{name}:{value}" for name, (value, _) in applied.items()))


@dataclass
class PreserveRule:
    selector: str
    as_: str  # "pre" | "table"
    code: str | None = None
    caption: str | None = None


@dataclass
class PageRules:
    utc_offset: str | None = None
    preserve: list[PreserveRule] = field(default_factory=list)
    accept_language: str | None = None


def article_utc_offset(config: dict) -> str | None:
    """The offset a source's article pages print their dates in: its detail rules' own, else its listing's."""
    detail = config.get("detail") or {}
    return detail["publishedAtUtcOffset"] if detail.get("publishedAtUtcOffset") is not None else config.get("publishedAtUtcOffset")


def page_rules(config: dict) -> PageRules:
    rules = (config.get("detail") or {}).get("preserve") or []
    return PageRules(article_utc_offset(config), [PreserveRule(r["selector"], r["as"], r.get("code"), r.get("caption")) for r in rules], config.get("acceptLanguage"))


def shown_text(el) -> str:
    """Text of an element as a reader sees it: without scripts, styles and parts hidden from assistive technology."""
    copy = D.clone_node(el, True)
    for hidden in D.select(copy, 'script, style, [aria-hidden="true"]'):
        D.remove(hidden)
    return js_trim(_text_of(copy))


def _first(el, selector: str | None):
    found = D.select(el, selector) if selector else []
    return found[0] if found else None


def _code_block(document: D.Document, el, rule: PreserveRule):
    """The widget's text (its `code` part) as a code block, verbatim, under its caption when it has one."""
    text = _first(el, rule.code) if rule.code else el
    if text is None:
        return None
    pre = document.create_element("pre")
    code = document.create_element("code")
    D.set_text_content(code, shown_text(text))
    pre.append(code)
    label = _first(el, rule.caption)
    caption = shown_text(label) if label is not None else ""
    if not caption:
        return pre
    block = document.create_element("div")
    p = document.create_element("p")
    D.set_text_content(p, caption)
    block.append(p)
    block.append(pre)
    return block


def _description_table(document: D.Document, el):
    """Side-by-side description lists as one table: each child of the element is a column, headed by its first other element with text; their terms are
    the rows. None unless every column lists the same terms."""
    columns: list[dict[str, Any]] = []
    for column in D.children(el):
        lst = _first(column, "dl")
        heading = next((c for c in D.children(column) if c is not lst and D.get_attribute(c, "aria-hidden") != "true" and shown_text(c)), None)
        rows: Any = []
        if lst is not None:
            for term in D.select(lst, "dt"):
                sib = D.next_element_sibling(term)
                rows.append({"label": shown_text(term), "value": shown_text(sib) if sib is not None and sib.name == "dd" else ""})
        columns.append({"label": shown_text(heading) if heading is not None else "", "rows": rows})
    rows = columns[0]["rows"] if columns else None
    if not rows or any(
        not c["label"] or len(c["rows"]) != len(rows) or any(not r["label"] or not r["value"] or r["label"] != rows[i]["label"] for i, r in enumerate(c["rows"]))
        for c in columns
    ):
        return None
    table = document.create_element("table")
    head = document.create_element("thead")
    body = document.create_element("tbody")

    def row(cells):
        tr = document.create_element("tr")
        for tag, text in cells:
            c = document.create_element(tag)
            D.set_text_content(c, text)
            tr.append(c)
        return tr

    head.append(row([("th", t) for t in ["", *[c["label"] for c in columns]]]))
    for i, entry in enumerate(rows):
        body.append(row([("th", entry["label"]), *[("td", c["rows"][i]["value"]) for c in columns]]))
    table.append(head)
    table.append(body)
    return table


def preserve_widgets(document: D.Document, rules: list[PreserveRule]) -> None:
    """A source's declared widgets get a portable structure before Readability removes their controls and CSS."""
    for rule in rules:
        for el in D.select(document.soup, rule.selector):
            content = _code_block(document, el, rule) if rule.as_ == "pre" else _description_table(document, el)
            if content is None:
                continue
            for name in ("hidden", "aria-hidden", "style"):
                if D.has_attribute(el, name):
                    D.set_attribute(content, name, D.get_attribute(el, name))
            el.replace_with(content)


PUBLISHED_META = ['meta[property="article:published_time"]', 'meta[name="pubdate"]', 'meta[itemprop="datePublished"]', 'meta[name="parsely-pub-date"]']


def _no_constants(name):
    raise ValueError(name)


def structured_published(document: D.Document) -> str | None:
    """The datePublished of the page's structured data (JSON-LD, also inside @graph)."""

    def find(v, depth: int = 0):
        if depth > 6 or not isinstance(v, (dict, list)):
            return None
        if isinstance(v, list):
            for x in v:
                got = find(x, depth + 1)
                if got:
                    return got
            return None
        if isinstance(v.get("datePublished"), str) and v["datePublished"]:
            return v["datePublished"]
        return find(v.get("@graph"), depth + 1)

    for el in D.select(document.soup, 'script[type="application/ld+json"]'):
        try:
            got = find(json.loads(_text_of(el), parse_constant=_no_constants))
        except ValueError:
            continue  # a broken block names no date
        if got:
            return got
    return None


def page_publication_date(document: D.Document, utc_offset: str | None = None) -> PublicationDate:
    """The page's own publication time: its named publication metadata, never a modification time or a date in its prose. The first one found counts;
    a date without a time gives way to a time the page names for that same day."""
    values = []
    for selector in PUBLISHED_META:
        meta = document.soup.select_one(selector)
        values.append(D.get_attribute(meta, "content") if meta is not None else None)
    values.append(structured_published(document))
    time_el = document.soup.select_one('time[itemprop~="datePublished"][datetime]')
    values.append(D.get_attribute(time_el, "datetime") if time_el is not None else None)
    day: PublicationDate | None = None
    for value in values:
        date = read_publication_date(value, _off(utc_offset))
        if not date.published_at:
            continue
        timed = "T" in (date.publication_time or "")
        if day is None:
            if timed:
                return date
            day = date
        elif timed and date.published_at >= cast(datetime, day.published_at) and (date.published_at - cast(datetime, day.published_at)).total_seconds() * 1000 < _DAY_MS:
            return date
    return day if day is not None else PublicationDate(None, None)


def _collapsed(s: str | None) -> str:
    return js_trim(jsre(r"\s+").sub(" ", s or ""))


def _words(s: str) -> str:
    """Only letters and digits: "Value: what is it?" and "Value What is it" are the same words."""
    return regex.sub(r"[^\p{L}\p{N}]+", "", s).lower()


_HIDDEN_STYLE = jsre(r"(?:^|;)(?:display:none|visibility:hidden)(?:;|$)")


def _hidden_in(el, figure) -> bool:
    """Hidden by an attribute or an inline style (settle_inline_visibility), itself or inside its figure."""
    node = el
    while node is not None and node is not figure:
        if D.has_attribute(node, "hidden") or _HIDDEN_STYLE.search(D.get_attribute(node, "style") or "") is not None:
            return True
        node = D.parent_element(node)
    return False


def _only_drawings(holder) -> bool:
    """A [role="img"] holder that has nothing besides its svgs: no text, image or video of its own."""
    copy = D.clone_node(holder, True)
    for svg in D.select(copy, "svg"):
        D.remove(svg)
    return not _collapsed(_text_of(copy)) and copy.select_one("img, picture, video") is None


def _by_id(document: D.Document, ident: str):
    return document.get_element_by_id(ident)


def _drawing_text(document: D.Document, el) -> list[str]:
    """The text alternative of a drawing, a line each: what aria-labelledby points to, aria-label, or an svg's title and desc."""
    ids = [i for i in jsre(r"\s+").split(D.get_attribute(el, "aria-labelledby") or "") if i]
    labelled = [t for t in (_collapsed(_text_of(_by_id(document, i)) if _by_id(document, i) is not None else "") for i in ids) if t]
    if labelled:
        return labelled
    label = _collapsed(D.get_attribute(el, "aria-label"))
    if label:
        return [label]
    if el.name != "svg":
        return []
    out = []
    for name in ("title", "desc"):
        child = next((c for c in D.children(el) if c.name == name), None)
        text = _collapsed(_text_of(child) if child is not None else "")
        if text:
            out.append(text)
    return out


def _chain(el):
    node = el
    while isinstance(node, Tag) and node.name != "[document]":
        yield node
        node = node.parent


def describe_figure_drawings(document: D.Document) -> None:
    """An inline SVG drawing in a figure is not kept: its colours come from the page's stylesheet and its labels are not prose. The drawing's own text
    alternative stands in for it as a paragraph, written as text, never as markup."""
    figures: dict[int, dict] = {}
    for svg in D.select(document.soup, "figure svg"):
        figure = next((a for a in _chain(svg) if a.name == "figure"), None)
        if figure is None or svg.parent is None:
            continue  # inside a drawing already replaced
        holder = next((a for a in _chain(svg) if D.get_attribute(a, "role") == "img" and D.has_attribute(a, "role")), None)
        held = holder if holder is not None and holder is not figure and D.contains(figure, holder) else None
        # The holder describes the drawing; it is replaced whole only when it holds nothing else.
        drawing = held if held is not None and _only_drawings(held) else svg
        lines = _drawing_text(document, held) if held is not None else []
        if not lines:
            lines.extend(_drawing_text(document, svg))
        text = " ".join(lines)
        if not text:
            continue
        seen = figures.get(id(figure))
        if seen is None:
            copy = D.clone_node(figure, True)
            # Readability drops aria-hidden elements, so their words are not shown either.
            for el in D.select(copy, 'svg, [role="img"], [hidden], [style], [aria-hidden="true"]'):
                if el.name == "svg" or (D.get_attribute(el, "role") == "img" and _only_drawings(el)) or D.get_attribute(el, "aria-hidden") == "true" or _hidden_in(el, copy):
                    D.remove(el)
            seen = {"shown": _words(_text_of(copy)), "texts": {}}
            figures[id(figure)] = seen
        if _words(text) in seen["shown"]:
            continue
        earlier = seen["texts"].get(text)
        if earlier is not None and (not _hidden_in(earlier, figure) or _hidden_in(drawing, figure)):
            D.remove(drawing)
            continue
        p = document.create_element("p")
        for i, line in enumerate(lines):
            if i:
                p.append(document.create_element("br"))
            p.append(NavigableString(line))
        for name in ("hidden", "style"):
            if D.has_attribute(drawing, name):
                D.set_attribute(p, name, D.get_attribute(drawing, name))
        drawing.replace_with(p)
        if earlier is not None:
            D.remove(earlier)
        seen["texts"][text] = p


def keep_asides_among_paragraphs(document: D.Document) -> None:
    """Readability removes every aside. One set among the article's paragraphs that carries prose, such as an update note or a correction, is part of what
    the author wrote: it is kept as a quotation (keeping the aside's attributes)."""
    for aside in D.select(document.soup, "aside"):
        parent = aside.parent
        if parent is None or not any(c.name == "p" for c in D.children(parent)):
            continue
        if aside.select_one("form, input, button") is not None:
            continue
        prose = _collapsed(" ".join(_text_of(p) for p in D.select(aside, "p")))
        linked = _collapsed(" ".join(_text_of(a) for a in D.select(aside, "p a")))
        if len(prose) < 20 or len(linked) * 2 > len(prose):
            continue
        quote = document.create_element("blockquote")
        for name, value in D.attributes(aside):
            D.set_attribute(quote, name, value)
        for c in list(aside.contents):
            quote.append(c)
        aside.replace_with(quote)


_LAYOUT_CLASS = jsre(r"^(?:[\w-]+:)*(?:overflow|overscroll|scroll|snap)-[\w-]+$")


def drop_layout_classes(document: D.Document) -> None:
    """Utility classes that only set overflow, scrolling or snapping read to Readability as "hidden" or "scroll": they are taken off before scoring."""
    for el in D.select(document.soup, "[class]"):
        classes = [c for c in jsre(r"\s+").split(D.get_attribute(el, "class") or "") if c]
        kept = [c for c in classes if not _LAYOUT_CLASS.search(c)]
        if len(kept) == len(classes):
            continue
        if kept:
            D.set_attribute(el, "class", " ".join(kept))
        else:
            D.remove_attribute(el, "class")


_TRAILING_CHROME = [
    regex.compile(p, regex.I)
    for p in (
        r"^topics$", r"^tags?$", r"^latest in [\p{L}\s&-]{1,30}$", r"^latest (?:news|stories)$", r"^most (?:popular|read)$",
        r"^related (?:articles|posts|stories|content|reading|coverage)$", r"^more (?:stories|articles|news)$", r"^(?:keep reading|read next|up next)$",
        r"^(?:you (?:might|may) also like|recommended(?: for you)?)$", r"^(?:subscribe|sign up)\b.{0,80}$", r"^share (?:this|on)\b.{0,40}$",
        r"^follow us\b.{0,40}$", r"^advertisement$",
    )
]  # fmt: skip


def trim_trailing_chrome(html: str) -> str:
    """Short blocks a news page puts after the article ("Topics", "Latest in AI"): only headings and calls to action without a full stop, only at the
    very end of an extracted page, one block at a time."""
    from newsroom.engine.sources.web_list import outer_html as page_html
    from newsroom.engine.sources.web_list import parse_html as parse_page
    from newsroom.engine.sources.web_list import text_of as page_text

    soup = parse_page(html)
    root = soup.body if soup.body is not None else soup
    blocks = [c for c in root.contents if isinstance(c, Tag)]
    removed = False
    while len(blocks) > 1:
        last = blocks[-1]
        text = js_trim(jsre(r"\s+").sub(" ", page_text(last)))
        if last.select_one("img, video, picture") is not None or regex.search(r"[.。!！?？:：]$", text) or not any(p.search(text) for p in _TRAILING_CHROME):
            break
        last.decompose()
        blocks = blocks[:-1]
        removed = True
    return "".join(page_html(c) for c in root.contents) if removed else html


@dataclass
class ExtractedBody:
    html: str
    text: str
    images: list[dict]
    via: str  # "readability" | "jina"
    published_at: object = None
    publication_time: str | None = None


def readable_document(document: D.Document, url: str, rules: PageRules | None = None) -> ExtractedBody | None:
    """readable() on a page already parsed and checked (content_document). Readability changes the document."""
    rules = rules or PageRules()
    if is_video_page_url(url):
        return None
    date = page_publication_date(document, rules.utc_offset)
    base = document.create_element("base")
    D.set_attribute(base, "href", url)
    D.append_child(document.head, base)
    settle_streamed_content(document)
    settle_inline_visibility(document)
    preserve_widgets(document, rules.preserve or [])
    describe_figure_drawings(document)
    keep_asides_among_paragraphs(document)
    drop_layout_classes(document)
    # A document theme is not an article candidate: names such as "header-dark" can discard the root.
    D.remove_attribute(document.document_element, "class")
    article = Readability(document, char_threshold=MIN_BODY_CHARS, keep_classes=False).parse()
    if not article or not article["content"]:
        return None
    clean = trim_trailing_chrome(sanitize_body(article["content"], url))
    text = strip_tags(clean)
    if len(text) < MIN_BODY_CHARS:
        return None
    try:
        assert_page_content(article["title"] or "", text)
    except UnavailablePageError:
        return None
    images: list[dict] = []
    for m in regex.finditer(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>', clean, regex.I):
        w = regex.search(r'\bwidth="(\d+)"', m.group(0))
        h = regex.search(r'\bheight="(\d+)"', m.group(0))
        images.append({"kind": "image", "url": m.group(1).replace("&amp;", "&"), "width": int(w.group(1)) if w else None, "height": int(h.group(1)) if h else None})
        if len(images) >= 12:
            break
    return ExtractedBody(clean, text, images, "readability", date.published_at, date.publication_time)


def readable(html: str, url: str, rules: PageRules | None = None) -> ExtractedBody | None:
    """The page's article, read under its source's rules; None for an error page or no article."""
    if is_video_page_url(url):
        return None
    try:
        return readable_document(content_document(html), url, rules)
    except UnavailablePageError:
        return None
