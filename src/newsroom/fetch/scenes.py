"""Usable ranges (FET-FR-008): where a video changes scene, so a draft can pick a part of it instead of all of it. The ranges are written to
usable_ranges_ms; when the detection fails the whole video is usable and a warning says so."""

from __future__ import annotations

import re
from collections.abc import Callable

from newsroom.fetch.ffmpeg import ffmpeg

SCENE_THRESHOLD = 0.3  # how different two frames must be (0 to 1) to count as a cut
MIN_SCENE_MS = 500  # a part shorter than this is a flash, not a scene: it joins the one before it

SceneDetector = Callable[[str, int], list[tuple[int, int]]]


def detect_scenes(file: str, duration_ms: int) -> list[tuple[int, int]]:
    """The scenes of a video as (start, end) in milliseconds, covering it from 0 to its length."""
    report = ffmpeg(["-i", file, "-an", "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"])
    cuts = [int(float(m) * 1000 + 0.5) for m in re.findall(r"pts_time:(\d+(?:\.\d+)?)", report)]
    return ranges_from_cuts(cuts, duration_ms)


def ranges_from_cuts(cuts: list[int], duration_ms: int) -> list[tuple[int, int]]:
    """Cuts (ms) to the ranges between them; a cut too close to the previous one or to the end is dropped."""
    kept: list[int] = []
    last = 0
    for cut in sorted(cuts):
        if cut - last >= MIN_SCENE_MS and duration_ms - cut >= MIN_SCENE_MS:
            kept.append(cut)
            last = cut
    edges = [0, *kept, duration_ms]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
