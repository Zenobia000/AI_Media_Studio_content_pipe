"""The cleaned body HTML of an article as Markdown, the way IF-004 keeps it: headings, links, lists, quotes and code, with images kept by
their original address."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from lxml import html as lxml_html
from lxml.html import HtmlElement

_BLOCKS = {"p", "div", "section", "article", "figure", "figcaption", "main", "table", "tbody", "thead", "tr"}
_SKIP = {"script", "style", "noscript", "svg"}


def _absolute(href: str | None, base: str) -> str | None:
    if not href:
        return None
    try:
        return urljoin(base, href.strip())
    except ValueError:
        return None


def _children(node, base: str) -> str:
    out = [(node.text or "").replace("\n", " ") and re.sub(r"\s+", " ", node.text or "")]
    for child in node:
        out.append(_render(child, base, 0))
        if child.tail:
            out.append(re.sub(r"\s+", " ", child.tail))
    return "".join(o for o in out if o)


def _render(node, base: str, depth: int) -> str:
    if not isinstance(node.tag, str):  # comments, processing instructions
        return ""
    name = node.tag.lower()
    if name in _SKIP:
        return ""
    if len(name) == 2 and name[0] == "h" and name[1] in "123456":
        return f"\n\n{'#' * int(name[1])} {_children(node, base).strip()}\n\n"
    if name == "br":
        return "  \n"
    if name == "hr":
        return "\n\n---\n\n"
    if name in ("strong", "b"):
        t = _children(node, base).strip()
        return f"**{t}**" if t else ""
    if name in ("em", "i"):
        t = _children(node, base).strip()
        return f"*{t}*" if t else ""
    if name == "code":
        return "`" + node.text_content().replace("`", "'") + "`"
    if name == "pre":
        return "\n\n```\n" + re.sub(r"\n+$", "", node.text_content()) + "\n```\n\n"
    if name == "a":
        href = _absolute(node.get("href"), base)
        text = _children(node, base).strip()
        return f"[{text}]({href})" if href and text else text
    if name == "img":
        src = _absolute(node.get("src") or node.get("data-src"), base)
        alt = re.sub(r"[\[\]]", "", node.get("alt") or "")
        return f"![{alt}]({src})" if src else ""
    if name == "blockquote":
        return "\n\n" + "\n".join(f"> {line}" for line in _children(node, base).strip().split("\n")) + "\n\n"
    if name in ("ul", "ol"):
        items = [c for c in node if isinstance(c.tag, str) and c.tag.lower() == "li"]
        lines = []
        for i, li in enumerate(items):
            mark = f"{i + 1}." if name == "ol" else "-"
            lines.append("  " * depth + f"{mark} " + re.sub(r"\n{2,}", "\n", _children(li, base).strip()))
        return "\n\n" + "\n".join(lines) + "\n\n"
    if name in ("td", "th"):
        return _children(node, base).strip() + " | "
    inner = _children(node, base)
    return f"\n\n{inner.strip()}\n\n" if name in _BLOCKS else inner


def html_to_markdown(html: str, base: str) -> str:
    if not html.strip():
        return ""
    root: HtmlElement = lxml_html.fromstring(f"<!doctype html><html><body>{html}</body></html>")
    body = root.find("body") if root.tag != "body" else root
    text = _render(body, base, 0) if body is not None else ""
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
