"""XML read into the plain structure fast-xml-parser gives the TypeScript collectors: an element is its text when it has nothing else, otherwise an
object with "@attribute" entries, child elements (a list when one name repeats), "#text" and "#cdata"; values that look like numbers are numbers
(which the callers print back as JavaScript would). Only the part of that parser the feeds meet is covered; XML that is not well formed is an
error here, where the parser is more forgiving."""

from __future__ import annotations

import html
import re

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def js_number_text(s: str) -> str:
    """What String(Number(s)) gives in JavaScript: the shortest digits that round-trip, written plainly between 1e-6 and 1e21. A whole number of
    more than 15 digits is kept as it is: the parser does not turn it into a number that would lose digits."""
    if re.fullmatch(r"[-+]?\d{16,}", s):
        return s
    f = float(s)
    if f != f:
        return "NaN"
    if f in (float("inf"), float("-inf")):
        return "Infinity" if f > 0 else "-Infinity"
    if f == 0:
        return "0"
    sign = "-" if f < 0 else ""
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?", repr(abs(f)).lower())
    int_part, frac_part, exp = m.group(1), m.group(2) or "", int(m.group(3) or 0)
    joined = int_part + frac_part
    stripped = joined.lstrip("0")
    point = len(int_part) - (len(joined) - len(stripped)) + exp  # where the decimal point falls among the significant digits
    digits = stripped.rstrip("0") or "0"
    n = len(digits)
    if 0 < point <= 21:
        out = digits + "0" * (point - n) if n <= point else digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        out = "0." + "0" * (-point) + digits
    else:
        e = point - 1
        out = digits[0] + ("." + digits[1:] if n > 1 else "") + f"e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + out


class _Node:
    __slots__ = ("attrs", "children", "text", "cdata", "raw", "after_cdata")

    def __init__(self, attrs: dict) -> None:
        self.attrs = {f"@{k}": v for k, v in attrs.items()}
        self.children: list[tuple[str, object]] = []
        self.text: list[str] = []
        self.cdata: list[str] = []
        self.raw: list[str] | None = None
        self.after_cdata = False


def _finish(node: _Node):
    text = "".join(seg.strip() for seg in node.text)
    if node.raw is not None:
        return {**node.attrs, "#text": "".join(node.raw).strip()}
    if not node.attrs and not node.children and not node.cdata:
        return js_number_text(text) if _NUMBER.fullmatch(text) else text
    out: dict = dict(node.attrs)
    seen: dict[str, object] = {}
    for name, value in node.children:
        if name in seen:
            current = seen[name]
            if isinstance(current, list):
                current.append(value)
            else:
                seen[name] = [current, value]
        else:
            seen[name] = value
    out.update(seen)
    if node.cdata:
        out["#cdata"] = "".join(node.cdata)
    if text:
        out["#text"] = js_number_text(text) if _NUMBER.fullmatch(text) else text
    return out


_TOKEN = re.compile(
    r"<!\[CDATA\[(?P<cdata>.*?)\]\]>|<!--.*?-->|<\?.*?\?>|<![^>]*>|</(?P<close>[^\s>]+)\s*>"
    r"|<(?P<open>[^\s/>!?]+)(?P<attrs>(?:\s+[^\s=/>]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?)*)\s*(?P<empty>/?)>",
    re.S,
)
_ATTR = re.compile(r"([^\s=/>]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")


def _decode(text: str) -> str:
    """processEntities and htmlEntities: the XML entities, numeric references and named HTML entities; an unknown name stays as written."""

    def sub(m: re.Match[str]) -> str:
        ch = html.unescape(m.group(0))
        return ch if ch != m.group(0) else m.group(0)

    return re.sub(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);", sub, text) if "&" in text else text


def parse(xml: str, stop_paths: tuple[tuple[str, ...], ...] = ()) -> dict:
    """The document as {root name: value}. `stop_paths` are element paths whose content stays raw markup (XHTML text). Like the parser it
    stands in for, this forgives what is not well formed: a close tag closes every element opened inside it, one that matches nothing is ignored,
    and text left open at the end of the input is lost."""
    xml = xml.lstrip("\ufeff")
    stack: list[tuple[str, _Node]] = []
    root: dict = {}
    pos, n = 0, len(xml)

    def close_top() -> None:
        tag, node = stack.pop()
        value = _finish(node)
        if stack:
            stack[-1][1].children.append((tag, value))
        else:
            root[tag] = value

    while pos < n:
        lt = xml.find("<", pos)
        if lt == -1:
            break  # text still open at the end of the input is dropped
        if lt > pos and stack:
            node = stack[-1][1]
            piece = _decode(xml[pos:lt])
            if node.text and not node.after_cdata:
                node.text[-1] += piece
            else:
                node.text.append(piece)
            node.after_cdata = False
        m = _TOKEN.match(xml, lt)
        if not m:
            if stack:
                stack[-1][1].text.append("<")
            pos = lt + 1
            continue
        pos = m.end()
        if m.group("cdata") is not None:
            if stack:
                stack[-1][1].cdata.append(m.group("cdata"))
                stack[-1][1].after_cdata = True
        elif m.group("close") is not None:
            name = m.group("close")
            if any(t == name for t, _ in stack):
                while stack[-1][0] != name:
                    close_top()
                close_top()
        elif m.group("open") is not None:
            name = m.group("open")
            attrs = {}
            for am in _ATTR.finditer(m.group("attrs") or ""):
                value = am.group(2) if am.group(2) is not None else am.group(3) if am.group(3) is not None else am.group(4)
                attrs[am.group(1)] = _decode(value) if value is not None else ""
            if stack:
                stack[-1][1].text.append("")  # the text so far ends here: the next piece is a new segment
            node = _Node(attrs)
            stack.append((name, node))
            path = tuple(t for t, _ in stack)
            if not m.group("empty") and attrs.get("type") == "xhtml" and any(path[-len(p) :] == p for p in stop_paths):
                # Raw markup up to the matching close tag.
                depth, scan = 1, pos
                for tm in re.finditer(rf"<(/?){re.escape(name)}(?=[\s/>])[^>]*?(/?)>", xml[pos:]):
                    if tm.group(1):
                        depth -= 1
                    elif not tm.group(2):
                        depth += 1
                    if depth == 0:
                        node.raw = [xml[pos : pos + tm.start()]]
                        scan = pos + tm.end()
                        break
                else:
                    node.raw = [xml[pos:]]
                    scan = n
                pos = scan
                close_top()
                continue
            if m.group("empty"):
                close_top()
    while stack:
        close_top()
    return root
