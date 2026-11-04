"""HTTP 200 can be a publisher's error page, including behind a paid browser renderer. Port of lib/page-content.ts."""

from __future__ import annotations

import re

from lxml import html as lxml_html

from newsroom.engine.lib.text import collapse_whitespace, strip_tags

_ERROR_LABEL = re.compile(
    r"^(?:(?:error\s*)?(?:401|403|404|410|429|500|502|503|504)(?:\s*:?\s*(?:page )?(?:not found|forbidden|unauthorized|gone|too many requests"
    r"|internal server error|bad gateway|service unavailable|gateway time-?out))?|(?:page )?not found|access denied|just a moment\.{0,3}"
    r"|attention required!?|security verification)$",
    re.I,
)
_INTERSTITIAL = (
    re.compile(r"^(?:[\w.-]+\s+)?Performing security verification This website uses a security service to protect against malicious bots\.", re.I),
    re.compile(r"^We couldn't find the page you were looking for\. This is either because:", re.I),
)
LEAD_CHARS = 1000  # enough of the page's opening text for the interstitial templates above


class UnavailablePageError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Unavailable page: {reason}")


def assert_page_content(title: str, text: str) -> None:
    """Match page-level error labels and observed interstitial templates, never keywords in prose."""
    label = re.split(r"\s+[|—–-]\s+", collapse_whitespace(title))[0]
    if _ERROR_LABEL.match(label):
        raise UnavailablePageError(title)
    lead = collapse_whitespace(text)
    if any(p.match(lead) for p in _INTERSTITIAL):
        raise UnavailablePageError("error or security interstitial instead of content")


def _lead_text(node, out: list[str], length: list[int]) -> str:
    """The text a page opens with, read from its tree: element boundaries separate words; scripts and styles are no text."""
    if node.text and length[0] < LEAD_CHARS:
        out.append(node.text)
        length[0] += len(node.text)
    for child in node:
        if length[0] >= LEAD_CHARS:
            break
        if isinstance(child.tag, str) and child.tag.lower() not in ("script", "style", "noscript", "template"):
            out.append(" ")
            _lead_text(child, out, length)
            out.append(" ")
        if child.tail and length[0] < LEAD_CHARS:
            out.append(child.tail)
            length[0] += len(child.tail)
    return "".join(out)


def content_document(html: str):
    """The page, parsed once, after its title, main heading and opening text passed the boundary check."""
    doc = lxml_html.fromstring(html) if html.strip() else lxml_html.fromstring("<html></html>")
    body = doc.find("body") if doc.tag != "body" else doc
    title = "".join(doc.xpath("//title[1]//text()"))
    assert_page_content(title, _lead_text(body, [], [0]) if body is not None else strip_tags(html))
    assert_page_content("".join(doc.xpath("//h1[1]//text()")), "")
    return doc
