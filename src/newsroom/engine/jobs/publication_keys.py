"""Keys of publication jobs (jobs/publication.ts)."""

from __future__ import annotations


def republish_key(source_id: str) -> str:
    return f"republish.source:{source_id}"
