"""The site and industry packs (site/site.ts, site/models.ts, industry/taxonomy.ts, selection.ts, wording.ts) as data.

They were TypeScript constants; pack/*.json is their evaluated form (exported once from the TypeScript files), and now the source of truth.
A regular expression is stored as {"$regexp": source, "flags": flags} and compiled here (with the `regex` module, which reads \\p{...} as
JavaScript does)."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import regex

PACK_DIR = Path(__file__).with_name("pack")


def _revive(value: Any) -> Any:
    if isinstance(value, dict):
        if "$regexp" in value:
            flags = 0
            if "i" in value.get("flags", ""):
                flags |= regex.IGNORECASE
            if "m" in value.get("flags", ""):
                flags |= regex.MULTILINE
            if "s" in value.get("flags", ""):
                flags |= regex.DOTALL
            return regex.compile(value["$regexp"], flags)
        return {k: _revive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_revive(v) for v in value]
    return value


@cache
def load(name: str) -> dict[str, Any]:
    """One pack file: site, models, taxonomy, selection or wording."""
    return _revive(json.loads((PACK_DIR / f"{name}.json").read_text(encoding="utf-8")))


def site() -> dict[str, Any]:
    return load("site")


def with_subject(noun: str) -> str:
    """ "AI 日報"-style wording: the industry word and the noun, with a space when the word ends in a Latin letter or digit."""
    subject = site()["SITE"]["subject"]
    return f"{subject} {noun}" if re.search(r"[A-Za-z0-9]$", subject) else f"{subject}{noun}"


def subject_after(text: str, noun: str | None = None) -> str:
    """ "按主題看 AI"-style wording: the industry word after Chinese text, with a space before a Latin one."""
    subject = site()["SITE"]["subject"]
    gap = " " if re.match(r"[A-Za-z0-9]", subject) else ""
    return f"{text}{gap}{with_subject(noun) if noun else subject}"
