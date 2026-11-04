"""Settings and credentials from the environment, with the optional per-group dotenv files AIHOT uses (AIHOT_CREDENTIALS_DIR/<group>.env)."""

from __future__ import annotations

import os
from pathlib import Path


def _read_dotenv(file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def credential(group: str, name: str) -> str | None:
    """A secret: the environment variable, else the group's dotenv file in AIHOT_CREDENTIALS_DIR. Empty counts as not set."""
    value = os.environ.get(name)
    if value:
        return value
    directory = os.environ.get("AIHOT_CREDENTIALS_DIR")
    if directory:
        value = _read_dotenv(Path(directory) / f"{group}.env").get(name)
        if value:
            return value
    return None


def flag(name: str, default: bool = False) -> bool:
    """A valve: only an explicit "true" turns it on (the same rule as COLLECT_ENABLED and friends)."""
    value = os.environ.get(name)
    return default if value is None else value == "true"
