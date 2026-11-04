"""Beijing (Asia/Shanghai, fixed UTC+8, no DST) calendar helpers: port of packages/contracts/src/time.ts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
BEIJING_TIMEZONE = "Asia/Shanghai"  # for whatever takes a zone name (a scheduler, SQL AT TIME ZONE)
WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]


def _instant(value: datetime | str | int | float) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    text = value.strip()
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)  # a date alone, or a time without a zone, is UTC as in JavaScript


def beijing_date(instant) -> str:
    """YYYY-MM-DD of the instant in Beijing time."""
    return _instant(instant).astimezone(BEIJING).strftime("%Y-%m-%d")


def beijing_time(instant) -> str:
    """HH:mm of the instant in Beijing time."""
    return _instant(instant).astimezone(BEIJING).strftime("%H:%M")


def beijing_iso(instant) -> str:
    """The instant as ISO 8601 in Beijing time, to the millisecond: "2026-09-26T08:07:13.000+08:00"."""
    d = _instant(instant).astimezone(BEIJING)
    return f"{d.strftime('%Y-%m-%dT%H:%M:%S')}.{d.microsecond // 1000:03d}+08:00"


def month_day(date: str, today: str | None = None) -> str:
    """ "9月28日" of a calendar date; a day of another year than `today` carries its year: "2019年2月19日"."""
    year = f"{date[:4]}年" if today and date[:4] != today[:4] else ""
    return f"{year}{int(date[5:7])}月{int(date[8:10])}日"


def beijing_day(instant) -> str:
    return month_day(beijing_date(instant))


def beijing_stamp(instant) -> str:
    """ "9月29日 13:40" in Beijing time."""
    return f"{beijing_day(instant)} {beijing_time(instant)}"


def beijing_midnight(date: str) -> datetime:
    """UTC instant of 00:00 Beijing on the given calendar day."""
    return datetime.fromisoformat(f"{date}T00:00:00+08:00").astimezone(UTC)


def beijing_at(date: str, time: str) -> datetime:
    """UTC instant of an HH:mm Beijing time on the given calendar day."""
    return datetime.fromisoformat(f"{date}T{time}:00+08:00").astimezone(UTC)


def add_days(date: str, days: int) -> str:
    return (datetime.fromisoformat(date) + timedelta(days=days)).strftime("%Y-%m-%d")


def beijing_weekday(date: str) -> str:
    return WEEKDAYS[(datetime.fromisoformat(date).weekday() + 1) % 7]


def iso_week_label(date: str) -> str:
    """ISO week label (e.g. 2026-W38) of a calendar date."""
    year, week, _ = datetime.fromisoformat(date).isocalendar()
    return f"{year}-W{week:02d}"


def iso_week_range(label: str) -> dict[str, str] | None:
    """Monday..Sunday calendar dates of an ISO week label."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", label)
    if not m:
        return None
    year, week = int(m.group(1)), int(m.group(2))
    if week < 1 or week > 53:
        return None
    try:
        start = datetime.fromisocalendar(year, week, 1).strftime("%Y-%m-%d")
    except ValueError:
        return None
    if iso_week_label(start) != label:
        return None
    return {"start": start, "end": add_days(start, 6)}


def month_range(label: str) -> dict[str, str] | None:
    """First and last day (YYYY-MM-DD) of a calendar month label such as 2026-09; None for anything else."""
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", label)
    if not m:
        return None
    nxt = f"{int(m.group(1)) + 1}-01-01" if int(m.group(2)) == 12 else f"{m.group(1)}-{int(m.group(2)) + 1:02d}-01"
    return {"start": f"{label}-01", "end": add_days(nxt, -1)}


def is_valid_date(date: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return False
    try:
        return datetime.fromisoformat(date).strftime("%Y-%m-%d") == date
    except ValueError:
        return False
