"""A small DOM with the behaviour of linkedom's (parsed by htmlparser2's rules), for the article reader. Elements and text are bs4 objects so that
CSS selectors (soupsieve) work on them; the functions here give the DOM API the reader is written against. Compare nodes with `is`: a bs4 string
is equal to any string with the same characters.

Tag names are as the parser gave them (lower case); `tag_name` is the DOM's tagName (upper case for HTML elements, as written for SVG ones)."""

from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import urlsplit

import soupsieve
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from newsroom.engine.sanitize import _events

ELEMENT_NODE, TEXT_NODE, COMMENT_NODE = 1, 3, 8
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "keygen", "link", "menuitem", "meta", "param", "source", "track", "wbr"}  # linkedom's isVoid
_BARE_WHEN_EMPTY = {
    "allowfullscreen", "allowpaymentrequest", "async", "autofocus", "autoplay", "checked", "class", "contenteditable", "controls", "default", "defer", "disabled",
    "draggable", "formnovalidate", "hidden", "id", "ismap", "itemscope", "loop", "multiple", "muted", "nomodule", "novalidate", "open", "playsinline", "readonly",
    "required", "reversed", "selected", "style", "truespeed",
}  # fmt: skip
_RAW_TEXT = {"script", "style", "textarea", "title"}  # linkedom's text elements write their text as it is


class El(Tag):
    """An element that is equal only to itself."""

    __eq__ = object.__eq__
    __hash__ = object.__hash__
    # set on the elements that need them; an unset one reads as None (see __getattr__), so these are annotations only
    svg: Any
    readability: Any
    _readabilityDataTable: Any

    def __ne__(self, other) -> bool:
        return self is not other

    def __bool__(self) -> bool:
        return True

    def __getattr__(self, name):  # bs4 would look for a child tag of that name
        if name.startswith("__"):
            raise AttributeError(name)
        return self.__dict__.get(name)


class Document:
    """A parsed page: a document node holding the elements, doctype aside."""

    def __init__(self, base_uri: str = "") -> None:
        self.soup = BeautifulSoup("", "html.parser", element_classes={Tag: El}, multi_valued_attributes=None)
        self.base_uri = base_uri
        self.document_uri = base_uri

    def base_uri_value(self) -> str | None:
        """baseURI: the href of the first <base>, as written."""
        for el in descendants(self.soup):
            if el.name == "base":
                return get_attribute(el, "href")
        return None

    # -- construction -------------------------------------------------------------------------------------------------------------------------

    def create_element(self, name: str) -> El:
        el = self.soup.new_tag(name)  # the name as given: it is also what the element is written as
        el.attrs = {}
        return cast(El, el)

    def create_text_node(self, text: str) -> NavigableString:
        return NavigableString(text)

    def create_comment(self, text: str) -> Comment:
        return Comment(text)

    # -- the tree -----------------------------------------------------------------------------------------------------------------------------

    @property
    def document_element(self) -> El | None:
        return first_element_child(self.soup)

    @property
    def head(self) -> El:
        root = self.document_element
        first = first_element_child(root)
        if first is None or tag_name(first) != "HEAD":
            first = self.create_element("head")
            cast(El, root).insert(0, first)
        return first

    @property
    def body(self) -> El:
        head = self.head
        nxt = next_element_sibling(head)
        if nxt is None or tag_name(nxt) != "BODY":
            nxt = self.create_element("body")
            head.insert_after(nxt)
        return nxt

    @property
    def title(self) -> str:
        found = get_elements_by_tag_name(self.head, "title")
        return text_content(found[0]) if found else ""

    def get_element_by_id(self, ident: str) -> El | None:
        for el in descendants(self.soup):
            if el.attrs.get("id") == ident:
                return el
        return None

    def query_selector(self, selector: str, scope: Tag | None = None) -> El | None:
        return cast("El | None", (scope or self.soup).select_one(selector))

    def query_selector_all(self, selector: str, scope: Tag | None = None) -> list[El]:
        return cast("list[El]", list((scope or self.soup).select(selector)))


def parse_html(html: str, base_uri: str = "") -> Document:
    """parseHTML(html).document: htmlparser2's events become nodes, attribute names keep their case, comments are kept."""
    doc = Document(base_uri)
    build(doc, doc.soup, html)
    return doc


def build(doc: Document, root: Tag, html: str) -> None:
    node: Tag = root
    svg_depth = 0
    stack: list[bool] = []
    for ev in _events(html, keep_case=True, comments=True):
        kind = ev[0]
        if kind == "text":
            node.append(NavigableString(ev[1]))
        elif kind == "comment":
            node.append(Comment(ev[1]))
        elif kind == "open":
            el = doc.create_element(ev[1])
            el.attrs = {k: (_class_tokens(v) if k == "class" else v) for k, v in ev[2].items()}
            if svg_depth or ev[1] == "svg":
                el.svg = True
                svg_depth += 1
                stack.append(True)
            else:
                stack.append(False)
            node.append(el)
            node = el
        else:  # close
            if stack and stack.pop():
                svg_depth -= 1
            node = node.parent if node.parent is not None else root


# -- navigation -----------------------------------------------------------------------------------------------------------------------------


def node_type(n) -> int:
    if isinstance(n, Comment):
        return COMMENT_NODE
    return TEXT_NODE if isinstance(n, NavigableString) else ELEMENT_NODE


def is_element(n) -> bool:
    return isinstance(n, Tag)


def tag_name(el) -> str:
    """tagName: the element's name in upper case; "" for a node that is no element (and for the document)."""
    if not isinstance(el, Tag) or el.name == "[document]":
        return ""
    return el.name.upper()


def local_name(el) -> str:
    return el.name if isinstance(el, Tag) else ""


def node_name(n) -> str:
    return tag_name(n) if isinstance(n, Tag) else "#comment" if isinstance(n, Comment) else "#text"


def child_nodes(el) -> list:
    return list(el.contents) if isinstance(el, Tag) else []


def children(el) -> list[El]:
    return cast("list[El]", [c for c in el.contents if isinstance(c, Tag)] if isinstance(el, Tag) else [])


def first_child(el):
    return el.contents[0] if isinstance(el, Tag) and el.contents else None


def last_child(el):
    return el.contents[-1] if isinstance(el, Tag) and el.contents else None


def first_element_child(el) -> El | None:
    for c in el.contents if isinstance(el, Tag) else []:
        if isinstance(c, Tag):
            return cast(El, c)
    return None


def next_sibling(n):
    return n.next_sibling


def previous_sibling(n):
    return n.previous_sibling


def next_element_sibling(n) -> El | None:
    s = n.next_sibling
    while s is not None and not isinstance(s, Tag):
        s = s.next_sibling
    return cast("El | None", s)


def previous_element_sibling(n) -> El | None:
    s = n.previous_sibling
    while s is not None and not isinstance(s, Tag):
        s = s.previous_sibling
    return cast("El | None", s)


def parent_node(n):
    return n.parent


def parent_element(n) -> El | None:
    p = n.parent
    return p if isinstance(p, El) else None


def descendants(el):
    """Every element below `el`, in document order."""
    for d in el.descendants:
        if isinstance(d, Tag):
            yield d


def get_elements_by_tag_name(el, name: str) -> list[El]:
    """getElementsByTagName of linkedom: the name must equal the element's local name or its upper-case form exactly ("*" matches nothing)."""
    return [d for d in descendants(el) if d.name == name or d.name.upper() == name]


def text_content(n) -> str:
    """textContent: the text of every text node below (an element), or the node's own data."""
    if isinstance(n, Comment):
        return str(n)
    if isinstance(n, NavigableString):
        return str(n)
    return "".join(str(d) for d in n.descendants if isinstance(d, NavigableString) and not isinstance(d, Comment))


def set_text_content(el, text: str) -> None:
    for c in list(el.contents):
        c.extract()
    if text:
        el.append(NavigableString(text))


def contains(el, other) -> bool:
    p = other
    while p is not None:
        if p is el:
            return True
        p = p.parent
    return False


# -- attributes ------------------------------------------------------------------------------------------------------------------------------


def get_attribute(el, name: str) -> str | None:
    if not isinstance(el, Tag):
        return None
    if name == "class":
        return str(el.attrs.get("class", ""))  # linkedom answers the class list, "" without one
    value = el.attrs.get(name)
    return None if value is None else str(value)


_CLASS_SPLIT = re.compile("[" + "".join(chr(c) for c in [9, 10, 11, 12, 13, 32, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF]) + "]+")


def _class_tokens(value: str) -> str:
    """The class list linkedom keeps: each token once, in order, empty ones dropped."""
    return " ".join(dict.fromkeys(t for t in _CLASS_SPLIT.split(value) if t))


def set_attribute(el, name: str, value) -> None:
    """setAttribute: a name the element has keeps its place, a new one goes to the front of the list (as in linkedom)."""
    text = _class_tokens(str(value)) if name == "class" else str(value)
    if name in el.attrs:
        el.attrs[name] = text
    else:
        el.attrs = {name: text, **el.attrs}


def set_attribute_node(el, name: str, value: str) -> None:
    """setAttributeNode(attr.cloneNode()): an attribute the element has is replaced and, like a new one, goes to the front."""
    el.attrs.pop(name, None)
    set_attribute(el, name, value)


def remove_attribute(el, name: str) -> None:
    el.attrs.pop(name, None)


def has_attribute(el, name: str) -> bool:
    return isinstance(el, Tag) and name in el.attrs


def attributes(el) -> list[tuple[str, str]]:
    return [(k, str(v)) for k, v in el.attrs.items()]


def class_name(el) -> str:
    """element.className: the class list as text; for an SVG element an object, written "[object Object]"."""
    if isinstance(el, Tag) and el.__dict__.get("svg"):
        return "[object Object]"
    return get_attribute(el, "class") or ""


def element_id(el) -> str:
    return get_attribute(el, "id") or ""


def style_of(el) -> dict[str, str]:
    """element.style: the declarations of the style attribute (the last of a name wins; names as written)."""
    out: dict[str, str] = {}
    for rule in re.split(r"\s*;\s*", get_attribute(el, "style") or ""):
        key, sep, rest = rule.partition(":")
        key, value = key.strip(), rest.strip()
        if sep and key and value:
            out[key] = value
    return out


def style_value(el, prop: str) -> str:
    return style_of(el).get(prop, "")


# -- changing the tree -----------------------------------------------------------------------------------------------------------------------


def append_child(parent, child):
    if child.parent is not None:
        child.extract()
    parent.append(child)
    return child


def insert_before(parent, child, ref):
    if child.parent is not None:
        child.extract()
    if ref is None:
        parent.append(child)
    else:
        ref.insert_before(child)
    return child


def remove(n) -> None:
    if n.parent is not None:
        n.extract()


def remove_child(parent, child):
    child.extract()
    return child


def replace_child(parent, new, old):
    """parent.replaceChild(new, old): `new` leaves wherever it was."""
    if new is old:
        return old
    if new.parent is not None:
        new.extract()
    old.replace_with(new)
    return old


def clone_node(n, deep: bool = False):
    if isinstance(n, Comment):
        return Comment(str(n))
    if isinstance(n, NavigableString):
        return NavigableString(str(n))
    doc = _doc_of(n)
    c = doc.create_element(n.name)
    c.attrs = dict(n.attrs)
    if n.__dict__.get("svg"):
        c.svg = True
    if deep:
        for ch in n.contents:
            c.append(clone_node(ch, True))
    return c


def _doc_of(n) -> Document:
    root = n
    while root.parent is not None:
        root = root.parent
    doc = Document()
    doc.soup = root if isinstance(root, BeautifulSoup) else BeautifulSoup("", "html.parser", element_classes={Tag: El}, multi_valued_attributes=None)
    return doc


def normalize(el) -> None:
    """element.normalize(): empty text nodes go, adjacent ones merge."""
    merged: list = []
    for c in list(el.contents):
        if isinstance(c, Tag):
            normalize(c)
            merged.append(c)
        elif isinstance(c, Comment):
            merged.append(c)
        elif str(c) == "":
            c.extract()
        elif merged and isinstance(merged[-1], NavigableString) and not isinstance(merged[-1], Comment):
            joined = NavigableString(str(merged[-1]) + str(c))
            merged[-1].replace_with(joined)
            c.extract()
            merged[-1] = joined
        else:
            merged.append(c)


# -- serialisation ---------------------------------------------------------------------------------------------------------------------------


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&#160;")


def _attr_text(name: str, value: str) -> str:
    if name in _BARE_WHEN_EMPTY and not value:
        return "" if name in ("id", "class", "style") else f" {name}"
    return f' {name}="{value.replace(chr(34), "&quot;")}"'


def outer_html(n) -> str:
    """Element.toString() of linkedom: values are not escaped but for the quote, an element is written under the name it was created with."""
    if isinstance(n, Comment):
        return f"<!--{n}-->"
    if isinstance(n, NavigableString):
        return _escape_text(str(n))
    attrs = "".join(_attr_text(k, str(v)) for k, v in n.attrs.items())
    svg = bool(n.__dict__.get("svg"))
    if svg and not n.contents:
        return f"<{n.name}{attrs} />"
    if n.name.lower() in _VOID and not svg:
        return f"<{n.name}{attrs}>"
    return f"<{n.name}{attrs}>{inner_html(n)}</{n.name}>"


def inner_html(n) -> str:
    if n.name.lower() in _RAW_TEXT and not n.__dict__.get("svg"):
        return text_content(n)
    return "".join(outer_html(c) for c in n.contents)


def set_inner_html(el, html: str) -> None:
    for c in list(el.contents):
        c.extract()
    doc = _doc_of(el)
    build(doc, el, html)


# -- URLs ------------------------------------------------------------------------------------------------------------------------------------


def new_url_href(uri: str, base: str) -> str:
    """new URL(uri, base).href; raises ValueError for an address that does not parse."""
    from newsroom.engine.sanitize import resolve_url

    out = resolve_url(uri, base)
    parts = urlsplit(out)
    if not parts.scheme:
        raise ValueError(f"Invalid URL: {uri}")
    return out


def select(root, selector: str) -> list[El]:
    return list(root.select(selector))


def matches(el, selector: str) -> bool:
    return soupsieve.match(selector, el)
