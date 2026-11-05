"""The relation between a report and candidate facts: prompts, schemas, candidate descriptions and the pure decision rules. group.py does the reading and writing
around them. Port of events/relate.ts.

A judgement is one of four relations. SAME_OCCURRENCE: the same real-world happening (one launch with its models, prices and details; the same ruling, report,
incident or interview from any outlet). SAME_STORY: a direct development of that happening (teaser and launch, launch and a review or a third-party listing, an
incident and the response). UNRELATED: a different happening, even for the same product or company. ROUNDUP: one side is a multi-topic digest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from newsroom.engine._readability_p1 import js_trim
from newsroom.engine.lib.text import js_slice
from newsroom.engine.llm import SchemaError, js_length
from newsroom.engine.prompts import prompt_text, prompt_version
from newsroom.engine.sources.dates import describe_publication_time

GROUP_BODY_CHARS = 10_000  # saved original evidence per report, shared by rendering and selected-background retrieval
RELATIONS = ("SAME_OCCURRENCE", "SAME_STORY", "UNRELATED", "ROUNDUP")
TIE_MIN_CONFIDENCE = 0.8  # how sure a relation must be before a report counts as evidence that two stories are one
TIE_RELATIONS = ("SAME_OCCURRENCE", "SAME_STORY")  # the relations that tie a report to a candidate
STORY_REVIEW_MIN_CONFIDENCE = 0.75  # the review model's bar for merging two stories
_CANDIDATE_ID = re.compile(r"C[1-9][0-9]*\Z")


def relate_prompt_version() -> str:
    return prompt_version("group-pair", "group-signal")


def batch_prompt_version() -> str:
    return prompt_version("group-batch")


def batch_system() -> str:
    return prompt_text("group-batch")


def pair_system() -> str:
    return prompt_text("group-pair")


def signal_system() -> str:
    return prompt_text("group-signal")


@dataclass
class Verdict:
    relation: str
    confidence: float
    note: str = ""


@dataclass
class CandidateView:
    fact_id: int
    story_id: int
    fact_title: str
    members: int
    story_root: bool
    score: float
    report: dict
    selected: bool = False
    selected_report: dict | None = field(default=None)


# ── answer shapes ───────────────────────────────────────────────────────────────


def _object(data) -> dict:
    if not isinstance(data, dict):
        raise SchemaError("expected an object")
    return data


def _number(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise SchemaError("confidence must be a number from 0 to 1")
    return v


def _relation(v) -> str:
    if v not in RELATIONS:
        raise SchemaError("relation must be one of " + ", ".join(RELATIONS))
    return v


def _caught(v, limit: int = 400) -> str:
    return v if isinstance(v, str) and js_length(v) <= limit else ""


def _decision(v, note: bool) -> dict:
    d = _object(v)
    if not isinstance(d.get("id"), str) or not _CANDIDATE_ID.match(d["id"]):
        raise SchemaError("decision id must look like C1")
    out = {"id": d["id"], "relation": _relation(d.get("relation")), "confidence": _number(d.get("confidence"))}
    if note:
        out["note"] = _caught(d.get("note"))
    return out


def _decisions(d: dict, note: bool, count: int | None) -> list[dict]:
    if not isinstance(d.get("decisions"), list):
        raise SchemaError("decisions must be a list")
    decisions = [_decision(x, note) for x in d["decisions"]]
    if count is not None and not complete_decisions(decisions, count):
        raise SchemaError("Every candidate requires exactly one decision")
    return decisions


def batch_schema(count: int):
    """The answer to one new report against `count` candidate facts: a relation for each, and whether the report adds value."""

    def parse(data) -> dict:
        d = _object(data)
        selection = _object(d.get("selection"))
        reason = selection.get("reason")
        if not isinstance(selection.get("addsValue"), bool) or not isinstance(reason, str) or not 1 <= js_length(js_trim(reason)) <= 400:
            raise SchemaError("selection needs addsValue and a reason")
        return {
            "query": _caught(d.get("query")),
            "decisions": _decisions(d, True, count),
            "selection": {"addsValue": selection["addsValue"], "reason": js_trim(reason)},
        }

    return parse


def pair_schema(data) -> dict:
    d = _object(data)
    return {"a": _caught(d.get("a")), "b": _caught(d.get("b")), "relation": _relation(d.get("relation")), "difference": _caught(d.get("difference")),
            "confidence": _number(d.get("confidence"))}  # fmt: skip


def signal_schema(count: int):
    def parse(data) -> dict:
        return {"decisions": _decisions(_object(data), False, count)}

    return parse


# ── descriptions ────────────────────────────────────────────────────────────────


def describe_report(r: dict, label: str, extra: str = "", summary_limit: int = 360) -> str:
    f = r.get("frame")
    scope = r.get("scope")
    lines = [
        f"【{label}】{extra}",
        f"標題：{r['title']}",
        f"來源：{r['source']}{'（當事方/官方）' if r['firstParty'] else ''}｜發布時間：{describe_publication_time(r.get('at'), r.get('publicationTime'))}",
        f"摘要：{js_slice(r.get('summary') or '', 0, summary_limit) or '（無）'}",
        f"資料範圍：{'多個獨立發生的綜合稿' if scope == 'composite' else '單一具體發生或議題'}" if scope and scope != "unknown" else None,
        f"事實要素：主體={f.get('subject') or '?'}；動作={f.get('action') or '?'}；物件={f.get('object') or '?'}；日期={f.get('occurredAt') or '未知'}"
        if f and (f.get("subject") or f.get("action") or f.get("object"))
        else None,
        f"【原文證據】\n{js_slice(r['sourceText'], 0, GROUP_BODY_CHARS)}\n【原文證據結束】" if r.get("sourceText") else None,
    ]
    return "\n".join(x for x in lines if x)


def candidate_key(index: int) -> str:
    return f"C{index + 1}"


def batch_user(query: dict, cands: list[CandidateView], query_label: str = "新報道", reading: list[dict] | None = None) -> str:
    reading = reading or []
    parts = []
    for i, c in enumerate(cands):
        lines = [describe_report(c.report, f"候選 {candidate_key(i)}", f"（該事實已有 {c.members} 篇報道；事實標題：{c.fact_title}）")]
        if query_label == "新報道":
            lines.append(
                describe_report(c.selected_report, "已公開精選", "（增量只與這篇已展示的內容比較）")
                if c.selected and c.selected_report
                else "【尚無公開精選】該候選只用於身份判斷。"
            )
        parts.append("\n".join(lines))
    background = "\n\n".join(
        "\n".join(
            x
            for x in [
                describe_report(item["report"], f"已公開精選閱讀背景 R{i + 1}", "（只比較資訊覆蓋，不是事實候選）", 2000),
                f"【該已選報道的原文證據】\n{js_slice(item['sourceText'], 0, GROUP_BODY_CHARS)}\n【原文證據結束】"
                if item.get("sourceText")
                else None,
            ]
            if x
        )
        for i, item in enumerate(reading)
    )
    tail = "同時比較所有已公開精選內容，判斷新增價值；無事實候選時 decisions=[]。" if background else ""
    listing = "\n\n".join(parts)
    extra = f"\n\n{background}" if background else ""
    return f"{describe_report(query, query_label)}\n\n{listing}{extra}\n\n{query_label}與每個候選的關係是什麼？{tail}"


def pair_user(a: dict, b: dict) -> str:
    return f"{describe_report(a, '報道 A')}\n\n{describe_report(b, '報道 B')}\n\n這兩篇報道是什麼關係？"


# ── decision rules ──────────────────────────────────────────────────────────────


def complete_decisions(decisions: list[dict], count: int) -> bool:
    """An identity answer must address every supplied candidate exactly once."""
    ids = {d["id"] for d in decisions}
    return len(decisions) == count and len(ids) == count and all(candidate_key(i) in ids for i in range(count))


def verdicts_by_fact(decisions: list[dict], cands: list[CandidateView]) -> dict[int, Verdict]:
    """Verdicts by fact id; missing answers are failures, never assertions of unrelatedness."""
    if not complete_decisions(decisions, len(cands)):
        raise ValueError("Identity answer must cover every candidate exactly once")
    out: dict[int, Verdict] = {}
    for d in decisions:
        n = int(re.sub(r"^C", "", js_trim(str(d["id"])), flags=re.I))
        c = cands[n - 1] if 1 <= n <= len(cands) else None
        if c and c.fact_id not in out:
            out[c.fact_id] = Verdict(d["relation"], d["confidence"], d.get("note") or "")
    return out


def same_occurrence(cands: list[CandidateView], verdicts: dict[int, Verdict]) -> list[CandidateView]:
    """Candidates the model calls the same occurrence, most confident first (recall similarity breaks ties)."""
    picked = [c for c in cands if (v := verdicts.get(c.fact_id)) and v.relation == "SAME_OCCURRENCE"]
    return sorted(picked, key=lambda c: (-verdicts[c.fact_id].confidence, -c.score))


def story_for_development(cands: list[CandidateView], verdicts: dict[int, Verdict]) -> CandidateView | None:
    """The story a development joins: the most similar candidate that is its story's root occurrence."""
    picked = sorted((c for c in cands if c.story_root and (v := verdicts.get(c.fact_id)) and v.relation == "SAME_STORY"), key=lambda c: -c.score)
    return picked[0] if picked else None


def signal_target(cands: list[CandidateView], verdicts: dict[int, Verdict], min_reaction_confidence: float = 0.8) -> CandidateView | None:
    """A discussion post counts for a fact it reports, or one it clearly reacts to."""
    same = same_occurrence(cands, verdicts)
    if same:
        return same[0]
    reacting = sorted(
        (c for c in cands if (v := verdicts.get(c.fact_id)) and v.relation == "SAME_STORY" and v.confidence >= min_reaction_confidence),
        key=lambda c: -c.score,
    )
    return reacting[0] if reacting else None


def firmly_tied(relation: str | None, confidence: float | None, min_confidence: float = TIE_MIN_CONFIDENCE) -> bool:
    """A report is firmly tied to a candidate it calls the same occurrence or a direct development of."""
    return relation in TIE_RELATIONS and (confidence or 0) >= min_confidence


def report_text(title: str, summary: str | None) -> str:
    """The text a report is embedded with: title and the start of the summary, the same on both sides of a comparison."""
    return f"{title}。{js_slice(summary or '', 0, 300)}"


def signal_text(a: dict) -> str:
    """The text a discussion post is embedded with: its title and the start of its body."""
    return report_text(a["title"], js_slice(a["body_text"], 0, 300) if a.get("body_text") else None)


def lexical_similarity(a: str, b: str) -> float:
    """Lexical stand-in for cosine when embeddings are off (no embedding key): shared character bigrams."""

    def grams(s: str) -> set[str]:
        chars = list(
            re.sub(r"\s+", "", s)
        )  # code points, as JavaScript spreads the string; a pair with an astral character is longer than two units and is dropped
        return {x for i in range(len(chars) - 1) if js_length(x := chars[i] + chars[i + 1]) == 2}

    g, h = grams(a), grams(b)
    if not g or not h:
        return 0.0
    return len(g & h) / min(len(g), len(h))
