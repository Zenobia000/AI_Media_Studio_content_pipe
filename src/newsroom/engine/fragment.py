"""HTML fragments as an editable tree, for the translation and the reuse of translated wording (cheerio's part in content/presentation.ts and
editorial/translate.ts). Parsed and written by html5lib, which follows the same HTML specification as the parse5 cheerio uses; the tree is the standard
DOM html5lib builds, so nodes are compared with `is`."""

from __future__ import annotations

from xml.dom import Node

import html5lib

from newsroom.engine.content import _serialize_walker

_NOT_TEXT = {"script", "style"}


def parse(html: str):
    return html5lib.parseFragment(html, treebuilder="dom", namespaceHTMLElements=False)


def is_element(node) -> bool:
    return node.nodeType == Node.ELEMENT_NODE


def elements(root) -> list:
    """Every element below `root`, in document order."""
    out: list = []

    def visit(node) -> None:
        for child in node.childNodes:
            if is_element(child):
                out.append(child)
                visit(child)

    visit(root)
    return out


def outer_html(node) -> str:
    return _serialize_walker(node, "dom")


def inner_html(node) -> str:
    return "".join(outer_html(child) for child in node.childNodes)


def text_of(node) -> str:
    """The text below a node, as cheerio's text(): text nodes only, none from scripts and styles."""
    if node.nodeType == Node.TEXT_NODE:
        return node.data
    if node.nodeType == Node.ELEMENT_NODE and node.tagName in _NOT_TEXT:
        return ""
    if node.nodeType in (Node.ELEMENT_NODE, Node.DOCUMENT_FRAGMENT_NODE):
        return "".join(text_of(child) for child in node.childNodes)
    return ""


def attribs(el) -> list[tuple[str, str]]:
    return [(a.name, a.value) for a in (el.attributes.item(i) for i in range(el.attributes.length))]


def attrib(el, name: str) -> str | None:
    return el.getAttribute(name) if el.hasAttribute(name) else None


def set_attribs(el, pairs: list[tuple[str, str]]) -> None:
    for name, _ in attribs(el):
        el.removeAttribute(name)
    for name, value in pairs:
        el.setAttribute(name, value)


def ancestors(el, stop=None):
    parent = el.parentNode
    while parent is not None and parent is not stop and parent.nodeType == Node.ELEMENT_NODE:
        yield parent
        parent = parent.parentNode


def set_inner_html(el, html: str) -> None:
    for child in list(el.childNodes):
        el.removeChild(child)
    for child in list(parse(html).childNodes):
        el.appendChild(child)


def replace_with_html(el, html: str) -> None:
    parent = el.parentNode
    for child in list(parse(html).childNodes):
        parent.insertBefore(child, el)
    parent.removeChild(el)


def replace_with_text(el, text: str) -> None:
    el.parentNode.replaceChild(el.ownerDocument.createTextNode(text), el)
