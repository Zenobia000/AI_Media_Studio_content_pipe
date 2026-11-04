"""Body HTML as Markdown: a port of turndown 7.2 with the rules content/markdown.ts adds (ATX headings, fenced code, "-" bullets, ~~strike~~,
GFM tables when they are simple, kept inline HTML for the tags Markdown has no form for). The export carries the same safe body as the web
and RSS: it runs after sanitize_body."""

from __future__ import annotations

import re
from xml.dom import Node as DomNode

import html5lib

from newsroom.engine.sanitize import sanitize_body

# JavaScript's \s, which is not Python's.
WS = "\t\n\u000b\f\r    -     　﻿"
_WS_RE = re.compile(f"[{WS}]")
_NOT_WS = f"[^{WS}]"

BLOCK = {
    "ADDRESS", "ARTICLE", "ASIDE", "AUDIO", "BLOCKQUOTE", "BODY", "CANVAS", "CENTER", "DD", "DIR", "DIV", "DL", "DT", "FIELDSET", "FIGCAPTION", "FIGURE",
    "FOOTER", "FORM", "FRAMESET", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER", "HGROUP", "HR", "HTML", "ISINDEX", "LI", "MAIN", "MENU", "NAV", "NOFRAMES",
    "NOSCRIPT", "OL", "OUTPUT", "P", "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT", "TH", "THEAD", "TR", "UL",
}  # fmt: skip
VOID = {"AREA", "BASE", "BR", "COL", "COMMAND", "EMBED", "HR", "IMG", "INPUT", "KEYGEN", "LINK", "META", "PARAM", "SOURCE", "TRACK", "WBR"}
MEANINGFUL_WHEN_BLANK = {"A", "TABLE", "THEAD", "TBODY", "TFOOT", "TH", "TD", "IFRAME", "SCRIPT", "AUDIO", "VIDEO"}
KEEP = ["sup", "sub", "u", "mark", "dl", "video"]


def js_trim(s: str) -> str:
    return re.sub(f"^[{WS}]+|[{WS}]+$", "", s)


def _name(node) -> str:
    return node.nodeName.upper()


def text_content(node) -> str:
    if node.nodeType in (DomNode.TEXT_NODE, DomNode.CDATA_SECTION_NODE):
        return node.data
    if node.nodeType != DomNode.ELEMENT_NODE:
        return ""
    return "".join(text_content(c) for c in node.childNodes if c.nodeType != DomNode.COMMENT_NODE)


def _descendants(node, names: set[str]) -> bool:
    return any(c.nodeType == DomNode.ELEMENT_NODE and (_name(c) in names or _descendants(c, names)) for c in node.childNodes)


def trim_leading_newlines(s: str) -> str:
    return re.sub(r"^\n*", "", s)


def trim_trailing_newlines(s: str) -> str:
    end = len(s)
    while end > 0 and s[end - 1] == "\n":
        end -= 1
    return s[:end]


def trim_newlines(s: str) -> str:
    return trim_trailing_newlines(trim_leading_newlines(s))


_ESCAPES = [
    (re.compile(r"\\"), r"\\\\"), (re.compile(r"\*"), r"\\*"), (re.compile(r"^-"), r"\\-"), (re.compile(r"^\+ "), r"\\+ "),
    (re.compile(r"^(=+)"), r"\\\1"), (re.compile(r"^(#{1,6}) "), r"\\\1 "), (re.compile(r"`"), r"\\`"), (re.compile(r"^~~~"), r"\\~~~"),
    (re.compile(r"\["), r"\\["), (re.compile(r"\]"), r"\\]"), (re.compile(r"^>"), r"\\>"), (re.compile(r"_"), r"\\_"),
    (re.compile(r"^(\d+)\. "), r"\1\\. "),
]  # fmt: skip


def escape_markdown(s: str) -> str:
    for pattern, repl in _ESCAPES:
        s = pattern.sub(repl, s)
    return s


def clean_attribute(value: str | None) -> str:
    return re.sub(r"(\n+" + f"[{WS}]*" + r")+", "\n", value) if value else ""


def escape_link_destination(dest: str) -> str:
    escaped = re.sub(r"([<>()])", r"\\\1", dest)
    return f"<{escaped}>" if " " in escaped else escaped


def escape_link_title(title: str) -> str:
    return title.replace('"', '\\"')


def attr(node, name: str) -> str | None:
    return node.getAttribute(name) if node.hasAttribute(name) else None


# ── the tree ────────────────────────────────────────────────────────────────────────────


def _next(prev, current):
    if (prev is not None and prev.parentNode is current) or _name(current) == "PRE":
        return current.nextSibling or current.parentNode
    return current.firstChild or current.nextSibling or current.parentNode


def _remove(node):
    nxt = node.nextSibling or node.parentNode
    node.parentNode.removeChild(node)
    return nxt


def collapse_whitespace(element) -> None:
    if element.firstChild is None or _name(element) == "PRE":
        return
    prev_text = None
    keep_leading_ws = False
    prev = None
    node = _next(prev, element)
    while node is not element:
        if node.nodeType in (DomNode.TEXT_NODE, DomNode.CDATA_SECTION_NODE):
            text = re.sub(r"[ \r\n\t]+", " ", node.data)
            if (prev_text is None or prev_text.data.endswith(" ")) and not keep_leading_ws and text[:1] == " ":
                text = text[1:]
            if not text:
                node = _remove(node)
                continue
            node.data = text
            prev_text = node
        elif node.nodeType == DomNode.ELEMENT_NODE:
            if _name(node) in BLOCK or _name(node) == "BR":
                if prev_text is not None:
                    prev_text.data = re.sub(r" $", "", prev_text.data)
                prev_text = None
                keep_leading_ws = False
            elif _name(node) in VOID or _name(node) == "PRE":
                prev_text = None
                keep_leading_ws = True
            elif prev_text is not None:
                keep_leading_ws = False
        else:
            node = _remove(node)
            continue
        nxt = _next(prev, node)
        prev = node
        node = nxt
    if prev_text is not None:
        prev_text.data = re.sub(r" $", "", prev_text.data)
        if not prev_text.data:
            _remove(prev_text)


def parse_root(html: str):
    doc = html5lib.parse(f'<x-turndown id="turndown-root">{html}</x-turndown>', treebuilder="dom", namespaceHTMLElements=False)
    root = next(e for e in doc.getElementsByTagName("x-turndown") if e.getAttribute("id") == "turndown-root")
    root.normalize()  # one text node per run of text, as the browser DOM has it
    collapse_whitespace(root)
    return root


def outer_html(node) -> str:
    from newsroom.engine.content import _serialize_walker  # noqa: PLC0415

    return _serialize_walker(node, "dom")


def inner_html(node) -> str:
    return "".join(outer_html(c) if c.nodeType == DomNode.ELEMENT_NODE else _text_html(c) for c in node.childNodes)


def _text_html(c) -> str:
    if c.nodeType != DomNode.TEXT_NODE:
        return ""
    return c.data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")


# ── per-node facts (turndown's Node()) ──────────────────────────────────────────────────


class _Info:
    __slots__ = ("is_block", "is_code", "is_blank", "leading", "trailing")


def _is_blank(node) -> bool:
    n = _name(node)
    return (
        n not in VOID
        and n not in MEANINGFUL_WHEN_BLANK
        and re.fullmatch(f"[{WS}]*", text_content(node)) is not None
        and not _descendants(node, VOID)
        and not _descendants(node, MEANINGFUL_WHEN_BLANK)
    )


_EDGE = re.compile(f"^(([ \\t\\r\\n]*)([{WS}]*))(?:(?={_NOT_WS})[\\s\\S]*{_NOT_WS})?(([{WS}]*?)([ \\t\\r\\n]*))$")


def _flanked(side: str, node) -> bool | None:
    sibling = node.previousSibling if side == "left" else node.nextSibling
    if sibling is None:
        return None
    if sibling.nodeType == DomNode.TEXT_NODE:
        return sibling.nodeValue.endswith(" ") if side == "left" else sibling.nodeValue.startswith(" ")
    if sibling.nodeType == DomNode.ELEMENT_NODE and _name(sibling) not in BLOCK:
        t = text_content(sibling)
        return t.endswith(" ") if side == "left" else t.startswith(" ")
    return None


def _info(node, infos: dict) -> _Info:
    i = _Info()
    i.is_block = _name(node) in BLOCK
    parent_info = infos.get(id(node.parentNode))
    i.is_code = _name(node) == "CODE" or bool(parent_info and parent_info.is_code)
    i.is_blank = _is_blank(node)
    if i.is_block:
        i.leading = i.trailing = ""
    else:
        m = _EDGE.match(text_content(node))
        leading, leading_ascii, leading_non_ascii, trailing, trailing_non_ascii, trailing_ascii = m.groups()
        if leading_ascii and _flanked("left", node):
            leading = leading_non_ascii
        if trailing_ascii and _flanked("right", node):
            trailing = trailing_non_ascii
        i.leading, i.trailing = leading, trailing
    infos[id(node)] = i
    return i


# ── rules ───────────────────────────────────────────────────────────────────────────────

OPTIONS = {"heading_style": "atx", "hr": "* * *", "bullet": "-", "fence": "```", "em": "_", "strong": "**", "br": "  "}


def _paragraph(content, node):
    return "\n\n" + content + "\n\n"


def _line_break(content, node):
    return OPTIONS["br"] + "\n"


def _heading(content, node):
    return "\n\n" + "#" * int(_name(node)[1]) + " " + content + "\n\n"


def _blockquote(content, node):
    return "\n\n" + re.sub(r"(?m)^", "> ", trim_newlines(content)) + "\n\n"


def _list(content, node):
    parent = node.parentNode
    last_element = next((c for c in reversed(parent.childNodes) if c.nodeType == DomNode.ELEMENT_NODE), None)
    if _name(parent) == "LI" and last_element is node:
        return "\n" + content
    return "\n\n" + content + "\n\n"


def _list_item(content, node):
    prefix = OPTIONS["bullet"] + "   "
    parent = node.parentNode
    if _name(parent) == "OL":
        start = attr(parent, "start")
        index = [c for c in parent.childNodes if c.nodeType == DomNode.ELEMENT_NODE].index(node)
        prefix = f"{_start_number(start) + index if start else index + 1}.  "
    is_paragraph = content.endswith("\n")
    content = trim_newlines(content) + ("\n" if is_paragraph else "")
    content = content.replace("\n", "\n" + " " * len(prefix))
    return prefix + content + ("\n" if node.nextSibling is not None else "")


def _start_number(start: str) -> int:
    m = re.match(r"\s*([+-]?\d+)", start)
    return int(m.group(1)) if m else 0


def _fenced_code(content, node):
    code_el = node.firstChild
    class_name = attr(code_el, "class") or ""
    m = re.search(r"language-(\S+)", class_name)
    language = m.group(1) if m else ""
    code = text_content(code_el)
    size = 3
    for match in re.finditer(r"(?m)^`{3,}", code):
        if len(match.group(0)) >= size:
            size = len(match.group(0)) + 1
    fence = "`" * size
    return "\n\n" + fence + language + "\n" + re.sub(r"\n$", "", code) + "\n" + fence + "\n\n"


def _hr(content, node):
    return "\n\n" + OPTIONS["hr"] + "\n\n"


def _inline_link(content, node):
    href = escape_link_destination(attr(node, "href") or "")
    title = escape_link_title(clean_attribute(attr(node, "title")))
    return f"[{content}]({href}{(' ' + chr(34) + title + chr(34)) if title else ''})"


def _emphasis(content, node):
    return "" if not js_trim(content) else OPTIONS["em"] + content + OPTIONS["em"]


def _strong(content, node):
    return "" if not js_trim(content) else OPTIONS["strong"] + content + OPTIONS["strong"]


def _code(content, node):
    if not content:
        return ""
    content = re.sub(r"\r?\n|\r", " ", content)
    extra = " " if re.search(r"^`|^ .*?[^ ].* $|`$", content) else ""
    delimiter = "`"
    matches = re.findall(r"`+", content)
    while delimiter in matches:
        delimiter += "`"
    return delimiter + extra + content + extra + delimiter


def _image(content, node):
    alt = escape_markdown(clean_attribute(attr(node, "alt")))
    src = escape_link_destination(attr(node, "src") or "")
    title = clean_attribute(attr(node, "title"))
    title_part = f' "{escape_link_title(title)}"' if title else ""
    return f"![{alt}]({src}{title_part})" if src else ""


def _strikethrough(content, node):
    return f"~~{content}~~"


def _children_named(node, names: set[str]):
    return [c for c in node.childNodes if c.nodeType == DomNode.ELEMENT_NODE and c.nodeName.lower() in names]


def _descendant_elements(node):
    for c in node.childNodes:
        if c.nodeType == DomNode.ELEMENT_NODE:
            yield c
            yield from _descendant_elements(c)


def _table(content, node):
    html = outer_html(node)
    rows = [e for e in _descendant_elements(node) if e.nodeName.lower() == "tr"]
    if not rows:
        return f"\n\n{html}\n\n"
    first = _children_named(rows[0], {"th", "td"})
    width = len(first)
    complex_ = (
        any(
            e.nodeName.lower() in ("table", "pre", "ul", "ol", "dl") or e.hasAttribute("rowspan") or e.hasAttribute("colspan")
            for e in _descendant_elements(node)
        )
        or not width
        or len(_children_named(rows[0], {"th"})) != width
        or any(len(_children_named(r, {"th", "td"})) != width for r in rows)
    )  # GFM cannot express merged cells, missing headings or blocks inside a cell faithfully
    if complex_:
        return f"\n\n{html}\n\n"

    def cells(row):
        return [
            re.sub(rf"[{WS}]*\n[{WS}]*", "<br>", js_trim(turndown(inner_html(c))).replace("|", "\\|")) for c in _children_named(row, {"th", "td"})
        ]

    def line(values):
        return "| " + " | ".join(values) + " |"

    aligns = []
    for cell in _children_named(rows[0], {"th"}):
        value = attr(cell, "align")
        aligns.append("---:" if value == "right" else ":---:" if value == "center" else ":---" if value == "left" else "---")
    caption_el = next(iter(_children_named(node, {"caption"})), None)
    caption = inner_html(caption_el) if caption_el is not None else ""
    head = f"{turndown(caption)}\n\n" if caption else ""
    return "\n\n" + head + "\n".join([line(cells(rows[0])), line(aligns), *[line(cells(r)) for r in rows[1:]]]) + "\n\n"


def _is_code_block(node) -> bool:
    return _name(node) == "PRE" and node.firstChild is not None and _name(node.firstChild) == "CODE"


def _code_filter(node) -> bool:
    has_siblings = node.previousSibling is not None or node.nextSibling is not None
    is_code_block = _name(node.parentNode) == "PRE" and not has_siblings
    return _name(node) == "CODE" and not is_code_block


def _rules():
    """In the order turndown looks them up: the rules content/markdown.ts adds, then turndown's own."""
    return [
        (lambda n: _name(n) == "TABLE", _table),
        (lambda n: _name(n) in ("DEL", "S"), _strikethrough),
        (lambda n: _name(n) == "P", _paragraph),
        (lambda n: _name(n) == "BR", _line_break),
        (lambda n: _name(n) in ("H1", "H2", "H3", "H4", "H5", "H6"), _heading),
        (lambda n: _name(n) == "BLOCKQUOTE", _blockquote),
        (lambda n: _name(n) in ("UL", "OL"), _list),
        (lambda n: _name(n) == "LI", _list_item),
        (_is_code_block, _fenced_code),
        (lambda n: _name(n) == "HR", _hr),
        (lambda n: _name(n) == "A" and bool(attr(n, "href")), _inline_link),
        (lambda n: _name(n) in ("EM", "I"), _emphasis),
        (lambda n: _name(n) in ("STRONG", "B"), _strong),
        (_code_filter, _code),
        (lambda n: _name(n) == "IMG", _image),
    ]


RULES = _rules()


def _for_node(node, info: _Info):
    if info.is_blank:
        return lambda content, n: "\n\n" if info.is_block else ""
    for test, replacement in RULES:
        if test(node):
            return replacement
    if node.nodeName.lower() in KEEP:
        return lambda content, n: ("\n\n" + outer_html(n) + "\n\n") if info.is_block else outer_html(n)
    return lambda content, n: ("\n\n" + content + "\n\n") if info.is_block else content


def _join(output: str, replacement: str) -> str:
    s1 = trim_trailing_newlines(output)
    s2 = trim_leading_newlines(replacement)
    nls = max(len(output) - len(s1), len(replacement) - len(s2))
    return s1 + "\n\n"[:nls] + s2


def _process(parent, infos: dict) -> str:
    output = ""
    for node in list(parent.childNodes):
        info = _info(node, infos)
        replacement = ""
        if node.nodeType == DomNode.TEXT_NODE:
            replacement = node.nodeValue if info.is_code else escape_markdown(node.nodeValue)
        elif node.nodeType == DomNode.ELEMENT_NODE:
            rule = _for_node(node, info)
            content = _process(node, infos)
            if info.leading or info.trailing:
                content = js_trim(content)
            replacement = info.leading + rule(content, node) + info.trailing
        output = _join(output, replacement)
    return output


def turndown(html: str) -> str:
    if html == "":
        return ""
    root = parse_root(html)
    output = _process(root, {})
    return re.sub(f"[\\t\\r\\n{WS}]+$", "", re.sub(r"^[\t\r\n]+", "", output))


def body_to_markdown(html: str, base_url: str | None = None) -> str:
    """Export the same safe body as the web and RSS without flattening its structure."""
    return js_trim(turndown(sanitize_body(html, base_url)))
