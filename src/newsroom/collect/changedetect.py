"""Page change monitoring (COL-FR-010): pages without a feed (changelogs, pricing) are watched by a self-hosted changedetection.io; each
change becomes an inbox candidate "<page> 有更新" with a summary of the difference. Polling its REST API, so the service being down costs
this one source and nothing else. (deploy/changedetection.yml starts the service.)"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from newsroom.collect.manual import add_source
from newsroom.core.config import credential
from newsroom.core.urls import identity_key_for_url

RECENT_SECONDS = 24 * 3600  # changes older than this are not brought in (the first run does not flood the inbox with history)
SUMMARY_CHARS = 600

WatchGet = Callable[[str], tuple[int, str]]  # path -> (status, text)


def api_get(base: str, key: str) -> WatchGet:
    def get(path: str) -> tuple[int, str]:
        res = httpx.get(base.rstrip("/") + "/" + path.lstrip("/"), headers={"x-api-key": key}, timeout=20.0)
        return res.status_code, res.text

    return get


def summarize(diff: str) -> str:
    """The lines the change added or removed, short enough for a note. changedetection.io's text difference marks them "(changed)",
    "(into)", "(added)" and "(removed)"; the "+" and "-" lines of a unified diff are read the same way."""

    def marked(line: str) -> bool:
        return bool(re.match(r"^\((?:changed|into|added|removed)\)\s*\S", line)) or (
            bool(re.match(r"^[+\-]\s*\S", line)) and not re.match(r"^(\+\+\+|---)", line)
        )

    text = "\n".join(ln for ln in (x.strip() for x in diff.splitlines()) if marked(ln))
    return text[:SUMMARY_CHARS] + "…" if len(text) > SUMMARY_CHARS else text


def _already_listed(conn, url: str) -> bool:
    key = identity_key_for_url(url)
    return bool(key) and bool(
        conn.execute("SELECT 1 FROM manual_candidates m JOIN articles a ON a.id = m.article_id WHERE a.identity_key = %s", (key,)).fetchone()
    )


def poll_watches(conn, get: WatchGet, now: datetime | None = None, add=add_source) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        status, text = get("api/v1/watch")
        if status != 200:
            return {"added": 0, "skipped": 0, "error": f"changedetection 回應 {status}"}
        watches = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return {"added": 0, "skipped": 0, "error": f"連不上 changedetection：{e}"}
    added = skipped = 0
    for uuid, w in watches.items():
        changed = w.get("last_changed") or 0
        if not changed or now.timestamp() - changed > RECENT_SECONDS or not re.match(r"^https?://", w.get("url") or ""):
            skipped += 1
            continue
        try:
            parts = urlsplit(w["url"])
            link = urlunsplit(parts._replace(query=urlencode([*parse_qsl(parts.query, keep_blank_values=True), ("_changed", str(changed))])))
            # An earlier poll brought this change in: adding it again would only repeat its note.
            if _already_listed(conn, link):
                skipped += 1
                continue
            history = json.loads(get(f"api/v1/watch/{uuid}/history")[1])
            stamps = sorted(int(k) for k in history)
            if len(stamps) < 2:  # the first snapshot is the page as it was found, not a change
                skipped += 1
                continue
            status, diff = get(f"api/v1/watch/{uuid}/difference/{stamps[-2]}/{stamps[-1]}?format=text")
            name = (w.get("title") or "").strip() or parts.hostname
            add(conn, link, title=f"{name} 有更新", note=f"[變動監測] {w['url']}\n{summarize(diff) if status == 200 else ''}".strip())
            added += 1
        except Exception:  # noqa: BLE001
            skipped += 1
    return {"added": added, "skipped": skipped}


def run_watch_poll(conn) -> dict:
    """The scheduled run: nothing to do when the service is not configured."""
    base, key = credential("collectors", "CHANGEDETECTION_URL"), credential("collectors", "CHANGEDETECTION_API_KEY")
    if not base or not key:
        return {"skipped": "CHANGEDETECTION_URL／CHANGEDETECTION_API_KEY 沒設定"}
    return poll_watches(conn, api_get(base, key))
