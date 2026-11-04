"""Published dates as list pages, articles and JSON lists print them, read the same on every host. Port of sources/dates.ts.

JavaScript's Date.parse reads many spellings (it is V8's legacy parser); the TypeScript keeps it only for a time with its zone and for English
wall-clock text read in UTC. `js_parse` reads the spellings those two uses meet: ISO 8601, and English text with a month name, a day, a year,
an optional time with AM/PM, and an optional zone (Z, GMT, UTC, the US zones, ±hh:mm, ±hhmm). Anything else is not a date."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from newsroom.engine.time import beijing_date, beijing_time

EXPLICIT_ZONE = re.compile(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:[AP]M\s*)?(?:Z|[+-]\d{2}:?\d{2}|GMT|UTC?|[EMP][SD]T|CDT)\b", re.I)

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
ZONES = {"z": 0, "gmt": 0, "utc": 0, "ut": 0, "est": -5, "edt": -4, "cst": -6, "cdt": -5, "mst": -7, "mdt": -6, "pst": -8, "pdt": -7}


def _calendar_day(y, mo, d) -> bool:
    try:
        return datetime(int(y), int(mo), int(d)).date() is not None
    except ValueError:
        return False


def _parse_offset(offset: str) -> timedelta:
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", offset)
    if not m:
        raise ValueError(offset)
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return -delta if m.group(1) == "-" else delta


def _at_offset(y, mo, d, h, mi, s, utc_offset: str) -> datetime | None:
    if not _calendar_day(y, mo, d) or int(h) > 23 or int(mi) > 59 or int(s) > 59:
        return None
    try:
        local = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=UTC)
        return local - _parse_offset(utc_offset)
    except (ValueError, OverflowError):
        return None


def _month_index(word: str) -> int | None:
    w = word.lower()
    if len(w) < 3:
        return None
    for i, m in enumerate(MONTHS):
        if w.startswith(m):
            return i + 1
    return None


def js_parse(text: str) -> datetime | None:
    """Date.parse for the spellings described above; None for NaN."""
    t = text.strip()
    iso = re.fullmatch(
        r"([+-]\d{6}|\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?)?\s*(Z|[+-]\d{2}:?\d{2})?", t, re.I
    )
    if iso:
        y, mo, d = int(iso.group(1)), int(iso.group(2) or 1), int(iso.group(3) or 1)
        h, mi, s = int(iso.group(4) or 0), int(iso.group(5) or 0), int(iso.group(6) or 0)
        frac = int(((iso.group(7) or "0") + "000")[:3])
        if not (1 <= mo <= 12 and 1 <= d <= 31 and h <= 24 and mi <= 59 and s <= 59) or not _calendar_day(y, mo, d):
            return None
        zone = iso.group(8)
        if zone is None:
            offset = timedelta(0)  # no zone: local time, which is UTC where the engine runs (Docker)
        elif zone.upper() == "Z":
            offset = timedelta(0)
        else:
            offset = _parse_offset(zone)
        return datetime(y, mo, d, tzinfo=UTC) + timedelta(hours=h, minutes=mi, seconds=s, milliseconds=frac) - offset
    # English legacy forms.
    s = re.sub(r"\([^)]*\)", " ", t)
    s = s.replace(",", " ")
    zone_match = re.search(r"(?:\b(?:GMT|UTC?|[ECMP][SD]T)\b|(?<=\d)\s*Z\b|(?:GMT|UTC)?\s*[+-]\d{2}:?\d{2}\b)\s*$", s.strip(), re.I)
    offset = timedelta(0)
    body = s.strip()
    if zone_match:
        z = zone_match.group(0).strip()
        body = body[: zone_match.start()].strip()
        zm = re.fullmatch(r"(?:GMT|UTC)?\s*([+-])(\d{2}):?(\d{2})", z, re.I)
        if zm:
            offset = timedelta(hours=int(zm.group(2)), minutes=int(zm.group(3))) * (-1 if zm.group(1) == "-" else 1)
        else:
            offset = timedelta(hours=ZONES[z.lower().rstrip(".")])
    body = re.sub(r"\b(?:GMT|UTC?|[ECMP][SD]T)\b", " ", body, flags=re.I)  # an earlier zone word is overridden by the last one, as V8 reads it
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*([AP]M)?", body, re.I)
    hour = minute = second = 0
    if time_match:
        hour, minute, second = int(time_match.group(1)), int(time_match.group(2)), int(time_match.group(3) or 0)
        meridiem = (time_match.group(5) or "").upper()
        if meridiem:
            if hour < 1 or hour > 12:
                return None
            hour = hour % 12 + (12 if meridiem == "PM" else 0)
        if hour > 24 or minute > 59 or second > 59:
            return None
        body = (body[: time_match.start()] + " " + body[time_match.end() :]).strip()
    body = re.sub(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\b", " ", body, flags=re.I)
    words = body.split()
    month = day = year = None
    for w in words:
        w = w.strip(".")
        if re.fullmatch(r"\d{4}", w) and year is None:
            year = int(w)
        elif re.fullmatch(r"\d{1,2}", w):
            if day is None:
                day = int(w)
            elif year is None:
                year = int(w) + (2000 if int(w) < 50 else 1900)
        elif re.fullmatch(r"[A-Za-z]{3,}", w) and _month_index(w) and month is None:
            month = _month_index(w)
        elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", w):
            a, b, c = (int(x) for x in w.split("/"))
            month, day, year = a, b, c + (0 if c >= 100 else 2000 if c < 50 else 1900)
        elif re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)", w, re.I):
            day = int(re.sub(r"\D", "", w))
        else:
            return None
    if month is None or day is None or year is None or not _calendar_day(year, month, day):
        return None
    return datetime(year, month, day, tzinfo=UTC) + timedelta(hours=hour, minutes=minute, seconds=second) - offset


def parse_loose_date(value: str | None, utc_offset: str = "+08:00") -> datetime | None:
    """A published date as a source prints it. Date.parse is kept only where it reads the same on every host: a time with its zone. A calendar day
    always belongs to the source, including ISO date-only text; anything else is read in the source's offset."""
    if not value:
        return None
    v = re.sub(r"(\d),?\s+at\s+(?=\d{1,2}:\d{2})", r"\1 ", value.strip(), flags=re.I)
    if not v:
        return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", v)
    if compact:
        return _at_offset(*compact.groups(), utc_offset)
    year_first = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:(?:T|\s*)(\d{1,2}):(\d{2})(?::(\d{2}))?)?", v)
    month_first = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})(?:(?:T|\s+)(\d{1,2}):(\d{2})(?::(\d{2}))?)?", v)
    numeric = None
    if year_first:
        numeric = list(year_first.groups())
    elif month_first:
        g = month_first.groups()
        numeric = [g[2], g[0], g[1], g[3], g[4], g[5]]
    if numeric and not _calendar_day(numeric[0], numeric[1], numeric[2]):
        return None
    english = None
    e1 = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b", v, re.I)
    if e1:
        english = [e1.group(1), e1.group(2), e1.group(3)]
    else:
        e2 = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\.?[,]?\s+(\d{2}|\d{4})\b", v, re.I)
        if e2:
            english = [e2.group(2), e2.group(1), e2.group(3)]
    if english:
        if len(english[2]) == 2:
            english[2] = str(int(english[2]) + (2000 if int(english[2]) < 50 else 1900))
        month = js_parse(f"{english[0]} 1, {english[2]} 00:00:00 GMT")
        if month is None or not _calendar_day(english[2], month.month, english[1]):
            return None
    if not numeric and not english:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v, flags=re.I)
    if EXPLICIT_ZONE.search(v):
        direct = js_parse(text)
        if direct is not None:
            return direct
        if not numeric:
            return None
    # 2026-09-26 / 2026/09/26 / 2026-09-26T10:00 / 2026年9月26日 (+ optional time), interpreted in the given offset.
    if numeric:
        y, mo, d = numeric[0], numeric[1], numeric[2]
        h, mi, s = numeric[3] or "00", numeric[4] or "00", numeric[5] or "00"
        m = re.search(r"\d:\d{2}(?::\d{2})?\s*([AP]M)\b", v, re.I)
        meridiem = m.group(1).upper() if m else None
        if meridiem and (int(h) < 1 or int(h) > 12):
            return None
        hour = int(h) % 12 + (12 if meridiem == "PM" else 0) if meridiem else h
        return _at_offset(y, mo, d, hour, mi, s, utc_offset)
    # Parse English wall-clock fields in UTC so the host's daylight-saving rules cannot shift them.
    en = js_parse(f"{text} GMT")
    english_day = js_parse(f"{english[0]} {english[1]}, {english[2]} GMT") if english and not re.search(r"\d:\d", text) else None
    utc = en or english_day
    if utc is None:
        return None
    return _at_offset(utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second, utc_offset)


@dataclass
class PublicationDate:
    """A source's claim keeps its precision, apart from the instant used for ordering and windows."""

    published_at: datetime | None
    publication_time: str | None  # YYYY-MM-DD for a calendar day; ISO timestamp for a real time; None for unknown


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _day_at(at: datetime, utc_offset: str) -> str:
    return (at + _parse_offset(utc_offset)).strftime("%Y-%m-%d")


def read_publication_date(value: str | None, utc_offset: str = "+08:00") -> PublicationDate:
    published = parse_loose_date(value, utc_offset)
    if not published:
        return PublicationDate(None, None)
    timed = re.search(r"\d{1,2}:\d{2}", value or "") or re.fullmatch(r"\d{14}", (value or "").strip())
    if timed:
        return PublicationDate(published, _iso(published))
    return PublicationDate(published, _day_at(published, utc_offset))


def describe_publication_time(at: datetime | None, source: str | None) -> str:
    """Legacy timestamps have lost their source precision; midnight never tells us which it was."""
    if not at:
        return "未知"
    if not source:
        return f"{beijing_date(at)} {beijing_time(at)}（歷史記錄，原始精度未知）"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source):
        return f"{source}（原文僅有日期，時刻未知）"
    return f"{beijing_date(at)} {beijing_time(at)}（北京時間）"


RELATIVE_UNITS = {"秒": 1e3, "分鐘": 60e3, "分": 60e3, "小時": 3600e3, "天": 86400e3, "second": 1e3, "minute": 60e3, "min": 60e3, "m": 60e3,
                  "hour": 3600e3, "hr": 3600e3, "h": 3600e3, "day": 86400e3, "d": 86400e3}  # fmt: skip
DAYS_BACK = {"今天": 0, "昨天": 1, "前天": 2, "yesterday": 1}


def read_relative_date(value: str | None, now: datetime, utc_offset: str = "+08:00") -> PublicationDate:
    """A page's own element printing an age instead of a date ("20小時前", "2天前", "昨天", "今天 15:46", "3 hours ago", "16h ago"), counted back from
    `now`, the moment it was read. Hours and minutes keep a time, however many; days keep the calendar day in the source's offset."""
    v = re.sub(r"^[^·]+·\s*", "", re.sub(r"\s+", " ", value or "").strip())
    none = PublicationDate(None, None)
    if not v:
        return none
    day_time = re.fullmatch(r"(今天|昨天|前天)\s*(\d{1,2}):(\d{2})", v)
    if day_time:
        day = _day_at(now - timedelta(milliseconds=DAYS_BACK[day_time.group(1)] * 86400e3), utc_offset)
        at = _at_offset(day[:4], day[5:7], day[8:10], day_time.group(2), day_time.group(3), 0, utc_offset)
        return PublicationDate(at, _iso(at)) if at else none
    ms = None
    unit_ms = 0.0
    if re.fullmatch(r"(剛剛|just now)", v, re.I):
        ms = 0.0
    elif re.fullmatch(r"(昨天|前天|yesterday)", v, re.I):
        ms, unit_ms = DAYS_BACK[v.lower()] * 86400e3, 86400e3
    else:
        m = re.fullmatch(r"(\d{1,3})\s*(秒|分鐘|分|小時|天)前", v) or re.fullmatch(
            r"(\d{1,3})\s*(?:(second|minute|min|hour|hr|day)s?|(m|h|d))\s+ago", v, re.I
        )
        name = (m.group(2) or m.group(3)) if m else None
        if m and name:
            unit_ms = RELATIVE_UNITS[name.lower()]
            ms = int(m.group(1)) * unit_ms
    if ms is None:
        return none
    at = now - timedelta(milliseconds=ms)
    # The text's own unit sets the precision: "47小時前" keeps its time, though it is more than a day back.
    if unit_ms < 86400e3:
        return PublicationDate(at, _iso(at))
    day = _day_at(at, utc_offset)
    return PublicationDate(_at_offset(day[:4], day[5:7], day[8:10], 0, 0, 0, utc_offset), day)
