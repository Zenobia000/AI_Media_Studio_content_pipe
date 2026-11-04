"""The front-matter of a Markdown file (IF-002, IF-003): `key: value` lines between two `---` lines.

An update changes the lines of the keys it names and adds new keys at the end; every other byte of the file, unknown keys and the
body included, stays as it was (CORE-FR-005).
"""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"^---[ \t]*$")
_KEY = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:")
_QUOTED = re.compile(r"^([\"'])(.*)\1$")
_NEEDS_QUOTES = re.compile(r"^$|^[\s\"'\[{&*!|>%@`#-]|[:#]\s|\s$|[\r\n]")


def _split(text: str):
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    if not lines or not _FENCE.match(lines[0]):
        return None
    end = next((i for i in range(1, len(lines)) if _FENCE.match(lines[i])), -1)
    if end < 0:
        return None
    return lines[1:end], lines[end + 1 :], eol


def _key_of(line: str) -> str | None:
    m = _KEY.match(line)
    return m.group(1) if m else None


def read_front_matter(text: str) -> tuple[dict[str, str], str]:
    """The fields as written (values as text, quotes removed), and the body. A file without front-matter has none."""
    s = _split(text)
    if s is None:
        return {}, text
    head, rest, eol = s
    fields: dict[str, str] = {}
    for line in head:
        key = _key_of(line)
        if key:
            value = line[line.index(":") + 1 :].strip()
            m = _QUOTED.match(value)
            fields[key] = m.group(2) if m else value
    return fields, eol.join(rest)


def _render(value: str) -> str:
    """A value that YAML would read as something else, or that has a line break or a colon-space, is quoted."""
    return json.dumps(value, ensure_ascii=False) if _NEEDS_QUOTES.search(value) else value


def update_front_matter(text: str, updates: dict[str, str]) -> str:
    """Sets the given fields; a file without front-matter gets a block in front of its text."""
    s = _split(text)
    eol = s[2] if s else ("\r\n" if "\r\n" in text else "\n")
    head = list(s[0]) if s else []
    for key, value in updates.items():
        line = f"{key}: {_render(value)}"
        at = next((i for i, h in enumerate(head) if _key_of(h) == key), -1)
        if at >= 0:
            head[at] = line
        else:
            head.append(line)
    block = eol.join(["---", *head, "---"])
    if s:
        return block + (eol + eol.join(s[1]) if s[1] else "")
    return block + eol + text
