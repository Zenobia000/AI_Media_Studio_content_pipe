"""Heat grading (COL-FR-013): a candidate's signals become a score, a tier S, A or B, the breakdown of the score, and the program
and formats to suggest. Nothing here is random or reads the clock: the same input and the same configuration give the same answer,
so a grade can be explained and re-computed. The weights are in heat.config.json."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SIGNALS = ("resonance", "x_velocity", "hn_velocity", "repo_growth", "newsletter", "source_weight", "local_gap")
LABELS = {
    "resonance": "共振",
    "x_velocity": "X 互動",
    "hn_velocity": "HN 增速",
    "repo_growth": "專案增速",
    "newsletter": "電子報",
    "source_weight": "來源權重",
    "local_gap": "在地缺口",
}
HEAT_CONFIG_FILE = Path(__file__).with_name("heat.config.json")


def load_heat_config(file: Path | str = HEAT_CONFIG_FILE) -> dict[str, Any]:
    return json.loads(Path(file).read_text(encoding="utf-8"))


def js_round(x: float) -> int:
    """Math.round: halves go up (Python's round() goes to even), so scores match the TypeScript version."""
    return math.floor(x + 0.5)


def _round1(x: float) -> float:
    return js_round(x * 10) / 10


@dataclass
class HeatInput:
    """What is known about one candidate. A signal that is not known is None and counts as nothing."""

    resonance_sources: int = 0
    hn_points_per_hour: float | None = None
    stars_per_hour: float | None = None
    hf_likes_per_hour: float | None = None
    hf_upvotes: float | None = None
    newsletter_mentions: float | None = None
    best_tier: str | None = None
    x_velocity: float | None = None
    local_gap: bool | None = None
    event_ended: bool = False
    identity_keys: tuple[str, ...] = ()


@dataclass
class Contribution:
    signal: str
    enabled: bool
    raw: float | None
    value: float
    weight: float
    points: float


@dataclass
class HeatResult:
    score: int
    tier: str
    breakdown: list[Contribution]
    reasons: list[str]
    program: str
    formats: list[str] = field(default_factory=list)


def _raw_values(i: HeatInput, config: dict[str, Any]) -> dict[str, float | None]:
    repo = [v for v in (i.stars_per_hour, i.hf_likes_per_hour, None if i.hf_upvotes is None else i.hf_upvotes / 5) if v is not None]
    return {
        "resonance": i.resonance_sources,
        "x_velocity": i.x_velocity,
        "hn_velocity": i.hn_points_per_hour,
        "repo_growth": max(repo) if repo else None,
        "newsletter": i.newsletter_mentions,
        "source_weight": config["sourceWeights"].get(i.best_tier) if i.best_tier else None,
        "local_gap": None if i.local_gap is None else (1 if i.local_gap else 0),
    }


def program_for(keys: tuple[str, ...] | list[str] = ()) -> str:
    if any(k.startswith("arxiv:") for k in keys):
        return "ai-engineering"
    if any(k.startswith("github:") or "huggingface.co/" in k for k in keys):
        return "tool-review"
    return "ai-today"


def score_heat(inp: HeatInput, config: dict[str, Any]) -> HeatResult:
    raw = _raw_values(inp, config)
    sig = config["signals"]
    # A signal that is switched off has weight 0 and is left out of the 100 points altogether.
    total = sum(sig[n]["weight"] for n in SIGNALS if sig[n]["enabled"])
    breakdown: list[Contribution] = []
    for name in SIGNALS:
        c = sig[name]
        value = min(1.0, max(0.0, raw[name] / c["cap"])) if c["enabled"] and raw[name] is not None else 0.0
        weight = c["weight"] if c["enabled"] else 0
        breakdown.append(
            Contribution(
                name,
                c["enabled"],
                raw[name],
                _round1(value * 1000) / 1000,
                weight,
                _round1(100 * weight * value / total) if total else 0,
            )
        )
    score = js_round(sum(b.points for b in breakdown))

    reasons: list[str] = []
    s_cfg, a_cfg = config["tiers"]["S"], config["tiers"]["A"]
    hours = config["windows"]["resonanceHours"]
    tier = "B"
    if inp.event_ended:
        reasons.append("預告過的發表會剛結束")
    if inp.resonance_sources >= s_cfg["minResonance"]:
        reasons.append(f"{hours} 小時內 {inp.resonance_sources} 個來源共振")
    if score >= s_cfg["minScore"]:
        reasons.append(f"熱度分數 {score} 達到 S 級（{s_cfg['minScore']}）")
    if reasons:
        tier = "S"
    else:
        if inp.resonance_sources >= a_cfg["minResonance"]:
            reasons.append(f"{hours} 小時內 {inp.resonance_sources} 個來源共振")
        if score >= a_cfg["minScore"]:
            reasons.append(f"熱度分數 {score} 達到 A 級（{a_cfg['minScore']}）")
        if reasons:
            tier = "A"
        else:
            reasons.append(f"熱度分數 {score}，沒有達到 A 級（{a_cfg['minScore']}）")
    return HeatResult(score, tier, breakdown, reasons, program_for(inp.identity_keys), list(config["mix"][tier]["formats"]))


def describe_breakdown(result: HeatResult) -> str:
    """ "共振 30＋HN 增速 11.5…": the points each signal gave, for the inbox."""
    parts = [f"{LABELS[b.signal]} {_num(b.points)}" for b in result.breakdown if b.enabled and b.points > 0]
    return f"{result.score} 分" + (f"（{'＋'.join(parts)}）" if parts else "")


def _num(x: float) -> str:
    """A number the way JavaScript prints it: 30 not 30.0."""
    return str(int(x)) if float(x).is_integer() else str(x)
