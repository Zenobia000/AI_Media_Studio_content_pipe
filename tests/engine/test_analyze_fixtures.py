"""The analysis' pure helpers must give what the TypeScript ones give (analyze_fixtures.json: AIHOT/scripts/gen-analyze-fixtures.ts): the prompt files and their
versions, every prompt the steps build from an article, the answer parsing, the guards and the normalised judgement."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsroom.engine import analyze as A
from newsroom.engine import vocabulary as V
from newsroom.engine import writing as W
from newsroom.engine.analyze_input import build_material

F = json.loads((Path(__file__).parent / "analyze_fixtures.json").read_text(encoding="utf-8"))
TODAY_THEN = F["today"]
TODAY_NOW = datetime.now(UTC).strftime("%Y-%m-%d")


def hydrate(raw: dict) -> dict:
    a = dict(raw)
    a["publishedAt"] = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00")) if a.get("publishedAt") else None
    return a


ARTICLES = [hydrate(a) for a in F["articles"]]


def day(text: str, when: str) -> str:
    return text.replace(when, "<TODAY>")


def test_prompt_files_and_versions():
    assert A.prompt_versions() == F["versions"]
    assert A.analyze_prompt_version() == F["analyzeVersion"] and A.selection_prompt_version() == F["selectionVersion"]


@pytest.mark.parametrize("name,fn", [("prefilter", W.prefilter_system), ("understand", W.understand_system), ("score", A.score_system), ("structure", A.structure_system), ("mend", A.mend_system)])
def test_system_prompts(name, fn):
    assert fn() == F["systems"][name]


@pytest.mark.parametrize("i", range(len(F["per"])))
def test_prompts_of_an_article(i):
    want, a = F["per"][i], ARTICLES[i]
    t = W.translate_input_of(a)
    assert W.prefilter_user(a) == want["prefilterUser"]
    assert W.understand_user(a) == want["understandUser"]
    assert W.render_context(a) == want["renderContext"]
    assert build_material(a) == want["material"]
    assert A.build_score_input(a) == want["score"]
    assert W.missing_evidence(a) is want["missing"] and A.awaits_material(a) is want["awaits"]
    assert A.selection_threshold(a["source"]) == want["threshold"]
    for key, fn in (("article", W.build_article_prompt), ("short", W.build_short_tweet_prompt), ("long", W.build_long_tweet_prompt)):
        try:
            got = fn(t)
        except Exception as e:
            got = f"ERR {e}"
        assert day(got, TODAY_NOW) == day(want[key], TODAY_THEN), key
    ti = want["translateInput"]
    assert (t.title, t.text, t.source_kind, t.source_name, t.document_url, t.source_owner_entity_id, t.main_text, t.quoted_text, t.quoted_author, t.publication_time) == (
        ti["title"], ti["text"], ti["sourceKind"], ti["sourceName"], ti["documentUrl"], ti["sourceOwnerEntityId"], ti.get("mainText"), ti.get("quotedText"), ti.get("quotedAuthor"), ti.get("publicationTime")
    )  # fmt: skip


@pytest.mark.parametrize("c", F["copies"], ids=lambda c: f"article{c['article']}-{c['copy']['titleZh'][:6]}")
def test_finalize_copy(c):
    assert W.finalize_copy(W.translate_input_of(ARTICLES[c["article"]]), c["copy"]) == c["out"]


@pytest.mark.parametrize("c", F["parse"], ids=lambda c: repr(c["in"])[:30])
def test_parse_translate_output(c):
    assert W.parse_translate_output(c["in"]) == c["out"]


@pytest.mark.parametrize("c", F["tags"], ids=lambda c: repr(c["in"])[:30])
def test_normalize_tags(c):
    assert V.normalize_tags(c["in"]) == c["out"]


@pytest.mark.parametrize("i", range(len(F["structure"])))
def test_normalize_structure(i):
    c = F["structure"][i]
    data = {**c["data"], "fact": A._fact(c["data"]["fact"])}
    assert A.normalize_structure(data, ARTICLES[c["article"]]) == c["out"]


@pytest.mark.parametrize("i", range(len(F["runs"])))
def test_normalize_analysis(i):
    c = F["runs"][i]
    run = {**c["run"], "prefilter": c["run"]["prefilter"]}
    for k in ("scores", "writing", "structure"):
        run[k] = c["run"][k]
    out = A.normalize_analysis(run)
    assert out == c["out"]
    assert A.shown_score(run["scores"]) == c["shown"] and A.scores_select(run["scores"]) is c["select"]


@pytest.mark.parametrize("c", F["scoreTimes"], ids=lambda c: c["in"])
def test_score_input_time(c):
    assert A.score_input_time(datetime.fromisoformat(c["in"].replace("Z", "+00:00"))) == c["out"]


@pytest.mark.parametrize("c", F["clean"], ids=lambda c: repr(c["in"])[:20])
def test_clean_text(c):
    assert W.clean_article_text_for_llm(c["in"]) == c["out"]


@pytest.mark.parametrize("c", F["compact"], ids=lambda c: repr(c["in"])[:12])
def test_compact_summary(c):
    assert W.compact_answer_first_summary(c["in"]) == c["out"]


@pytest.mark.parametrize("c", F["short"], ids=lambda c: repr(c["in"])[:12])
def test_short_tweet_rules(c):
    assert (W.is_short_tweet(c["in"]), W.needs_short_tweet_translation(c["in"])) == (c["short"], c["needs"])


@pytest.mark.parametrize("c", F["match"], ids=lambda c: repr(c["in"])[:30])
def test_entity_matching(c):
    assert W.match_entity_ids(c["in"]) == c["out"]
