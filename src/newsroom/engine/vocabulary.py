"""Tag normalization and company identity over the industry pack's vocabulary (editorial/vocabulary.ts, the parts the read side uses)."""

from __future__ import annotations

import re
import unicodedata

from newsroom.engine import pack


def entities() -> dict[str, dict]:
    return pack.load("taxonomy")["ENTITIES"]


def entity_names(e: dict) -> list[str]:
    """Every name the vocabulary knows a company by."""
    return [e["name"], *e.get("aliases", []), *(e.get("orgNames") or []), *(e.get("otherNames") or [])]


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).strip().lower())


def entity_identity(value: str | None) -> str | None:
    """Exact entity/alias only: mentions embedded in a sentence are not ownership."""
    if not value or not value.strip():
        return None
    normalized = _key(value)
    matches = [eid for eid, e in entities().items() if any(_key(a) == normalized for a in [eid, *entity_names(e)])]
    return None if len(matches) > 1 else (matches[0] if matches else None)
