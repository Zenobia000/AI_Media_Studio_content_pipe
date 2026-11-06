"""Simplified -> Traditional (Taiwan usage) for what a page shows, with OpenCC `s2twp` (characters and words: 软件 -> 軟體, 内存 -> 記憶體).

A Chinese article from a Simplified site is stored and shown as it was fetched. On a site in Traditional that reads wrong, so when TRADITIONAL_DISPLAY=true the
page's Chinese body is converted as it is sent. Only text between tags is converted (not tag names, attributes or URLs), and nothing is written back: the stored
text, the exports and the search stay as fetched. (A Chinese article has no separate "original" view, so the converted body is what a reader gets.) It is opt-in: the original sent the body as it is, and the Node comparison tests read it so.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

# text between a '>' and the next '<'
_TEXT = re.compile(r">([^<>]+)(?=<)")


def enabled() -> bool:
    return os.environ.get("TRADITIONAL_DISPLAY") == "true"


@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC  # noqa: PLC0415 - only needed when it is on

    return OpenCC("s2twp.json")


def text(value: str) -> str:
    return _converter().convert(value)


def html(value: str) -> str:
    """The HTML with the text between its tags converted; the markup is untouched."""
    convert = _converter().convert
    return _TEXT.sub(lambda m: ">" + convert(m.group(1)), value)


def plain_for_display(value: str | None) -> str | None:
    """Plain text (a title, summary or reason) converted when TRADITIONAL_DISPLAY is on, else as it is; None stays None."""
    return text(value) if enabled() and value else value


def for_display(value: str) -> str:
    """`value` converted when TRADITIONAL_DISPLAY is on, else as it is."""
    return html(value) if enabled() and value else value
