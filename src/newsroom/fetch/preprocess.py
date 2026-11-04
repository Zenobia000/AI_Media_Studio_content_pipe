"""preprocess (REEL-FR-003, SUB-FR-001): the media of a topic made fit to cut together. Video and GIF become 30 fps constant-frame-rate,
yuv420p, H.264 (so picture and sound stay in step); a recording becomes 48 kHz WAV with the silence taken off both ends and its loudness
set to -14 LUFS. The result goes to media/proc/ named after the original (its asset id), what is already there is not made again, and
the original is never touched (media/raw is read-only)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from newsroom.collect.topics import OpenTopicError
from newsroom.core.workspace import workspace_dir
from newsroom.fetch.assets import Asset, inventory
from newsroom.fetch.ffmpeg import ToolMissing, ffmpeg, ffprobe_json

TARGET_FPS = 30
DEFAULT_LUFS = -14
TRUE_PEAK = -1.5
LRA = 11
SILENCE = "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB:detection=peak"
TRIM_BOTH_ENDS = f"{SILENCE},areverse,{SILENCE},areverse"


def measure_lufs(file: str) -> float:
    """The integrated loudness (LUFS) of a file, measured the way broadcasters do (EBU R128)."""
    report = ffmpeg(["-nostats", "-i", file, "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = report[report.rfind("Integrated loudness") :]
    m = re.search(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", summary)
    if not m:
        raise RuntimeError("量不到響度（這個檔案可能沒有聲音）")
    return float(m.group(1))


def _conforms(file: str) -> bool:
    """Whether the video stream already is H.264, yuv420p, 30 fps constant (frame rate and average agree) with even sides."""
    try:
        info = ffprobe_json(["-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,r_frame_rate,avg_frame_rate,width,height", file])
        v = (info.get("streams") or [None])[0]
    except Exception:  # noqa: BLE001
        return False
    return (
        bool(v)
        and v["codec_name"] == "h264"
        and v["pix_fmt"] == "yuv420p"
        and v["r_frame_rate"] == f"{TARGET_FPS}/1"
        and v["avg_frame_rate"] == f"{TARGET_FPS}/1"
        and int(v["width"]) % 2 == 0
        and int(v["height"]) % 2 == 0
    )


def normalize_video(src: str, dest: str) -> None:
    """A video or GIF as 30 fps CFR yuv420p H.264. Both sides are made even, which yuv420p needs."""
    # Footage that already is what we want is wrapped as it is (seconds, not minutes, and no loss from encoding twice).
    if _conforms(src):
        ffmpeg(["-i", src, "-c", "copy", "-movflags", "+faststart", dest])
        return
    ffmpeg(
        [
            "-i",
            src,
            "-vf",
            f"fps={TARGET_FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-r",
            str(TARGET_FPS),
            "-fps_mode",
            "cfr",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            dest,
        ]
    )


def normalize_narration(src: str, dest: str, lufs: float = DEFAULT_LUFS) -> None:
    """A recording as 48 kHz 16-bit WAV, silence off both ends, two-pass loudness normalisation to `lufs`."""
    norm = f"loudnorm=I={lufs}:TP={TRUE_PEAK}:LRA={LRA}"
    first = ffmpeg(["-i", src, "-af", f"{TRIM_BOTH_ENDS},{norm}:print_format=json", "-f", "null", "-"])
    m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', first)
    if not m:
        raise RuntimeError("量不到響度（這個檔案可能沒有聲音）")
    j = json.loads(m.group(0))
    measured = f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}"
    ffmpeg(["-i", src, "-af", f"{TRIM_BOTH_ENDS},{norm}:{measured}:linear=true", "-ar", "48000", "-c:a", "pcm_s16le", dest])


@dataclass
class PreprocessResult:
    topic_id: str
    made: list[dict] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # already made earlier
    failed: list[dict] = field(default_factory=list)


def preprocess(conn, topic_id: str, video: bool = True, audio: bool = True, lufs: float = DEFAULT_LUFS) -> PreprocessResult:
    """Normalises every video, GIF and recording of media/raw and media/gen that has not been normalised yet."""
    stock = inventory(conn, topic_id)
    d = workspace_dir() / "topics" / topic_id
    proc = d / "media" / "proc"
    result = PreprocessResult(topic_id)

    def wanted(a: Asset) -> str | None:
        if not re.match(r"^media/(raw|gen)/", a.path):
            return None
        if a.type in ("video", "gif") and video:
            return "video"
        if a.type == "audio" and audio:
            return "narration"
        return None

    for asset in stock.assets:
        kind = wanted(asset)
        if not kind:
            continue
        ext = "mp4" if kind == "video" else "wav"
        out = proc / f"{asset.asset_id}.{ext}"
        if out.exists():
            result.kept.append(asset.path)
            continue
        proc.mkdir(parents=True, exist_ok=True)
        part = proc / f".{asset.asset_id}.part.{ext}"
        try:
            (normalize_video if kind == "video" else normalize_narration)(str(d / asset.path), str(part), *([] if kind == "video" else [lufs]))
            part.replace(out)
            result.made.append({"from": asset.path, "path": out.relative_to(d).as_posix(), "kind": kind})
        except (OpenTopicError, ToolMissing):
            raise
        except Exception as e:  # noqa: BLE001
            Path(part).unlink(missing_ok=True)
            result.failed.append({"from": asset.path, "reason": str(e)})
    return result
