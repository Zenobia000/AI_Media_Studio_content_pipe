"""The local heat check (COL-FR-016, US-019): for S and A candidates, has the Chinese-speaking side covered it yet? A keyword search on
Threads answers; few or no matches is a gap worth taking. The search has a daily allowance and needs the platform's approval
(THREADS_ACCESS_TOKEN): without either, the inbox says "未檢查" and why, and nothing else changes."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx

from newsroom.collect.inbox import Candidate
from newsroom.collect.titles import needs_translation
from newsroom.core.config import credential

COVERED_MIN = 3  # posts that make a story "covered" in Chinese
DAILY_BUDGET = 200  # searches a day the check may spend (the platform allows 2,200; the rest is left for other uses)
DAY = timedelta(hours=24)


class QuotaError(Exception):
    def __init__(self) -> None:
        super().__init__("搜尋額度用完")


class DeniedError(Exception):
    pass


Search = Callable[[str], int]  # posts found for the words; raises QuotaError / DeniedError


def threads_search(token: str, client: httpx.Client | None = None) -> Search:
    def search(query: str) -> int:
        c = client or httpx.Client(timeout=20.0)
        res = c.get(
            "https://graph.threads.net/v1.0/keyword_search", params={"q": query, "search_type": "RECENT", "fields": "id", "access_token": token}
        )
        if res.status_code == 429:
            raise QuotaError()
        if res.status_code in (400, 401, 403):
            raise DeniedError(f"Threads 搜尋被拒絕（HTTP {res.status_code}），權限可能還沒通過審核")
        if res.status_code >= 400:
            raise RuntimeError(f"Threads 搜尋失敗（HTTP {res.status_code}）")
        return len(res.json().get("data") or [])

    return search


def default_search() -> Search | None:
    token = credential("integrations", "THREADS_ACCESS_TOKEN")
    return threads_search(token) if token else None


def describe_local(status: str, matches: int | None, reason: str | None) -> str:
    if status == "covered":
        return f"中文圈已有 {matches} 則相關貼文"
    if status == "gap":
        return f"中文圈還沒人報導（找到 {matches} 則，缺口）"
    return f"未檢查（{reason or '原因不明'}）"


_UNSET = object()


def with_local_heat(conn, listed: list[Candidate], search=_UNSET, now: datetime | None = None, budget: int = DAILY_BUDGET) -> list[Candidate]:
    """Checks the S and A candidates not checked in the last day, within the allowance, and puts the answer on every candidate that has one."""
    do_search: Search | None = default_search() if search is _UNSET else search  # type: ignore[assignment]
    now = now or datetime.now(timezone.utc)
    wanted = [c for c in listed if c.tier in ("S", "A") and not c.manual]
    if wanted:
        rows = conn.execute("SELECT candidate_id, status, checked_at FROM local_heat").fetchall()
        checked = [r for r in rows if r["status"] != "unchecked" and now - r["checked_at"] < DAY]
        recent = {r["candidate_id"] for r in checked}
        spent = len(checked)
        stop: str | None = None if do_search else "還沒設定 Threads 搜尋權限（THREADS_ACCESS_TOKEN）"

        def save(cid: str, status: str, matches: int | None, reason: str | None) -> None:
            conn.execute(
                """INSERT INTO local_heat (candidate_id, status, matches, reason, checked_at) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (candidate_id) DO UPDATE SET status = EXCLUDED.status, matches = EXCLUDED.matches, reason = EXCLUDED.reason,
                   checked_at = EXCLUDED.checked_at""",
                (cid, status, matches, reason, now),
            )

        for c in wanted:
            if c.id in recent:
                continue
            if needs_translation(c.title):
                save(c.id, "unchecked", None, "還沒有中文標題，無法用中文搜尋")
                continue
            if not stop and spent >= budget:
                stop = "今天的搜尋額度用完"
            if stop:
                save(c.id, "unchecked", None, stop)
                continue
            try:
                matches = do_search(c.title)  # type: ignore[misc]
                spent += 1
                save(c.id, "covered" if matches >= COVERED_MIN else "gap", matches, None)
            except QuotaError:
                stop = "今天的搜尋額度用完"
                save(c.id, "unchecked", None, stop)
            except DeniedError as e:
                stop = str(e)
                save(c.id, "unchecked", None, stop)
            except Exception as e:  # noqa: BLE001 — tried again on the next candidate
                save(c.id, "unchecked", None, f"搜尋失敗：{str(e)[:80]}")
    if not listed:
        return []
    found = {
        r["candidate_id"]: r
        for r in conn.execute(
            "SELECT candidate_id, status, matches, reason FROM local_heat WHERE candidate_id = ANY(%s)", ([c.id for c in listed],)
        ).fetchall()
    }
    return [
        dataclasses.replace(c, local=describe_local(found[c.id]["status"], found[c.id]["matches"], found[c.id]["reason"]))
        if c.id in found and c.tier in ("S", "A")
        else c
        for c in listed
    ]
