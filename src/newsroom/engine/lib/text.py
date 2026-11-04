"""Text helpers: port of packages/backend/src/lib/text.ts."""

from __future__ import annotations

import html.entities
import re

import regex

_WS = re.compile(r"\s+")
_HAN = regex.compile(r"\p{Script=Han}")
_KANA_HANGUL = regex.compile(r"[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]")
_LETTER = regex.compile(r"\p{L}")
# The TypeScript source writes the second range as 豈-﫿, whose first character is U+8C48 (not the U+F900 it was meant to be), so the
# effective range is U+3400-U+FAFF and Hangul counts. Kept as it is, so a title is judged the same by both versions.
_CJK = re.compile("[㐀-﫿]")
_BLANK = "\t\n\f\r "


def collapse_whitespace(s: str) -> str:
    return _WS.sub(" ", s).strip()


def is_chinese_text(text: str, language: str | None = None) -> bool:
    """A declared language wins; otherwise Han text is Chinese only without Japanese or Korean script."""
    declared = re.split(r"[-_]", language.lower())[0] if language else None
    if declared == "zh":
        return True
    if declared and declared not in ("und", "unknown", "other", "zxx"):
        return False
    return bool(_HAN.search(text)) and not _KANA_HANGUL.search(text)


def truncate(s: str, max_len: int, ellipsis: str = "…") -> str:
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - len(ellipsis))].rstrip() + ellipsis


def strip_tag_markup(html: str, replacement: str = " ") -> str:
    """Removes element markup while keeping `>` inside quoted attribute values (a link title such as `title="Platforms > Xbox"` must not
    end the tag there). Follows the small part of the HTML tokenizer needed here: an equals sign starts a quoted value only after an
    attribute name. Comments, doctypes and `< a>` keep the first-`>` behaviour."""
    out: list[str] = []
    at = 0
    n = len(html)
    while at < n:
        start = html.find("<", at)
        if start < 0:
            out.append(html[at:])
            return "".join(out)
        out.append(html[at:start])
        first_end = html.find(">", start + 1)
        if first_end < 0:
            out.append(html[start:])
            return "".join(out)
        end = first_end
        if re.match(r"^<\/?[a-z]", html[start : start + 3], re.I):
            quote = ""
            tag_name = True
            attribute_name = False
            value = "none"
            i = start + 1
            while i < n:
                char = html[i]
                if quote:
                    if char == quote:
                        quote = ""
                elif char == ">":
                    end = i
                    break
                elif value == "unquoted":
                    if char in _BLANK:
                        value = "none"
                elif value == "start":
                    if char in _BLANK:
                        i += 1
                        continue
                    value = "none"
                    if char in "\"'":
                        quote = char
                    else:
                        value = "unquoted"
                elif tag_name:
                    if char in _BLANK:
                        tag_name = False
                    elif char == "/":
                        i += 1
                        continue
                    elif char == "=":
                        tag_name = False
                        attribute_name = True
                elif char == "=":
                    if attribute_name:
                        attribute_name = False
                        value = "start"
                    else:
                        attribute_name = True  # a bare `=` is an attribute name in malformed HTML
                elif char not in _BLANK:
                    attribute_name = True
                i += 1
            if i == n:  # an unfinished quoted tag consumes the rest of the document
                out.append(replacement)
                return "".join(out)
        out.append("<>" if end == start + 1 else replacement)
        at = end + 1
    return "".join(out)


_REF = re.compile(r"&(#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]*);")
_CP1252 = {
    0x80: 0x20AC, 0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E, 0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021, 0x88: 0x02C6, 0x89: 0x2030, 0x8A: 0x0160,
    0x8B: 0x2039, 0x8C: 0x0152, 0x8E: 0x017D, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C, 0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014,
    0x98: 0x02DC, 0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A, 0x9C: 0x0153, 0x9E: 0x017E, 0x9F: 0x0178,
}  # fmt: skip


def _ref(m: re.Match[str]) -> str:
    body = m.group(1)
    if body[0] == "#":
        code = int(body[2:], 16) if body[1] in "xX" else int(body[1:])
        if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            return "�"
        return chr(_CP1252.get(code, code))
    return html.entities.html5.get(body + ";", m.group(0))


def decode_entities(s: str) -> str:
    """Character references, numeric and named, as browsers read them in text; only those ending in a semicolon, so an address keeps its
    "&copy=". Feeds escape text twice ("AI&amp;#8217;s"), so "&amp;" goes first."""
    return _REF.sub(_ref, s.replace("&amp;", "&"))


def strip_tags(html_text: str) -> str:
    """The text of HTML: without scripts and styles, every tag a word boundary, references decoded, whitespace collapsed."""
    cleaned = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", html_text, flags=re.I)
    return collapse_whitespace(decode_entities(strip_tag_markup(cleaned)))


def escape_xml(s: str) -> str:
    out = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    return re.sub("[\u0000-\u0008\u000b\u000c\u000e-\u001f]", "", out)  # XML 1.0 forbids most control characters


def cjk_ratio(s: str) -> float:
    """Share of CJK characters among letters; used to decide whether a title needs translation."""
    letters = _LETTER.findall(s)
    if not letters:
        return 0.0
    return len(_CJK.findall(s)) / len(letters)


def guess_language(s: str) -> str:
    if cjk_ratio(s) > 0.3:
        return "zh"
    if re.search(r"[a-z]", s, re.I):
        return "en"
    return "other"
