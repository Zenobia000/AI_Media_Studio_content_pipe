"""Every editorial prompt lives in the industry pack as a Markdown file (pack/prompts/*.md), so a new industry changes its taste by editing text, not code.
Two template forms, nothing else: {{name}} a value the calling step passes (plus siteName), {{> file}} another prompt file, inserted as it is. A missing value or
file is an error, never a silent blank. Versions are content hashes, so a receipt always says which wording produced a result. Port of editorial/prompts.ts."""

from __future__ import annotations

import hashlib
import re

from newsroom.engine import pack

TOKEN = re.compile(r"\{\{(>\s*)?([A-Za-z][\w.-]*)\s*\}\}", re.A)
_files: dict[str, str] = {}


def raw(name: str) -> str:
    text = _files.get(name)
    if text is None:
        text = (pack.PACK_DIR / "prompts" / f"{name}.md").read_bytes().decode("utf-8")
        _files[name] = text
    return text


def _expand_text(text: str, seen: list[str]) -> tuple[str, list[str]]:
    """`text` with its includes expanded, and the names of every file it read."""
    used: list[str] = []

    def sub(m: re.Match[str]) -> str:
        if not m.group(1):
            return m.group(0)
        inner_text, inner_used = _expand(m.group(2), seen)
        used.extend(inner_used)
        return inner_text

    return TOKEN.sub(sub, text), used


def _expand(name: str, seen: list[str] | None = None) -> tuple[str, list[str]]:
    seen = seen or []
    if name in seen:
        raise RuntimeError(f"prompt include cycle: {' → '.join([*seen, name])}")
    text, used = _expand_text(raw(name), [*seen, name])
    return text, [name, *used]


def _fill(name: str, text: str, values: dict[str, str]) -> str:
    all_values = {"siteName": pack.site()["SITE"]["name"], **values}

    def sub(m: re.Match[str]) -> str:
        value = all_values.get(m.group(2))
        if value is None:
            raise RuntimeError("prompt " + name + ": no value for {{" + m.group(2) + "}}")
        return value

    return TOKEN.sub(sub, text)


def prompt_text(name: str, values: dict[str, str] | None = None) -> str:
    """A prompt with its values filled in."""
    return _fill(name, _expand(name)[0], values or {})


def prompt_from_text(label: str, text: str, values: dict[str, str] | None = None) -> str:
    """Wording given as text instead of a pack file, rendered like one; `label` names it in errors."""
    return _fill(label, _expand_text(text, [])[0], values or {})


def prompt_version(*names: str) -> str:
    """`name@hash` over the files a prompt reads, so any edit shows up as a new version."""
    used = sorted({u for n in names for u in _expand(n)[1]})
    h = hashlib.sha256()
    for n in used:
        h.update(f"{n}\n{raw(n)}\n".encode())
    return f"{'+'.join(names)}@{h.hexdigest()[:10]}"
