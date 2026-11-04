"""calendar/events.md (COL-FR-017): the launch events a person lists. The table is read into the events table; the day before an
event the inbox gets a preparation candidate, and for a day after one ends a story that names its keywords is graded S."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsroom.core.workspace import day_of, workspace_dir

TAIPEI = timezone(timedelta(hours=8))
DEFAULT_HOURS = 2
AFTER_END = timedelta(hours=24)  # how long after the end a story still counts as about the event
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?:\s*[-~–]\s*(\d{1,2}):(\d{2}))?$")


@dataclass
class CalendarEvent:
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    url: str | None = None
    keywords: list[str] = field(default_factory=list)


def _at(date: str, h: str, m: str) -> datetime:
    y, mo, d = (int(x) for x in date.split("-"))
    return datetime(y, mo, d, int(h), int(m), tzinfo=TAIPEI)


def parse_events(text: str) -> tuple[list[CalendarEvent], list[str]]:
    """The table: 日期 | 時間 | 活動 | 關鍵字 | 官方連結. 時間 is "10:00" (two hours long) or "10:00-12:30" in Taipei time; keywords
    are separated by commas. A row that is empty or has no readable date and time is left out, and says why in `problems`."""
    events: list[CalendarEvent] = []
    problems: list[str] = []
    for raw in re.split(r"\r?\n", text):
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.sub(r"^\||\|$", "", line).split("|")]
        if len(cells) < 3 or cells[0] == "日期" or re.fullmatch(r"[-: ]+", cells[0]) or all(not c for c in cells):
            continue
        cells += [""] * (5 - len(cells))
        date, time, title, kw, url = cells[:5]
        t = _TIME.match(time)
        starts = None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and t:
            try:
                starts = _at(date, t.group(1), t.group(2))
            except ValueError:
                starts = None
        if not title or starts is None or t is None:
            problems.append(f"略過：{line}（要有 YYYY-MM-DD 日期、HH:MM 時間和活動名稱）")
            continue
        ends = starts + timedelta(hours=DEFAULT_HOURS)
        if t.group(3):
            try:
                ends = _at(date, t.group(3), t.group(4))
            except ValueError:
                problems.append(f"略過：{line}（結束時間不對）")
                continue
            if ends <= starts:
                ends += timedelta(days=1)
        events.append(
            CalendarEvent(
                id="evt_" + hashlib.sha1(f"{date}|{title}".encode()).hexdigest()[:12],
                title=title,
                starts_at=starts,
                ends_at=ends,
                url=url if re.match(r"^https?://", url) else None,
                keywords=[k.strip() for k in re.split(r"[,，、]", kw) if k.strip()],
            )
        )
    return events, problems


def just_ended(events: list[CalendarEvent], now: datetime) -> list[CalendarEvent]:
    """Events that ended within the last day: a story naming one of their keywords is about it."""
    return [e for e in events if e.ends_at <= now and now - e.ends_at < AFTER_END]


def mentions_ended_event(title: str, ended: list[CalendarEvent]) -> bool:
    """True when the title names a keyword of an event that just ended (case does not matter)."""
    t = title.lower()
    return any(k.lower() in t for e in ended for k in e.keywords)


def starting_tomorrow(events: list[CalendarEvent], now: datetime) -> list[CalendarEvent]:
    """The events that start tomorrow (Taipei) and have not started: each gets a preparation candidate today."""
    tomorrow = day_of(now + timedelta(days=1))
    return [e for e in events if e.starts_at > now and day_of(e.starts_at) == tomorrow]


def sync_events(conn, file: Path | str | None = None) -> tuple[list[CalendarEvent], list[str]]:
    """Brings the events table in line with the file: what is listed is there, what was taken out is gone."""
    path = Path(file) if file else workspace_dir() / "calendar" / "events.md"
    if not path.exists():
        return [], []
    events, problems = parse_events(path.read_text(encoding="utf-8"))
    for e in events:
        conn.execute(
            """INSERT INTO events (event_id, title, starts_at, ends_at, url, keywords) VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (event_id) DO UPDATE SET title = EXCLUDED.title, starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at,
               url = EXCLUDED.url, keywords = EXCLUDED.keywords""",
            (e.id, e.title, e.starts_at, e.ends_at, e.url, e.keywords),
        )
    conn.execute("DELETE FROM events WHERE event_id <> ALL(%s)", ([e.id for e in events],))
    return events, problems
