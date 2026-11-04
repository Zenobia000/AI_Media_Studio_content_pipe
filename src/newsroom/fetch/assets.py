"""assets (FET-FR-007, FET-FR-009): the media of a topic as assets.json (IF-005) and media.md. The files under media/raw, media/gen and
media/proc are the truth; the table remembers where downloaded ones came from and which have been deleted. A file keeps its id (SHA-256)
when moved or renamed."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from newsroom.collect.topics import FORMATS, OpenTopicError, block, replace_block
from newsroom.core.workspace import taipei_iso, workspace_dir, write_atomic
from newsroom.fetch.ffmpeg import ToolMissing, ffprobe_json
from newsroom.fetch.scenes import SceneDetector, detect_scenes

EXTENSIONS = {
    **dict.fromkeys(["mp4", "mov", "webm", "mkv", "avi", "m4v"], "video"),
    "gif": "gif",
    **dict.fromkeys(["wav", "mp3", "m4a", "aac", "flac", "ogg"], "audio"),
    **dict.fromkeys(["png", "jpg", "jpeg", "webp", "avif"], "image"),
    **dict.fromkeys(["pdf", "md", "txt"], "document"),
}
FOLDERS = [("raw", "self"), ("gen", "generated"), ("proc", "derived")]


@dataclass
class Asset:
    asset_id: str
    path: str  # relative to the topic folder
    type: str  # video | image | gif | audio | screenshot | document
    origin: str  # official | self | third_party | generated | derived
    source_id: str | None = None
    source_url: str | None = None
    fetch_layer: int | None = None
    fetch_method: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    usable_ranges_ms: list[list[int]] | None = None
    notes: str = ""
    usage_note: str = ""


@dataclass
class Gap:
    need: str
    reason: str
    suggestion: str


@dataclass
class InventoryResult:
    topic_id: str
    assets: list[Asset]
    gaps: list[Gap]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def type_of(file: str | Path) -> str | None:
    t = EXTENSIONS.get(Path(file).suffix[1:].lower())
    if not t:
        return None
    return "screenshot" if t == "image" and re.match(r"^(screenshot|截圖|截图)", Path(file).name, re.I) else t


def probe(file: str | Path) -> dict:
    """Length, size, frame rate and sound of a media file, read with ffprobe."""
    info = ffprobe_json(["-show_format", "-show_streams", str(file)])
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    num, _, den = ((video or {}).get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den) * 100) / 100 if float(den) else None
    except ValueError:
        fps = None
    try:
        seconds = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        seconds = 0.0
    return {
        "duration_ms": int(seconds * 1000 + 0.5) if seconds > 0 else None,
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "fps": fps or None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _files_under(directory: Path) -> list[Path]:
    out: list[Path] = []
    if not directory.exists():
        return out
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.name.endswith(".tmp"):
            continue
        out.extend(_files_under(entry) if entry.is_dir() else [entry])
    return sorted(out)


def gaps_for(formats: list[str], assets: list[Asset], voiceover: bool = False) -> list[Gap]:
    """What each format of the topic needs and does not have (FET-FR-007 step 5)."""
    gaps: list[Gap] = []

    def has(*types: str) -> bool:
        return any(a.type in types for a in assets)

    if "reel" in formats:
        if not has("video", "gif", "image", "screenshot"):
            gaps.append(Gap("visual", "reel 沒有任何畫面素材", "錄 30 秒操作畫面或找官方影片，放進 media/raw/ 後再執行 assets；也可以改做輪播"))
        if voiceover and not has("audio"):
            gaps.append(Gap("voiceover", "reel 還沒有旁白錄音", "錄好旁白（wav 或 m4a）放進 media/raw/"))
    if "carousel" in formats and not has("image", "screenshot"):
        gaps.append(Gap("hero-image", "輪播沒有主視覺圖片", "放一張主視覺進 media/raw/；沒有的話可以用輪播模板代替"))
    return gaps


def _length(ms: int | None) -> str:
    return "—" if ms is None else f"{ms // 60000}:{str(ms % 60000 // 1000).zfill(2)}"


def _cell(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("|", "／")).strip() or "—"


def media_markdown(assets: list[Asset], gaps: list[Gap]) -> str:
    def how(a: Asset) -> str:
        return f"{a.origin}（L{a.fetch_layer} {a.fetch_method or ''}）" if a.fetch_layer else a.origin

    rows = [
        f"| {_cell(a.path)} | {a.type} | {_length(a.duration_ms)} | {_cell(a.source_url or '自己放入')} | {_cell(how(a))} | {_cell(a.usage_note)} |"
        for a in assets
    ]
    table = "\n".join(["| 檔名 | 型別 | 長度 | 來源 | 取得方式 | 使用註記 |", "|---|---|---|---|---|---|", *rows]) if assets else "目前沒有素材。"
    missing = "\n".join(f"- **{g.need}**：{g.reason}。建議：{g.suggestion}" for g in gaps) if gaps else "沒有缺口。"
    return f"## 素材\n\n{table}\n\n## 缺口與建議\n\n{missing}"


def inventory(conn, topic_id: str, now: datetime | None = None, voiceover: bool = False, detect: SceneDetector | None = None) -> InventoryResult:
    now = now or datetime.now(timezone.utc)
    if not conn.execute("SELECT topic_id FROM topics WHERE topic_id = %s", (topic_id,)).fetchone():
        raise OpenTopicError(f"找不到 topic {topic_id}")
    d = workspace_dir() / "topics" / topic_id
    warnings: list[str] = []
    known = {
        k["asset_id"]: k
        for k in conn.execute(
            "SELECT asset_id, origin, source_id, source_url, fetch_layer, fetch_method, deleted_at FROM topic_assets WHERE topic_id = %s", (topic_id,)
        ).fetchall()
    }
    # What a person wrote about an asset in the last assets.json stays.
    try:
        previous = json.loads((d / "assets.json").read_text(encoding="utf-8")).get("assets", [])
    except (OSError, ValueError):
        previous = []
    written = {a["asset_id"]: a for a in previous}

    assets: list[Asset] = []
    seen: set[str] = set()
    for folder, default_origin in FOLDERS:
        for file in _files_under(d / "media" / folder):
            kind = type_of(file)
            rel = file.relative_to(d).as_posix()
            if not kind:
                warnings.append(f"不認得的檔案型別，已略過：{rel}")
                continue
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            aid = f"ast_{digest[:12]}"
            if aid in seen:
                warnings.append(f"{rel} 的內容和另一個檔案完全相同，只列一次")
                continue
            try:
                probed = {"duration_ms": None, "width": None, "height": None, "fps": None, "has_audio": None} if kind == "document" else probe(file)
            except ToolMissing:
                raise
            except Exception:  # noqa: BLE001 — a file ffprobe cannot read is left out and said so
                warnings.append(f"讀不了這個檔案，沒有列進素材（可能已損壞）：{rel}")
                continue
            seen.add(aid)
            # A still picture has no length (ffprobe reports a single frame as a clip of a few milliseconds).
            media = {**probed, "duration_ms": None, "fps": None} if kind in ("image", "screenshot") else probed
            k = known.get(aid)
            before = written.get(aid) or {}
            usable = before.get("usable_ranges_ms")
            if usable is None and kind == "audio" and media["duration_ms"]:
                usable = [[0, media["duration_ms"]]]
            if usable is None and kind == "video" and media["duration_ms"]:
                try:
                    usable = [list(r) for r in (detect or detect_scenes)(str(file), media["duration_ms"])]
                except Exception as e:  # noqa: BLE001
                    usable = [[0, media["duration_ms"]]]
                    warnings.append(f"場景偵測失敗，整段視為可用：{rel}（{str(e)[:80]}）")
            layer = k["fetch_layer"] if k else None
            made_from = f"由 {file.name.split('.')[0]} 前處理而來" if folder == "proc" and re.match(r"^ast_[0-9a-f]{12}\.", file.name) else ""
            assets.append(
                Asset(
                    aid,
                    rel,
                    kind,
                    k["origin"] if k else default_origin,
                    k["source_id"] if k else None,
                    k["source_url"] if k else None,
                    layer,
                    k["fetch_method"] if k else None,
                    **media,
                    usable_ranges_ms=usable,
                    notes=before.get("notes", made_from),
                    usage_note=before["usage_note"]
                    if "usage_note" in before
                    else ("官方網頁上的檔案，使用時標註來源" if layer == 2 else "第三方來源，確認授權後再使用並標註來源" if layer == 3 else ""),
                )
            )
            conn.execute(
                """INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, sha256) VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, type = EXCLUDED.type, deleted_at = NULL""",
                (topic_id, aid, rel, kind, k["origin"] if k else default_origin, digest),
            )
    removed = [i for i, k in known.items() if i not in seen and not k["deleted_at"]]
    if removed:
        conn.execute("UPDATE topic_assets SET deleted_at = %s WHERE topic_id = %s AND asset_id = ANY(%s)", (now, topic_id, removed))
    added = [a.asset_id for a in assets if a.asset_id not in known or known[a.asset_id]["deleted_at"]]

    contents = d / "contents"
    formats = [n.name.split("-")[0] for n in contents.iterdir()] if contents.exists() else []
    formats = [f for f in formats if f in FORMATS]
    # The format gaps, and the ones a download left (a file too big, a page that forbids robots): both go to the person.
    fetch_gaps = conn.execute(
        "SELECT need, reason, suggestion FROM topic_fetch_gaps WHERE topic_id = %s ORDER BY layer, reason", (topic_id,)
    ).fetchall()
    gaps = [*gaps_for(list(dict.fromkeys(formats)), assets, voiceover), *(Gap(g["need"], g["reason"], g["suggestion"]) for g in fetch_gaps)]

    write_atomic(
        d / "assets.json",
        json.dumps(
            {
                "schema_version": 1,
                "topic_id": topic_id,
                "updated_at": taipei_iso(now),
                "assets": [asdict(a) for a in assets],
                "gaps": [asdict(g) for g in gaps],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    media_file = d / "media.md"
    existing = media_file.read_text(encoding="utf-8") if media_file.exists() else None
    body = media_markdown(assets, gaps)
    write_atomic(media_file, f"# 素材\n\n{block('media', body)}\n" if existing is None else replace_block(existing, "media", body))
    return InventoryResult(topic_id, assets, gaps, added, removed, warnings)
