"""IF-001 inbox file (docs/spec/07_interfaces.md): the program writes only the managed block, the person edits the checkbox, 節目,
格式 and 備註. Regenerating keeps what the person wrote. Also which candidates a day lists (COL-FR-015, cross-day)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from newsroom.engine.traditional import plain_for_display

BEGIN = "<!-- newsroom:begin candidates -->"
END = "<!-- newsroom:end candidates -->"
HEALTH_BEGIN = "<!-- newsroom:begin collect-health -->"
HEALTH_END = "<!-- newsroom:end collect-health -->"
TIER_TITLES = {"S": "S 級", "A": "A 級", "B": "B 級"}
CARRY_MS = 48 * 3600_000


@dataclass
class Manual:
    note: str = ""
    program: str = ""
    formats: str = ""


@dataclass
class Candidate:
    id: str  # cand_<story public id>: the program maps a tick back to its story by it, so it never changes.
    title: str
    tier: str
    participant_count: int
    source_names: list[str]
    first_seen: str
    suggested_program: str
    suggested_formats: list[str]
    manual: Manual | None = None  # Added by hand: listed first, under its own heading
    carried: str | None = None  # "昨日" | "已勾選"
    signals: str | None = None  # "HN 412 分、120 則留言"
    local: str | None = None  # 在地: whether the Chinese-speaking side has covered it
    score: int | None = None
    heat: str | None = None
    heat_reason: str | None = None


@dataclass
class Annotation:
    checked: bool = False
    program: str = ""
    formats: str = ""
    note: str = ""


class ManagedBlockError(Exception):
    def __init__(self) -> None:
        super().__init__("E_MANAGED_BLOCK: inbox 受管區塊損壞，不覆寫")


def tier_of(participant_count: int) -> str:
    """Interim grading by independent participants until the heat tiers replace it."""
    return "S" if participant_count >= 5 else "A" if participant_count >= 3 else "B"


def _managed_block(text: str) -> str | None:
    start, end = text.find(BEGIN), text.find(END)
    return None if start < 0 or end < start else text[start + len(BEGIN) : end]


_HEAD = re.compile(r"^- \[( |x|X)\] .*`(cand_[A-Za-z0-9]+)`\s*$")
_FIELD = re.compile(r"^ {2}- (節目|格式|備註)：(.*)$")
_SPACES = re.compile(r"\s+")
_LINE_BREAK = re.compile(r"\s*\n\s*")


def parse_annotations(text: str) -> dict[str, Annotation]:
    """Reads the person's ticks, 節目, 格式 and 備註 back, by candidate id."""
    out: dict[str, Annotation] = {}
    block = _managed_block(text)
    if block is None:
        return out
    current: Annotation | None = None
    for line in re.split(r"\r?\n", block):
        head = _HEAD.match(line)
        if head:
            current = Annotation(checked=head.group(1) != " ")
            out[head.group(2)] = current
            continue
        f = _FIELD.match(line) if current else None
        if f and current:
            value = f.group(2).strip()
            if f.group(1) == "節目":
                current.program = value
            elif f.group(1) == "格式":
                current.formats = value
            else:
                current.note = value
    return out


def render_block(candidates: list[Candidate], notes: dict[str, Annotation]) -> str:
    lines = [BEGIN]
    groups = [("手動新增", [c for c in candidates if c.manual])] + [
        (TIER_TITLES[t], [c for c in candidates if not c.manual and c.tier == t]) for t in ("S", "A", "B")
    ]
    for heading, group in groups:
        if not group:
            continue
        lines.append(f"## {heading}")
        for c in group:
            kept = notes.get(c.id)
            sources = "、".join([*c.source_names[:3], *([c.signals] if c.signals else [])])
            carried = f"｜{c.carried}" if c.carried else ""
            # shown in Traditional when TRADITIONAL_DISPLAY is on (the stored title is untouched)
            lines.append(f"- [{'x' if kept and kept.checked else ' '}] **{plain_for_display(c.title)}** `{c.id}`")
            lines.append(f"  - 共振 {c.participant_count} 源｜{sources}｜首次出現 {c.first_seen}{carried}")
            if c.heat:
                lines.append(f"  - 熱度：{c.heat}" + (f"｜{c.heat_reason}" if c.heat_reason else ""))
            if c.local:
                lines.append(f"  - 在地：{c.local}")
            lines.append(f"  - 建議：{c.suggested_program}｜{', '.join(c.suggested_formats)}")
            lines.append(f"  - 節目：{(kept.program if kept else '') or (c.manual.program if c.manual else '')}")
            lines.append(f"  - 格式：{(kept.formats if kept else '') or (c.manual.formats if c.manual else '')}")
            manual_note = _LINE_BREAK.sub("；", c.manual.note) if c.manual else ""
            lines.append(f"  - 備註：{(kept.note if kept else '') or manual_note}")
    lines.append(END)
    return "\n".join(lines)


def render_file(date: str, candidates: list[Candidate], existing: str | None, earlier: dict[str, Annotation] | None = None) -> str:
    """A new file, or the existing one with only its managed block replaced. Raises ManagedBlockError when the block is damaged."""
    # What the person wrote on earlier days travels with a carried candidate; today's file overrides it.
    notes = {**(earlier or {}), **(parse_annotations(existing) if existing else {})}
    block = render_block(candidates, notes)
    if existing is None:
        return f"# {date} 候選\n\n{block}\n"
    start, end = existing.find(BEGIN), existing.find(END)
    if start < 0 or end < start:
        raise ManagedBlockError()
    return existing[:start] + block + existing[end + len(END) :]


@dataclass
class FailingSource:
    name: str
    fail_count: int
    last_error: str | None = None
    last_ok_at: str | None = None  # "YYYY-MM-DD HH:mm", or None when it never succeeded


def render_health(failing: list[FailingSource]) -> str:
    if failing:
        rows = []
        for f in failing:
            error = f"｜{_SPACES.sub(' ', f.last_error)[:120]}" if f.last_error else ""
            rows.append(f"- **{f.name}**：連續失敗 {f.fail_count} 次｜最後成功：{f.last_ok_at or '從未成功'}{error}")
        body = "\n".join(["## 收集狀況", *rows])
    else:
        body = "## 收集狀況\n所有來源最近一次收集都正常。"
    return f"{HEALTH_BEGIN}\n{body}\n{HEALTH_END}"


def with_health(text: str, failing: list[FailingSource]) -> str:
    """The inbox with its collection-health block (COL-FR-004), replaced where it is or added at the foot."""
    block = render_health(failing)
    start, end = text.find(HEALTH_BEGIN), text.find(HEALTH_END)
    if start >= 0 and end > start:
        return text[:start] + block + text[end + len(HEALTH_END) :]
    return f"{text.rstrip()}\n\n{block}\n"


# ── cross-day ───────────────────────────────────────────────────────────────────────────


@dataclass
class StateRow:
    id: str
    status: str  # new | picked | opened | skipped
    first_listed_at: datetime
    snapshot: Candidate  # as it was last listed, so a day it has dropped out of the ranking it can still be written


@dataclass
class Plan:
    listed: list[Candidate] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)  # candidates whose 48 hours are over without a tick


def plan_inbox(fresh: list[Candidate], rows: list[StateRow], now: datetime, today: str, day: Callable[[datetime], str]) -> Plan:
    """A candidate not ticked the day before and within 48 hours since first listed is brought along ("昨日"); after that it is
    skipped. A ticked one stays until opened as a topic. Skipped and opened ones are not listed again."""
    by_id = {r.id: r for r in rows}

    def expired(r: StateRow) -> bool:
        return r.status == "new" and (now - r.first_listed_at).total_seconds() * 1000 > CARRY_MS

    def gone(cid: str) -> bool:
        r = by_id.get(cid)
        return r is not None and (r.status in ("skipped", "opened") or expired(r))

    def carried_from(r: StateRow) -> str | None:
        return None if day(r.first_listed_at) == today else "已勾選" if r.status == "picked" else "昨日"

    listed: list[Candidate] = []
    for c in fresh:
        if gone(c.id):
            continue
        r = by_id.get(c.id)
        listed.append(replace(c, carried=carried_from(r)) if r and carried_from(r) else c)
    shown = {c.id for c in listed}
    for r in rows:
        if r.id in shown or gone(r.id):
            continue
        if r.status in ("new", "picked"):
            listed.append(replace(r.snapshot, carried=carried_from(r)))
    return Plan(listed, [r.id for r in rows if expired(r)])
