"""Chinese titles for candidates (COL-FR-012): the triage-inbox skill translates, this keeps the result. A candidate is translated once;
without a stored title the inbox shows the original, so switching this off changes nothing else."""

from __future__ import annotations

import dataclasses
import re

from newsroom.collect.inbox import Candidate

_HAN = re.compile("[一-鿿]")


def needs_translation(title: str) -> bool:
    return not _HAN.search(title)


class EmptyTitleError(Exception):
    def __init__(self) -> None:
        super().__init__("中文標題不能是空的")


def set_title_zh(conn, candidate_id: str, title_zh: str, force: bool = False) -> bool:
    """Stores the title; a candidate that already has one keeps it unless `force`. Returns whether it was written."""
    title = title_zh.strip()
    if not title:
        raise EmptyTitleError()
    rows = conn.execute(
        """INSERT INTO candidate_titles (candidate_id, title_zh) VALUES (%s, %s)
           ON CONFLICT (candidate_id) DO UPDATE SET title_zh = EXCLUDED.title_zh, set_at = now() WHERE %s
           RETURNING candidate_id""",
        (candidate_id, title, force),
    ).fetchall()
    return len(rows) > 0


def with_titles_zh(conn, candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return candidates
    rows = conn.execute("SELECT candidate_id, title_zh FROM candidate_titles WHERE candidate_id = ANY(%s)", ([c.id for c in candidates],)).fetchall()
    zh = {r["candidate_id"]: r["title_zh"] for r in rows}
    return [dataclasses.replace(c, title=zh[c.id]) if c.id in zh else c for c in candidates]


def pending_titles(conn) -> list[dict[str, str]]:
    """The listed candidates still in a foreign language and without a stored title."""
    rows = conn.execute(
        """SELECT s.candidate_id, s.snapshot FROM candidate_state s LEFT JOIN candidate_titles t USING (candidate_id)
           WHERE t.candidate_id IS NULL AND s.status IN ('new', 'picked') ORDER BY s.first_listed_at DESC"""
    ).fetchall()
    return [{"id": r["candidate_id"], "title": r["snapshot"]["title"]} for r in rows if needs_translation(r["snapshot"]["title"])]
