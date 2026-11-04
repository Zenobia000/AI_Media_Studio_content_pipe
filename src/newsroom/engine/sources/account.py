"""The account an X source reads, and which posts are that account's own. Port of sources/account.ts."""

from __future__ import annotations

import re

X_HANDLE = re.compile(r"from:([A-Za-z0-9_]{1,15})", re.I)


def x_handle(config: dict) -> str | None:
    """The account an X source reads: the first `from:` of its query (names may show it differently, or not at all)."""
    query = config.get("query")
    m = X_HANDLE.search("" if query is None else str(query))
    return m.group(1) if m else None
