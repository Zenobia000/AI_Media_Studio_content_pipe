"""The one body representation for the web page, Markdown export and full RSS: whitelisted HTML. Port of content/sanitize.ts (sanitizeBody and
normalizeBlocks) with the part of sanitize-html and htmlparser2 it relies on, written for this configuration only (a fixed tag and attribute
list, disallowed tags dropped with their text kept, nothing escaped).

External HTML is never executed; images keep their original src and are signed at read time (content.proxy_body_images)."""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import quote, urljoin, urlsplit

import html5lib

from newsroom.engine.content import _parse, _serialize, _text_of, normalize_videos
from newsroom.engine.lib.image_url import is_non_article_image

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "h5", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "caption", "a", "img", "figure", "figcaption", "strong", "em", "b", "i", "u", "s", "del", "sup", "sub", "mark", "span", "dl", "dt", "dd",
    "picture", "video", "source",
}  # fmt: skip
# Elements whose content is never article text: dropped with everything inside, where other unknown tags are only unwrapped.
DROP_WHOLE = {
    "script", "style", "noscript", "textarea", "option", "iframe", "object", "embed", "applet", "form", "input", "select", "button", "label",
    "fieldset", "legend", "svg", "link", "meta", "base", "title", "head", "template", "audio", "map", "area", "frame", "frameset", "track",
    "param", "annotation", "annotation-xml", "mglyph",
}  # fmt: skip
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"], "img": ["src", "alt", "width", "height", "title"],
    "video": ["src", "poster", "width", "height", "controls", "playsinline", "preload"], "source": ["src", "type"], "code": ["class"], "pre": ["class"],
    "th": ["colspan", "rowspan", "align"], "td": ["colspan", "rowspan", "align"], "span": [],
}  # fmt: skip
ALLOWED_CLASSES = {"code": re.compile(r"^language-[\w-]+$"), "pre": re.compile(r"^language-[\w-]+$")}
SELF_CLOSING = {"img", "br", "hr", "area", "base", "basefont", "input", "link", "meta", "col"}
ALLOWED_EMPTY = {"alt"}
SCHEME_ATTRIBUTES = {"href", "src", "poster"}
NON_BOOLEAN = set(
    "abbr accept accept-charset accesskey action allow alt as autocapitalize autocomplete blocking charset cite class color cols colspan content "
    "contenteditable coords crossorigin data datetime decoding dir dirname download draggable enctype enterkeyhint fetchpriority for form formaction "
    "formenctype formmethod formtarget headers height hidden high href hreflang http-equiv id imagesizes imagesrcset inputmode integrity is itemid "
    "itemprop itemref itemtype kind label lang list loading low max maxlength media method min minlength name nonce optimum pattern ping placeholder "
    "popover popovertarget popovertargetaction poster preload referrerpolicy rel rows rowspan sandbox scope shape size sizes slot span spellcheck src "
    "srcdoc srclang srcset start step style tabindex target title translate type usemap value width wrap".split()
)  # event handler attributes are not allowed anywhere here, so they need no entry
VALID_ATTRIBUTE_NAME = re.compile(r"^[^\0\t\n\f\r /<=>]+$")

# A class naming a promotion block (msr-promo, promo-box …), not text such as "promotion".
PROMO_CLASS = re.compile(r"(?:^|[-_])promo(?:$|[-_])", re.I)

VOID = {
    "area",
    "base",
    "basefont",
    "br",
    "col",
    "command",
    "embed",
    "frame",
    "hr",
    "img",
    "input",
    "isindex",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
FOREIGN_CONTEXT = {"math", "svg"}
HTML_INTEGRATION = {"mi", "mo", "mn", "ms", "mtext", "annotation-xml", "foreignobject", "desc", "title"}
_FORM = {"input", "option", "optgroup", "select", "button", "datalist", "textarea"}
_P, _SECTION, _DDT, _RTP = {"p"}, {"thead", "tbody"}, {"dd", "dt"}, {"rt", "rp"}
OPEN_IMPLIES_CLOSE: dict[str, set[str]] = {
    "tr": {"tr", "th", "td"}, "th": {"th"}, "td": {"thead", "th", "td"}, "body": {"head", "link", "script"}, "li": {"li"}, "p": _P,
    **dict.fromkeys(["h1", "h2", "h3", "h4", "h5", "h6"], _P),
    **dict.fromkeys(["select", "input", "output", "button", "datalist", "textarea"], _FORM),
    "option": {"option"}, "optgroup": {"optgroup", "option"}, "dd": _DDT, "dt": _DDT,
    **dict.fromkeys(["address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer", "form",
                     "header", "hr", "main", "nav", "ol", "pre", "section", "table", "ul"], _P),
    "rt": _RTP, "rp": _RTP, "tbody": _SECTION, "tfoot": _SECTION,
}  # fmt: skip
SPECIAL_TAGS = {"script", "style", "title", "textarea", "xmp"}  # raw text up to the matching end tag; entities decode in title and textarea only


# ── tokenizer and tree builder (the part of htmlparser2 that decides which tags open and close) ──────────


def _decode(s: str) -> str:
    return html_lib.unescape(s) if "&" in s else s


_WS = " \n\t\f\r"


def _scan(s: str):
    """Yields ("text", str), ("open", name, attribs, self_closing) and ("close", name), as htmlparser2's tokenizer does for HTML."""
    i, n = 0, len(s)
    while i < n:
        j = s.find("<", i)
        if j == -1:
            yield "text", _decode(s[i:])
            return
        if j > i:
            yield "text", _decode(s[i:j])
        i = j
        nxt = s[i + 1 : i + 2]
        if s.startswith("<!--", i):
            end = s.find("-->", i + 4)
            i = n if end == -1 else end + 3
        elif s.startswith("<![CDATA[", i):
            end = s.find("]]>", i + 9)
            i = n if end == -1 else end + 3
        elif nxt in ("!", "?"):
            end = s.find(">", i + 2)
            i = n if end == -1 else end + 1
        elif nxt == "/":
            m = re.match(r"</([A-Za-z][^\s/>]*)", s[i:])
            if m:
                end = s.find(">", i + m.end())
                if end == -1:
                    return
                yield "close", m.group(1).lower()
                i = end + 1
            elif s[i + 2 : i + 3] == ">":
                i += 3
            else:
                end = s.find(">", i + 2)
                i = n if end == -1 else end + 1
        elif nxt.isascii() and nxt.isalpha():
            m = re.match(r"<([^\s/>]+)", s[i:])
            name = m.group(1).lower()  # type: ignore[union-attr]
            pos = i + m.end()  # type: ignore[union-attr]
            attribs: dict[str, str] = {}
            self_closing = False
            closed = False
            while pos < n:
                c = s[pos]
                if c in _WS:
                    pos += 1
                elif c == ">":
                    pos += 1
                    closed = True
                    break
                elif c == "/":
                    if s[pos + 1 : pos + 2] == ">":
                        self_closing = True
                        pos += 2
                        closed = True
                        break
                    pos += 1
                else:
                    m2 = re.compile(r"[^\s=/>]+" if c != "=" else r"=[^\s=/>]*").match(s, pos)
                    attr = m2.group(0).lower()
                    pos = m2.end()
                    value = ""
                    k = pos
                    while k < n and s[k] in _WS:
                        k += 1
                    if k < n and s[k] == "=":
                        k += 1
                        while k < n and s[k] in _WS:
                            k += 1
                        if k < n and s[k] in "\"'":
                            q = s[k]
                            end = s.find(q, k + 1)
                            if end == -1:
                                return
                            value = _decode(s[k + 1 : end])
                            pos = end + 1
                        else:
                            m3 = re.compile(r"[^\s>]*").match(s, k)
                            value = _decode(m3.group(0))
                            pos = m3.end()
                    if attr not in attribs:
                        attribs[attr] = value
            if not closed:
                return
            yield "open", name, attribs, self_closing
            i = pos
            if name in SPECIAL_TAGS and not self_closing:
                m4 = re.compile(r"</" + re.escape(name) + r"(?=[\s/>])", re.I).search(s, i)
                end = m4.start() if m4 else n
                if end > i:
                    body = s[i:end]
                    yield "text", _decode(body) if name in ("title", "textarea") else body
                i = end
        else:
            yield "text", "<"
            i += 1


def _events(s: str):
    """The open and close events of htmlparser2's Parser: elements a start tag implies closed, void elements, a stray </p> and </br>."""
    stack: list[str] = []
    foreign: list[bool] = []

    def open_(name: str, attribs: dict, self_closing: bool):
        implies = OPEN_IMPLIES_CLOSE.get(name)
        if implies:
            while stack and stack[0] in implies:
                yield "close", stack.pop(0), True
        is_void = name in VOID
        if not is_void:
            stack.insert(0, name)
            if name in FOREIGN_CONTEXT:
                foreign.insert(0, True)
            elif name in HTML_INTEGRATION:
                foreign.insert(0, False)
        yield "open", name, attribs
        if is_void:
            yield "close", name, True
        elif self_closing and foreign and foreign[0]:
            if stack and stack[0] == name:
                stack.pop(0)
                yield "close", name, True

    for ev in _scan(s):
        if ev[0] == "text":
            yield "text", ev[1]
        elif ev[0] == "open":
            yield from open_(ev[1], ev[2], ev[3])
        else:
            name = ev[1]
            if name in FOREIGN_CONTEXT or name in HTML_INTEGRATION:
                if foreign:
                    foreign.pop(0)
            if name not in VOID:
                if name in stack:
                    pos = stack.index(name)
                    for k in range(pos + 1):
                        yield "close", stack.pop(0), k != pos
                elif name == "p":
                    yield "open", "p", {}
                    yield "close", "p", True
            elif name == "br":
                yield "open", "br", {}
                yield "close", "br", False
    for name in stack:
        yield "close", name, True


# ── URLs ────────────────────────────────────────────────────────────────────────────────

_SPECIAL = {"http": "80", "https": "443", "ftp": "21", "ws": "80", "wss": "443"}
_PATH_KEEP = "!$&'()*+,-./:;=@[]^_|~" + "%"
_QUERY_KEEP = "!$%&()*+,-./:;=?@[\\]^_`{|}~" + "'"


def resolve_url(href: str, base: str | None = None) -> str:
    """new URL(href, base).toString(); an address that does not parse stays as written."""
    if not href:
        return href
    try:
        text = re.sub(r"[\t\n\r]", "", href).strip(
            "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f "
        )
        if base is None and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
            raise ValueError("relative without a base")
        text = text.replace("\\", "/") if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?[/\\]", text) or base else text
        joined = urljoin(base, text) if base else text
        parts = urlsplit(joined)
        scheme = parts.scheme.lower()
        if scheme in _SPECIAL:
            host = (parts.hostname or "").lower()
            if not host:
                raise ValueError("no host")
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                pass
            port = parts.port
            netloc = host
            if parts.username:
                netloc = quote(parts.username, safe="") + (":" + quote(parts.password, safe="") if parts.password else "") + "@" + netloc
            if port is not None and str(port) != _SPECIAL[scheme]:
                netloc += f":{port}"
            path = _normalize_path(parts.path or "/")
            out = f"{scheme}://{netloc}{path}"
            if parts.query or joined.split("#")[0].endswith("?"):
                out += "?" + quote(parts.query, safe=_QUERY_KEEP)
            if parts.fragment or joined.endswith("#"):
                out += "#" + quote(parts.fragment, safe=_QUERY_KEEP + "#")
            return out
        return joined
    except (ValueError, UnicodeError):
        return href


def _normalize_path(path: str) -> str:
    out: list[str] = []
    for seg in path.split("/")[1:]:
        low = seg.lower()
        if low in (".", "%2e"):
            continue
        if low in ("..", ".%2e", "%2e.", "%2e%2e"):
            if out:
                out.pop()
            continue
        out.append(seg)
    if path.rsplit("/", 1)[-1].lower() in (".", "..", "%2e", ".%2e", "%2e.", "%2e%2e"):
        out.append("")
    return "/" + "/".join(quote(seg, safe=_PATH_KEEP) for seg in out)


_OWN_PROXY = re.compile(r"^(?:https?://[^/?#]+)?/api/img-proxy\?", re.I)


def unwrap_proxy_url(src: str) -> str:
    """The image an address of our own image proxy stands for. Older bodies were saved with the proxy's relative address
    (`/api/img-proxy?u=…&exp=…&sig=…`); the source image is the `u` it wraps, signed again when a page is served."""
    s = src.strip()
    if not _OWN_PROXY.match(s):
        return src
    from urllib.parse import parse_qs  # noqa: PLC0415

    u = (parse_qs(s[s.index("?") + 1 :].replace("&amp;", "&"), keep_blank_values=True).get("u") or [None])[0]
    return u if u and re.match(r"^https?://", u, re.I) else src


def naughty_href(tag: str, href: str) -> bool:
    """launder's check: a scheme outside the allowed ones (http and https; an img may also hold data:)."""
    schemes = ("http", "https", "data") if tag == "img" else ("http", "https")
    href = re.sub(r"[\x00-\x20]+", "", href)
    while True:
        first = href.find("<!--")
        if first == -1:
            break
        last = href.find("-->", first + 4)
        if last == -1:
            break
        href = href[:first] + href[last + 3 :]
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9.\-+]*):", href)
    if not m:
        return False  # protocol-relative addresses are allowed; no scheme at all is fine
    return m.group(1).lower() not in schemes


# ── the sanitizer ───────────────────────────────────────────────────────────────────────


def _escape(s: str, quote_: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;") if quote_ else s


class _Frame:
    def __init__(self, tag: str, attribs: dict, position: int) -> None:
        self.tag, self.attribs, self.position, self.opening = tag, attribs, position, 0


def _transform(name: str, attribs: dict, base: str | None) -> tuple[str, dict]:
    if name == "h1":
        return "h2", attribs
    if name == "h6":
        return "h5", attribs
    if name in ("div", "section", "article"):
        return "p", attribs
    if name == "a":
        return name, {**attribs, **({"href": resolve_url(attribs["href"], base)} if attribs.get("href") else {})}
    if name == "img":
        src = unwrap_proxy_url(attribs.get("data-src") or attribs.get("data-original") or attribs.get("src") or "")
        return name, {**attribs, "src": resolve_url(src, base)}
    if name == "video":
        src = (attribs.get("src") or "").strip() or (attribs.get("data-src") or "").strip()
        poster = (attribs.get("poster") or "").strip() or (attribs.get("data-poster") or "").strip()
        return name, {**attribs, "src": resolve_url(src, base), "poster": resolve_url(unwrap_proxy_url(poster), base)}
    if name == "source":
        return name, {**attribs, "src": resolve_url((attribs.get("src") or "").strip() or (attribs.get("data-src") or "").strip(), base)}
    return name, attribs


def drop_non_body_markup(html: str) -> str:
    """Drop promotions and non-video source elements before unknown containers are unwrapped."""
    if not re.search(r"promo|<source\b", html, re.I):
        return html
    root = _parse(html)
    parents = {c: p for p in root.iter() for c in p}
    for el in [e for e in root.iter() if e.tag == "source"]:
        parent = parents.get(el)
        if parent is None or parent.tag != "video":
            _detach(parents, el)
    for el in [e for e in root.iter() if e.get("class") is not None]:
        if any(PROMO_CLASS.search(c) for c in re.split(r"\s+", el.get("class") or "")) and el in parents:
            _detach(parents, el)
    return _serialize(root)


def _detach(parents: dict, el) -> None:
    parent = parents.get(el)
    if parent is None:
        return
    siblings = list(parent)
    i = siblings.index(el)
    tail = el.tail or ""
    parent.remove(el)
    if i == 0:
        parent.text = (parent.text or "") + tail
    else:
        siblings[i - 1].tail = (siblings[i - 1].tail or "") + tail
    parents.pop(el, None)


def sanitize_body(html: str, base_url: str | None = None) -> str:
    cleaned = _sanitize(drop_non_body_markup(html), base_url)
    return normalize_blocks(cleaned)


def _sanitize(html: str, base: str | None) -> str:
    result = ""
    stack: list[_Frame] = []
    skip_map: dict[int, bool] = {}
    transform_map: dict[int, str] = {}
    depth = 0
    skip_text = False
    skip_text_depth = 0

    for ev in _events(html):
        if ev[0] == "text":
            if skip_text:
                continue
            result += _escape(ev[1])
        elif ev[0] == "open":
            name, attribs = ev[1], ev[2]
            if skip_text:
                skip_text_depth += 1
                continue
            frame = _Frame(name, attribs, len(result))
            stack.append(frame)
            skip = False
            new_name, new_attribs = _transform(name, attribs, base)
            frame.attribs = attribs = new_attribs
            if new_name != name:
                name = new_name
                transform_map[depth] = new_name
            if name not in ALLOWED_TAGS:
                skip = True
                skip_map[depth] = True
                if name in DROP_WHOLE:
                    skip_text = True
                    skip_text_depth = 1
            depth += 1
            if skip:
                continue  # we want the contents but not this tag
            result += "<" + name
            if name in ALLOWED_ATTRIBUTES:
                for a, value in list(attribs.items()):
                    if not VALID_ATTRIBUTE_NAME.match(a):
                        del frame.attribs[a]
                        continue
                    if value == "" and a not in ALLOWED_EMPTY and a in NON_BOOLEAN:
                        del frame.attribs[a]
                        continue
                    if a not in ALLOWED_ATTRIBUTES[name]:
                        del frame.attribs[a]
                        continue
                    if a in SCHEME_ATTRIBUTES and naughty_href(name, value):
                        del frame.attribs[a]
                        continue
                    if a == "class":
                        pattern = ALLOWED_CLASSES.get(name)
                        value = " ".join(c for c in re.split(r"\s+", value) if pattern and pattern.search(c))
                        if not value:
                            del frame.attribs[a]
                            continue
                    result += " " + a
                    if value:
                        result += '="' + _escape(value, True) + '"'
                    elif a in ALLOWED_EMPTY:
                        result += '=""'
            result += " />" if name in SELF_CLOSING else ">"
            frame.opening = len(result) - frame.position
        else:
            name = ev[1]
            if skip_text:
                skip_text_depth -= 1
                if skip_text_depth:
                    continue
                skip_text = False
            if not stack:
                continue  # do not crash on bad markup
            frame = stack.pop()
            if frame.tag != name:
                stack.append(frame)  # bad markup: kept for a later closing tag
                continue
            depth -= 1
            if skip_map.pop(depth, False):
                continue
            if depth in transform_map:
                name = transform_map.pop(depth)
            # An image without a usable source is left out whole.
            if frame.tag == "img" and (
                not frame.attribs.get("src") or is_non_article_image(frame.attribs["src"], frame.attribs.get("width"), frame.attribs.get("height"))
            ):
                result = result[: frame.position]
                continue
            if name in SELF_CLOSING:
                continue
            result += f"</{name}>"
    return result


# ── block normalisation (cheerio in the TypeScript) ─────────────────────────────────────

BLOCK_TAGS = {"p", "h2", "h3", "h4", "h5", "ul", "ol", "li", "blockquote", "pre", "table", "figure", "hr", "dl", "picture", "video"}


def _parents(root) -> dict:
    return {c: p for p in root.iter() for c in p}


def _has_media(el) -> bool:
    return any(d.tag in ("img", "video", "picture") for d in el.iter() if d is not el)


def _replace_with_contents(parents: dict, el) -> None:
    parent = parents[el]
    siblings = list(parent)
    i = siblings.index(el)
    prefix = el.text or ""
    children = list(el)
    tail = el.tail or ""
    parent.remove(el)
    if i == 0:
        parent.text = (parent.text or "") + prefix
    else:
        siblings[i - 1].tail = (siblings[i - 1].tail or "") + prefix
    pos = i
    for c in children:
        parent.insert(pos, c)
        parents[c] = parent
        pos += 1
    if children:
        children[-1].tail = (children[-1].tail or "") + tail
    elif i == 0:
        parent.text = (parent.text or "") + tail
    else:
        siblings[i - 1].tail = (siblings[i - 1].tail or "") + tail
    parents.pop(el, None)


def _node_html(node) -> str:
    if isinstance(node, str):
        return node.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")
    return _serialize(node)


def normalize_blocks(html: str) -> str:
    """Re-parses the sanitised HTML the way browsers do: a block element closes an open paragraph, so containers turned into <p> no longer
    leave nested or empty paragraphs behind. Links and paragraphs with neither text nor media are dropped and loose top-level text is wrapped
    in its own paragraph."""
    root = _parse(html)
    normalize_videos(root)
    parents = _parents(root)
    for tag in ("a", "p"):
        for el in [e for e in root.iter() if e.tag == tag]:
            if el in parents and not _text_of(el).strip() and not _has_media(el):
                _detach(parents, el)
    for el in [e for e in root.iter() if e.tag == "p"]:
        if el in parents and any(a.tag == "a" for a in _ancestors(parents, el)) and not _text_of(el).strip():
            _replace_with_contents(parents, el)
    out: list[str] = []
    run: list[str] = []
    meaningful = False

    def flush() -> None:
        nonlocal run, meaningful
        if meaningful:
            out.append(f"<p>{''.join(run).strip()}</p>")
        run, meaningful = [], False

    nodes: list = []
    if root.text:
        nodes.append(root.text)
    for child in root:
        nodes.append(child)
        if child.tail:
            nodes.append(child.tail)
    for node in nodes:
        if isinstance(node, str):
            run.append(_node_html(node))
            if node.strip():
                meaningful = True
        elif not isinstance(node.tag, str):
            continue  # comments
        elif node.tag in BLOCK_TAGS:
            flush()
            out.append(_node_html(node))
        else:
            run.append(_node_html(node))
            if _text_of(node).strip() or node.tag == "img" or (node.tag == "a" and any(d.tag == "img" for d in node.iter() if d is not node)):
                meaningful = True
    flush()
    return "".join(out).strip()


def _ancestors(parents: dict, el):
    while el in parents:
        el = parents[el]
        yield el


__all__ = ["html5lib", "normalize_blocks", "sanitize_body", "unwrap_proxy_url"]
