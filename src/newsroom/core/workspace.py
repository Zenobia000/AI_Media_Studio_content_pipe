"""Where the workspace lives (docs/spec/00_overview.md §5.1) and the two things every writer of it does the same way."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAIPEI = timezone(timedelta(hours=8))


def find_workspace(start: Path | str, env: dict[str, str] | None = None) -> Path | None:
    """NEWSROOM_HOME, else the first folder going up from `start` that holds config.toml and inbox/ (or newsroom/config.toml)."""
    env = os.environ if env is None else env
    if env.get("NEWSROOM_HOME"):
        return Path(env["NEWSROOM_HOME"]).resolve()
    d = Path(start).resolve()
    while True:
        if (d / "config.toml").exists() and (d / "inbox").exists():
            return d
        if (d / "newsroom" / "config.toml").exists():
            return d / "newsroom"
        if d.parent == d:
            return None
        d = d.parent


def workspace_dir() -> Path:
    """NEWSROOM_HOME, else the workspace found going up from the current folder, else <AIHOT_DATA_DIR or AIHOT/.data>/workspace (CORE-FR-003)."""
    data = os.environ.get("AIHOT_DATA_DIR") or str(Path(__file__).resolve().parents[3] / "AIHOT" / ".data")
    return find_workspace(Path.cwd()) or Path(data) / "workspace"


def inbox_dir() -> Path:
    return Path(os.environ["INBOX_DIR"]) if os.environ.get("INBOX_DIR") else workspace_dir() / "inbox"


def to_taipei(d: datetime) -> datetime:
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(TAIPEI)


def day_of(d: datetime) -> str:
    """The inbox day (YYYY-MM-DD) of an instant, in Taipei time."""
    return to_taipei(d).strftime("%Y-%m-%d")


def clock_hm(d: datetime) -> str:
    return to_taipei(d).strftime("%H:%M")


def taipei_iso(d: datetime) -> str:
    """An instant as ISO-8601 with the Taipei offset, as the front-matter of IF-002 writes it."""
    return to_taipei(d).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def write_atomic(file: Path | str, text: str) -> None:
    """Written to a temporary file and renamed, so an interruption never leaves half a file (R-DATA-5)."""
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, file)


_INBOX_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def read_annotations(directory: Path | str, start: str, before: str):
    """What the person wrote in the inbox files from day `start` up to but not including day `before`, oldest first."""
    from newsroom.collect.inbox import Annotation, parse_annotations  # noqa: PLC0415

    notes: dict[str, Annotation] = {}
    d = Path(directory)
    names = sorted(n.name for n in d.iterdir() if _INBOX_NAME.match(n.name)) if d.exists() else []
    for name in names:
        day = name[:10]
        if day < start or day >= before:
            continue
        notes.update(parse_annotations((d / name).read_text(encoding="utf-8")))
    return notes
