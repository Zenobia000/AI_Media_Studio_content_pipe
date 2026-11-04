"""add-source: a link a person found goes into the inbox as a candidate (COL-FR-001). The same address added again, in any spelling the
identity rule treats as one, keeps one article and one candidate and gains the note."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from lxml import html as lxml_html

from newsroom.collect.materials import upsert_manual_material
from newsroom.core.http import Get, default_get
from newsroom.core.urls import normalize_url
from newsroom.core.workspace import day_of

MANUAL_SOURCE = "manual"

_SHORTENERS = {
    "t.co",
    "bit.ly",
    "buff.ly",
    "lnkd.in",
    "goo.gl",
    "tinyurl.com",
    "ow.ly",
    "is.gd",
    "dlvr.it",
    "t.ly",
    "cutt.ly",
    "reurl.cc",
    "fb.me",
    "wp.me",
}


class InvalidUrlError(Exception):
    def __init__(self, url: str) -> None:
        super().__init__(f"不是有效的網址（只接受 http／https）：{url}")


def is_short_url(text: str) -> bool:
    try:
        host = (urlsplit(text.strip()).hostname or "").lower()
    except ValueError:
        return False
    return re.sub(r"^www\.", "", host) in _SHORTENERS


def expand_short_url(text: str, get: Get = default_get) -> str:
    """Short links name the same page under another address, so the one a person adds is followed to the page before the identity rule
    sees it. A link that cannot be followed is kept as it is."""
    if not is_short_url(text):
        return text
    try:
        page = get(text.strip())
    except Exception:  # noqa: BLE001
        return text
    return page.url if page.status < 400 and re.match(r"^https?://", page.url) and not is_short_url(page.url) else text


def page_title(url: str, get: Get) -> str | None:
    """The page's own title (og:title, else <title>); nothing when it cannot be read."""
    try:
        res = get(url, {"accept-language": "zh-TW,zh;q=0.9,en;q=0.8"})
        if res.status != 200 or "html" not in res.type:
            return None
        doc = lxml_html.fromstring(res.text)
        og = doc.xpath('//meta[@property="og:title"]/@content')
        title = (og[0] if og else "") or "".join(doc.xpath("//title/text()"))
        return re.sub(r"\s+", " ", title).strip() or None
    except Exception:  # noqa: BLE001
        return None


@dataclass
class AddSourceResult:
    article_id: str
    url: str
    created: bool  # the article is new to the site
    already_added: bool  # it was already a manual candidate, so only the note (and any program or formats) was added
    inbox_date: str  # the day (Asia/Taipei) of the inbox it first appeared in


def add_source(
    conn, text: str, note: str = "", program: str | None = None, formats: list[str] | None = None, title: str | None = None, get: Get | None = None
) -> AddSourceResult:
    getter = get or default_get
    url = normalize_url(expand_short_url(text, getter))
    if not url:
        raise InvalidUrlError(text)
    name = (title or "").strip() or page_title(url, getter) or url
    conn.execute(
        """INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health, tags)
           VALUES (%s, '手動新增', 'external', '{}'::jsonb, 'T2', 'editorial', 1440, true, 'ok', %s) ON CONFLICT (id) DO NOTHING""",
        (MANUAL_SOURCE, ["manual"]),
    )
    material = upsert_manual_material(conn, MANUAL_SOURCE, url, name)
    row = conn.execute(
        """INSERT INTO manual_candidates (article_id, notes, program, formats) VALUES (%s, %s, %s, %s)
           ON CONFLICT (article_id) DO UPDATE SET
             notes = CASE WHEN EXCLUDED.notes = '' THEN manual_candidates.notes
                          WHEN manual_candidates.notes = '' THEN EXCLUDED.notes
                          ELSE manual_candidates.notes || E'\\n' || EXCLUDED.notes END,
             program = COALESCE(EXCLUDED.program, manual_candidates.program),
             formats = CASE WHEN cardinality(EXCLUDED.formats) = 0 THEN manual_candidates.formats ELSE EXCLUDED.formats END
           RETURNING (xmax = 0) AS inserted, added_at""",
        (material.article_id, note.strip(), program, formats or []),
    ).fetchone()
    added_at: datetime = row["added_at"]
    return AddSourceResult(material.article_id, url, material.created, not row["inserted"], day_of(added_at))
