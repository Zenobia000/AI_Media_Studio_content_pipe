"""The pure parts of event grouping - how a report and its candidates are described to the judge, how its answer is read, and the rules that turn answers into a decision -
must be what the TypeScript engine's are, on the same inputs."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

import pytest

from tests.parity.conftest import AIHOT, ENV

EMOJI = "😀" * 200
REPORTS = {
    "full": {
        "title": "OpenAI 發布 GPT-5.5", "source": "OpenAI", "firstParty": True, "at": "2026-10-30T08:00:00Z", "publicationTime": "2026-10-30T16:00:00+08:00",
        "summary": "這是摘要。" * 100, "scope": "single", "frame": {"subject": "OpenAI", "action": "發布", "object": "GPT-5.5", "occurredAt": "2026-10-30"},
        "sourceText": "原文證據。" * 3000,
    },
    "minimal": {"title": "A title", "source": "Blog", "firstParty": False, "at": None, "summary": None},
    "composite": {"title": "本週要聞", "source": "週報", "firstParty": False, "at": "2026-10-29T01:02:03Z", "summary": EMOJI, "scope": "composite", "frame": {"subject": None, "action": "", "object": "x"}},
    "unknown": {"title": "T", "source": "S", "firstParty": False, "at": "2026-10-28T00:00:00Z", "summary": "short", "scope": "unknown", "frame": {"subject": "", "action": "", "object": ""}, "sourceText": ""},
}  # fmt: skip


def cand(n, report, **kw):
    return {"factId": n, "storyId": n * 10, "factTitle": f"事實 {n}", "members": n, "storyRoot": n % 2 == 1, "score": 0.5 + n / 20, "report": report, **kw}


CANDS = [
    cand(1, REPORTS["full"], selected=True, selectedReport=REPORTS["minimal"]),
    cand(2, REPORTS["minimal"]),
    cand(3, REPORTS["composite"], selected=True, selectedReport=None),
    cand(4, REPORTS["unknown"], selected=False, selectedReport=REPORTS["full"]),
]
READING = [{"report": REPORTS["full"], "sourceText": "背景原文" * 5000}, {"report": REPORTS["minimal"], "sourceText": None}]


def node(tmp_path, calls):
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"op": "relate", "calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x"}
    out = subprocess.run(["node", "scripts/events-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


def py_report(r):
    if r is None:
        return None
    return {**r, "at": datetime.fromisoformat(r["at"].replace("Z", "+00:00")) if r.get("at") else None}


def py_cand(c):
    from newsroom.engine.events.relate import CandidateView

    return CandidateView(c["factId"], c["storyId"], c["factTitle"], c["members"], c["storyRoot"], c["score"], py_report(c["report"]), c.get("selected", False), py_report(c.get("selectedReport")))


def test_descriptions(settings_env, tmp_path):
    from newsroom.engine.events import relate as R

    names = list(REPORTS)
    calls = [["describeReport", REPORTS[n], "標籤", "（額外）", 360] for n in names] + [["describeReport", REPORTS["composite"], "標籤", "", 20]]
    calls += [["pairUser", REPORTS["full"], REPORTS["minimal"]], ["pairUser", REPORTS["composite"], REPORTS["unknown"]]]
    calls += [["batchUser", REPORTS["full"], CANDS, "新報道", []], ["batchUser", REPORTS["minimal"], CANDS[:2], "新報道", READING], ["batchUser", REPORTS["unknown"], CANDS, "帖子", []], ["batchUser", REPORTS["full"], [], "新報道", READING]]
    want = node(tmp_path, calls)
    got = [R.describe_report(py_report(REPORTS[n]), "標籤", "（額外）", 360) for n in names] + [R.describe_report(py_report(REPORTS["composite"]), "標籤", "", 20)]
    got += [R.pair_user(py_report(REPORTS["full"]), py_report(REPORTS["minimal"])), R.pair_user(py_report(REPORTS["composite"]), py_report(REPORTS["unknown"]))]
    reading = [{"report": py_report(x["report"]), "sourceText": x["sourceText"]} for x in READING]
    got += [
        R.batch_user(py_report(REPORTS["full"]), [py_cand(c) for c in CANDS], "新報道", []),
        R.batch_user(py_report(REPORTS["minimal"]), [py_cand(c) for c in CANDS[:2]], "新報道", reading),
        R.batch_user(py_report(REPORTS["unknown"]), [py_cand(c) for c in CANDS], "帖子", []),
        R.batch_user(py_report(REPORTS["full"]), [], "新報道", reading),
    ]
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, (i, g[:300], w[:300])


TEXTS = [("a b", "ab"), ("你好嗎我很好", "你好嗎他不好"), ("😀😀😀 hello", "😀 hello world"), ("", "x"), ("a", "a"), ("相同的內容 相同", "相同的內容相同"), ("xx" * 40, "xy" * 40)]


def test_texts(settings_env, tmp_path):
    from newsroom.engine.events import relate as R

    calls = [["lexicalSimilarity", a, b] for a, b in TEXTS] + [["reportText", "標題", s] for s in (None, "", "短", "長" * 400, EMOJI)]
    calls += [["signalText", {"title": "標題", "body_text": b}] for b in (None, "", "正文" * 300, EMOJI)]
    want = node(tmp_path, calls)
    got = [R.lexical_similarity(a, b) for a, b in TEXTS] + [R.report_text("標題", s) for s in (None, "", "短", "長" * 400, EMOJI)]
    got += [R.signal_text({"title": "標題", "body_text": b}) for b in (None, "", "正文" * 300, EMOJI)]
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        # a cut through an emoji leaves a lone surrogate in JavaScript, which JSON writes as an escape and Python cannot hold
        assert g == pytest.approx(w) if isinstance(g, float) else g.replace("\ud83d", "") == w.replace("\ud83d", ""), (i, g, w)


def decisions(*rows):
    return [{"id": i, "relation": r, "confidence": c, "note": "n"} for i, r, c in rows]


def test_answers_and_rules(settings_env, tmp_path):
    from newsroom.engine.events import relate as R

    cs = CANDS
    good = decisions(("C1", "SAME_OCCURRENCE", 0.9), ("C2", "SAME_STORY", 0.85), ("C3", "UNRELATED", 0.2), ("C4", "SAME_OCCURRENCE", 0.9))
    verdict_cases = [good, good[:3], good + good[:1], [{**d, "id": "c" + d["id"][1:]} for d in good], decisions(("C1", "SAME_STORY", 0.5), ("C2", "SAME_STORY", 0.95), ("C3", "SAME_STORY", 0.95), ("C4", "ROUNDUP", 1))]
    calls = [["verdictsByFact", d, cs] if R.complete_decisions(d, 4) else ["completeDecisions", d, 4] for d in verdict_cases]
    calls += [["completeDecisions", d, 4] for d in verdict_cases]
    calls += [["firmlyTied", rel, conf] for rel in ("SAME_OCCURRENCE", "SAME_STORY", "UNRELATED", "ROUNDUP", None) for conf in (0.8, 0.79, 0.95, None)]
    verdicts = [[c, {"relation": r, "confidence": conf, "note": ""}] for c, (r, conf) in zip((1, 2, 3, 4), (("SAME_OCCURRENCE", 0.7), ("SAME_STORY", 0.9), ("SAME_STORY", 0.85), ("SAME_OCCURRENCE", 0.7)), strict=True)]
    calls += [["rules", cs, verdicts], ["rules", cs, verdicts[1:2]], ["rules", cs, []]]
    # answer shapes: the schema keeps what the engine uses and refuses what it cannot use
    batch = {"query": "q", "decisions": good, "selection": {"addsValue": True, "reason": "  有新增  "}}
    bad_batches = [batch, {**batch, "query": "x" * 401}, {**batch, "selection": {"addsValue": True, "reason": "   "}}, {**batch, "selection": {"addsValue": "yes", "reason": "r"}}, {**batch, "decisions": good[:3]},
                   {**batch, "decisions": [{**good[0], "confidence": 1.5}] + good[1:]}, {**batch, "decisions": [{**good[0], "id": "C0"}] + good[1:]}, {**batch, "decisions": [{**good[0], "relation": "MAYBE"}] + good[1:]},
                   {**batch, "decisions": [{**d, "note": "n" * 401} for d in good]}, [], None, {"decisions": good}]  # fmt: skip
    calls += [["batchSchema", b, 4] for b in bad_batches]
    pair = {"a": "a", "b": "b", "relation": "SAME_STORY", "difference": "d", "confidence": 0.8}
    pairs = [pair, {**pair, "a": 1}, {**pair, "relation": "x"}, {**pair, "confidence": -0.1}, {**pair, "difference": "d" * 500}, {}, "no"]
    calls += [["pairSchema", p] for p in pairs]
    signals = [{"decisions": [{k: v for k, v in d.items() if k != "note"} for d in good]}, {"decisions": good[:2]}, {"decisions": []}, {"decisions": "no"}]
    calls += [["signalSchema", s, 4] for s in signals]
    calls += [["signalSchema", {"decisions": []}, 0]]
    want = node(tmp_path, calls)

    got: list = []
    for d in verdict_cases:
        if R.complete_decisions(d, 4):
            v = R.verdicts_by_fact(d, [py_cand(c) for c in cs])
            got.append([[k, {"relation": x.relation, "confidence": x.confidence, "note": x.note}] for k, x in v.items()])
        else:
            got.append(R.complete_decisions(d, 4))
    got += [R.complete_decisions(d, 4) for d in verdict_cases]
    got += [R.firmly_tied(rel, conf) for rel in ("SAME_OCCURRENCE", "SAME_STORY", "UNRELATED", "ROUNDUP", None) for conf in (0.8, 0.79, 0.95, None)]
    for vs in (verdicts, verdicts[1:2], []):
        pv = {k: R.Verdict(v["relation"], v["confidence"]) for k, v in vs}
        pcs = [py_cand(c) for c in cs]
        dev, sig = R.story_for_development(pcs, pv), R.signal_target(pcs, pv)
        got.append({"same": [c.fact_id for c in R.same_occurrence(pcs, pv)], "dev": dev.fact_id if dev else None, "signal": sig.fact_id if sig else None})

    def parsed(fn, *args):
        try:
            return fn(*args)
        except R.SchemaError:
            return None

    got += [parsed(R.batch_schema(4), b) for b in bad_batches]
    got += [parsed(R.pair_schema, p) for p in pairs]
    got += [parsed(R.signal_schema(4), s) for s in signals]
    got += [parsed(R.signal_schema(0), {"decisions": []})]
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, (i, g, w)
