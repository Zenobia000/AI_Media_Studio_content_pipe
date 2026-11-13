"""素材區: the articles a person put there with the bookmark on an article, and the download of what they ask for. Each pick gets a topic of its own the
first time it is downloaded (the same topic the CLI makes with open-topic, so fetch-topic, fetch-media and assets-topic work on it afterwards), and three
kinds can be asked for separately:

  article  the text of the page (fetch-topic: sources/<id>.md)
  photo    pictures (media layers 2 and 3: the page's own images, then gallery-dl)
  video    videos and GIFs (layers 1, 2 and 3: a platform's API, the page's own files, then yt-dlp)

Layer 4, the browser recording, is only for a topic that has a reel to make, so a pick does not run it.
A download runs in a thread of the process that asked for it; at most TWO run at a time, the others wait as "queued"."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from newsroom.collect.topics import open_topic
from newsroom.core.workspace import workspace_dir

log = logging.getLogger("newsroom.picks")

KINDS = ("article", "photo", "video")
MAX_PICKS = 500
STALE_AFTER = timedelta(minutes=45)  # a download that has shown no end by then was cut off (the process stopped); it is listed as such
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
PROGRAM = "ai-today"  # a topic needs one; the picks have no use for it
_slots = threading.BoundedSemaphore(2)


def wanted_of(kinds: list[str] | None) -> list[str]:
    """The kinds asked for, in a fixed order; nothing asked for means all three."""
    asked = [k for k in KINDS if k in (kinds or KINDS)]
    return asked or list(KINDS)


def add_picks(conn, ids: list[str]) -> int:
    """Puts articles in the area (those that exist and are not there yet); returns how many were new. The area holds at most MAX_PICKS."""
    clean = [i for i in dict.fromkeys(ids) if ID_PATTERN.match(i)]
    if not clean:
        return 0
    room = MAX_PICKS - conn.execute("SELECT count(*) AS n FROM material_picks").fetchone()["n"]
    if room <= 0:
        return 0
    rows = conn.execute(
        """INSERT INTO material_picks (article_id) SELECT id FROM articles WHERE id = ANY(%s) AND id NOT IN (SELECT article_id FROM material_picks)
           ORDER BY id LIMIT %s ON CONFLICT DO NOTHING RETURNING article_id""",
        (clean, room),
    ).fetchall()
    return len(rows)


def remove_pick(conn, article_id: str) -> bool:
    """Takes an article out of the area. What was downloaded stays on disk with its topic."""
    return conn.execute("DELETE FROM material_picks WHERE article_id = %s AND state NOT IN ('queued', 'running')", (article_id,)).rowcount > 0


VIDEO_FILES = frozenset({"mp4", "mov", "webm", "mkv", "m4v", "gif"})
PHOTO_FILES = frozenset({"png", "jpg", "jpeg", "webp", "avif"})


def _folder_files(topic_id: str) -> list[dict[str, Any]]:
    """The files of a topic by kind, for the page: the article text (sources/), pictures and videos (media/, GIFs with the videos). Paths are relative to
    the workspace."""
    root = workspace_dir()
    base = root / "topics" / topic_id
    out: list[dict[str, Any]] = []
    for sub in ("sources", "media"):
        folder = base / sub
        if not folder.exists():
            continue
        for f in sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")):
            ext = f.suffix.lower().lstrip(".")
            if sub == "sources":
                kind = "article" if ext == "md" else None
            else:
                kind = "video" if ext in VIDEO_FILES else "photo" if ext in PHOTO_FILES else None
            if kind:
                out.append({"kind": kind, "path": f.relative_to(root).as_posix(), "name": f.name, "size": f.stat().st_size})
    return out


def list_picks(conn) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT p.article_id, p.added_at, p.topic_id, p.state, p.wanted, p.started_at, p.finished_at, p.result,
                  a.title, a.url, s.name AS source_name, an.title_zh, an.summary_zh
           FROM material_picks p JOIN articles a ON a.id = p.article_id JOIN sources s ON s.id = a.source_id
           LEFT JOIN LATERAL (SELECT title_zh, summary_zh FROM analyses WHERE article_id = a.id AND origin = 'model' ORDER BY id DESC LIMIT 1) an ON true
           ORDER BY p.added_at DESC"""
    ).fetchall()
    gaps: dict[str, list[dict[str, str]]] = {}
    for g in conn.execute("SELECT topic_id, need, reason, suggestion FROM topic_fetch_gaps ORDER BY layer").fetchall():
        gaps.setdefault(g["topic_id"], []).append({"need": g["need"], "reason": g["reason"], "suggestion": g["suggestion"]})
    out: list[dict[str, Any]] = []
    for r in rows:
        state = r["state"]
        if state in ("queued", "running") and r["started_at"] and now - r["started_at"] > STALE_AFTER:
            state = "interrupted"
        files = _folder_files(r["topic_id"]) if r["topic_id"] else []
        out.append(
            {
                "id": r["article_id"],
                "title": r["title_zh"] or r["title"],
                "originalTitle": r["title"],
                "summary": r["summary_zh"],
                "url": r["url"],
                "source": r["source_name"],
                "addedAt": r["added_at"],
                "topicId": r["topic_id"],
                "folder": f"topics/{r['topic_id']}" if r["topic_id"] else None,
                "state": state,
                "wanted": list(r["wanted"]),
                "startedAt": r["started_at"],
                "finishedAt": r["finished_at"],
                "result": r["result"],
                "files": files,
                "counts": {k: sum(1 for f in files if f["kind"] == k) for k in KINDS},
                "gaps": gaps.get(r["topic_id"] or "", []),
            }
        )
    return out


def start(pool, article_id: str, kinds: list[str] | None) -> str:
    """Asks for a download and returns at once. 'started' | 'busy' (one is already queued or running for it) | 'missing' (not in the area)."""
    wanted = wanted_of(kinds)
    with pool.connection() as conn:
        row = conn.execute(
            """UPDATE material_picks SET state = 'queued', wanted = %s, started_at = now(), finished_at = NULL, result = '{}'::jsonb
               WHERE article_id = %s AND (state NOT IN ('queued', 'running') OR started_at < now() - %s::interval) RETURNING article_id""",
            (wanted, article_id, STALE_AFTER),
        ).fetchone()
        if not row:
            exists = conn.execute("SELECT 1 FROM material_picks WHERE article_id = %s", (article_id,)).fetchone()
            return "busy" if exists else "missing"
    threading.Thread(target=_run, args=(pool, article_id, wanted), daemon=True, name=f"pick-{article_id[:8]}").start()
    return "started"


def _ensure_topic(conn, article_id: str) -> str:
    """The topic of a pick, made on first use through the same open-topic the CLI runs, from a candidate of the article itself."""
    row = conn.execute("SELECT topic_id FROM material_picks WHERE article_id = %s", (article_id,)).fetchone()
    if row and row["topic_id"]:
        return str(row["topic_id"])
    a = conn.execute(
        """SELECT a.title, an.title_zh FROM articles a
           LEFT JOIN LATERAL (SELECT title_zh FROM analyses WHERE article_id = a.id AND origin = 'model' ORDER BY id DESC LIMIT 1) an ON true WHERE a.id = %s""",
        (article_id,),
    ).fetchone()
    cid = f"cand_{article_id}"
    snapshot: dict[str, Any] = {"id": cid, "title": (a["title_zh"] or a["title"]) if a else article_id, "tier": None, "suggested_program": PROGRAM, "suggested_formats": []}
    conn.execute(
        "INSERT INTO candidate_state (candidate_id, status, first_listed_at, snapshot) VALUES (%s, 'picked', now(), %s) ON CONFLICT (candidate_id) DO NOTHING",
        (cid, Jsonb(snapshot)),
    )
    conn.execute("UPDATE candidate_state SET status = 'picked', updated_at = now() WHERE candidate_id = %s AND status = 'skipped'", (cid,))
    opened = open_topic(conn, [cid], program=PROGRAM, contents=False)
    conn.execute("UPDATE material_picks SET topic_id = %s WHERE article_id = %s", (opened.topic_id, article_id))
    return opened.topic_id


def _run(pool, article_id: str, wanted: list[str]) -> None:
    with _slots:
        with pool.connection() as conn:
            conn.execute("UPDATE material_picks SET state = 'running', started_at = now(), wanted = %s WHERE article_id = %s", (wanted, article_id))
        result: dict[str, Any] = {"errors": []}
        state = "done"
        try:
            with pool.connection() as conn:
                topic_id = _ensure_topic(conn, article_id)
                result["topicId"] = topic_id
                _download(conn, topic_id, wanted, result)
        except Exception as e:  # noqa: BLE001
            log.exception("download of pick %s failed", article_id)
            result["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
            state = "failed"
        if state == "done" and result["errors"] and not any(result.get(k) for k in ("article", "photo", "video")):
            state = "failed"
        with pool.connection() as conn:
            conn.execute(
                "UPDATE material_picks SET state = %s, finished_at = now(), result = %s WHERE article_id = %s", (state, Jsonb(result), article_id)
            )


def _download(conn, topic_id: str, wanted: list[str], result: dict[str, Any]) -> None:
    """Runs the steps asked for; one that fails is written down and the others still run."""
    from newsroom.fetch.assets import inventory  # noqa: PLC0415
    from newsroom.fetch.layers.l1_api import fetch_layer1  # noqa: PLC0415
    from newsroom.fetch.layers.l2_html import fetch_layer2  # noqa: PLC0415
    from newsroom.fetch.layers.l3_dl import fetch_layer3  # noqa: PLC0415
    from newsroom.fetch.topic import fetch_topic  # noqa: PLC0415

    def attempt(label: str, step):
        try:
            return step()
        except Exception as e:  # noqa: BLE001
            log.warning("pick %s: %s failed: %s", topic_id, label, e)
            result["errors"].append(f"{label}：{str(e)[:240]}")
            return None

    if "article" in wanted:
        r = attempt("原文", lambda: fetch_topic(conn, topic_id))
        if r is not None:
            result["article"] = {"fetched": len(r.fetched), "kept": len(r.kept), "failed": [f"{f['url']}：{f['reason']}" for f in r.failed]}
    skipped: list[str] = []
    downloaded: list[dict] = []
    failed: list[str] = []
    if "video" in wanted:
        l1 = attempt("影片（平臺）", lambda: fetch_layer1(conn, topic_id))
        have = {d["sourceId"] for d in l1.downloaded} if l1 else set()
        if l1:
            downloaded += l1.downloaded
            skipped += [f"{s['url']}：{s['reason']}" for s in l1.skipped if s["failed"]]
    else:
        have = set()
    page_kinds = frozenset(({"video", "gif"} if "video" in wanted else set()) | ({"image"} if "photo" in wanted else set()))
    if page_kinds:
        l2 = attempt("網頁上的圖片與影片", lambda: fetch_layer2(conn, topic_id, kinds=page_kinds))
        if l2:
            downloaded += l2.downloaded
            skipped += [f"{s['url']}：{s['reason']}" for s in l2.skipped if s["failed"]]
            have |= {d["sourceId"] for d in l2.downloaded if d["kind"] == "video"}
        tool_kinds = frozenset(({"video"} if "video" in wanted else set()) | ({"image"} if "photo" in wanted else set()))
        l3 = attempt("下載器", lambda: fetch_layer3(conn, topic_id, have_video=have, kinds=tool_kinds))
        if l3:
            downloaded += l3.downloaded
            failed += [f"{f['url']}：{f['message']}" for f in l3.failed]
    if page_kinds:
        stock = attempt("盤點", lambda: inventory(conn, topic_id))
        if stock is not None:
            result["gaps"] = [{"need": g.need, "reason": g.reason, "suggestion": g.suggestion} for g in stock.gaps]
    if "photo" in wanted:
        result["photo"] = {"downloaded": sum(1 for d in downloaded if d["kind"] == "image")}
    if "video" in wanted:
        result["video"] = {"downloaded": sum(1 for d in downloaded if d["kind"] in ("video", "gif"))}
    result["notes"] = [*skipped, *failed][:12]
