"""The chat's decisions without a model or a database: what kind of message it is, when it answers "not found" without asking the model, which sources
an answer lists, and what the endpoint refuses. The model and the search are replaced by fakes."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from newsroom.api.app import build_app
from newsroom.api.routes import chat as chat_route
from newsroom.rag import ask as rag


def _hit(title: str, score: float) -> dict:
    return {"article_id": title, "score": score, "title": title, "summary": f"{title}的摘要", "url": f"https://example.com/{title}"}


def _model(kind: str, topic: str = ""):
    """A fake model that sorts every message as `kind`, and fails the test if it is asked for anything else."""

    def chat_fn(messages, temperature=0.2, fmt=None):
        if fmt is None:
            raise AssertionError("the model was asked for an answer")
        return json.dumps({"intent": kind, "topic": topic})

    return chat_fn


def test_cited_numbers_in_order_once_each():
    assert rag.cited_numbers("A [3] B [1][3] C [12]") == [3, 1, 12]
    assert rag.cited_numbers("沒有出處") == []
    # the other ways the model writes them: several in one pair, full-width and lenticular brackets; a name or a year in brackets is not a number
    assert rag.cited_numbers("見 [1, 2]、［3］與【2】，還有 [4、5]；[GPT-5] 和 [2026] 不是") == [1, 2, 3, 4, 5]


def test_every_request_to_the_model_turns_thinking_off():
    """A model that reasons first would spend seconds reasoning before each route, rewrite and answer."""
    for stream, fmt in ((False, rag.ROUTE_FORMAT), (False, None), (True, None)):
        assert rag._body([{"role": "user", "content": "你好"}], stream, 0.2, fmt)["think"] is False


def test_route_reads_the_kind_and_topic():
    assert rag.route("推薦 AI 影片", None, _model("recommend", "AI 影片")) == ("recommend", "AI 影片")


@pytest.mark.parametrize("reply", ["not json", '{"intent": "dance", "topic": ""}', "[]"])
def test_route_falls_back_to_a_question_about_the_news(reply):
    assert rag.route("你好", None, lambda *a, **k: reply) == ("ask", "")


def test_no_earlier_turns_leaves_the_question_alone():
    def never(*a, **k):
        raise AssertionError("the model was asked to rewrite")

    assert rag.standalone_question("NVIDIA 發布了什麼", None, never) == "NVIDIA 發布了什麼"


def test_a_question_the_articles_do_not_cover_is_not_found_and_the_model_is_not_asked(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("離題", 0.45)])  # too far even to be called related
    p = rag.plan(None, "今天天氣", None, chat_fn=_model("ask"))
    assert p.reply == rag.NOT_FOUND and p.sources == [] and p.messages is None


def test_a_question_only_near_some_articles_lists_them_without_the_model(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("近", 0.57), _hit("較遠", 0.56), _hit("太遠", 0.5)])
    p = rag.plan(None, "近的訓練成本", None, chat_fn=_model("ask"))
    assert p.reply == rag.NOT_ANSWERED and p.messages is None
    assert [(s["n"], s["title"]) for s in p.sources] == [(1, "近"), (2, "較遠")]


def test_when_an_article_answers_the_nearer_ones_are_not_listed_as_related(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("甲", 0.7), _hit("乙", 0.58)])
    hits, related = rag.retrieve_with_related(None, "甲是什麼", chat_fn=_model("ask"))
    assert [h["title"] for h in hits] == ["甲"] and related == []


def test_a_covered_question_goes_to_the_model_with_numbered_articles(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("甲", 0.7), _hit("乙", 0.66)])
    p = rag.plan(None, "甲是什麼", None, chat_fn=_model("ask"))
    assert [s["n"] for s in p.sources] == [1, 2] and p.reply is None
    assert p.messages and "[1] 甲" in p.messages[-1]["content"] and "[2] 乙" in p.messages[-1]["content"]


def test_chat_has_no_sources_and_keeps_the_site_only_rule():
    p = rag.plan(None, "你好", None, chat_fn=_model("chat"))
    assert p.sources == [] and p.messages and p.messages[0]["content"] == rag.CHAT_SYSTEM and p.messages[-1]["content"] == "你好"
    assert rag.OFF_SITE in rag.CHAT_SYSTEM  # what it is told to say to anything else is the fixed reply, word for word


def test_a_request_that_is_not_about_the_site_gets_the_fixed_reply_and_the_model_is_not_asked(monkeypatch):
    """The fake model fails the test if it is asked for an answer: "other" never reaches it. Articles that are only near do not change that."""
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("近", 0.57), _hit("離題", 0.4)])
    assert rag.route("幫我寫一封請假信", None, _model("other")) == ("other", "")
    p = rag.plan(None, "幫我寫一封請假信", None, chat_fn=_model("other"))
    assert p.kind == "other" and p.reply == rag.OFF_SITE and p.sources == [] and p.messages is None


def test_a_message_sorted_as_not_about_the_site_is_answered_when_an_article_does_answer_it(monkeypatch):
    """The sorting is a model's guess: a question about the stock market was sorted as "other" although the site has articles on it."""
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("台股", 0.65)])
    p = rag.plan(None, "台股最近的走勢如何", None, chat_fn=_model("other"))
    assert p.kind == "ask" and p.reply is None and [s["title"] for s in p.sources] == ["台股"]
    assert p.messages and p.messages[0]["content"] == rag.ASK_SYSTEM


def test_the_model_may_only_sort_a_message_into_the_four_kinds():
    assert rag.ROUTE_FORMAT["properties"]["intent"]["enum"] == ["chat", "recommend", "ask", "other"]


def test_recommend_with_nothing_to_recommend_says_so(monkeypatch):
    monkeypatch.setattr(rag, "top_stories", lambda *a, **k: [])
    monkeypatch.setattr(rag, "latest_selected", lambda *a, **k: [])
    assert rag.plan(None, "推薦幾篇", None, chat_fn=_model("recommend")).reply == rag.NO_RECOMMENDATION


def test_recommend_for_a_topic_accepts_nearer_than_an_answer_needs(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("影片", 0.55), _hit("離題", 0.4)])
    p = rag.plan(None, "有影片的嗎", None, chat_fn=_model("recommend", "影片"))
    assert [s["title"] for s in p.sources] == ["影片"]


def test_with_no_topic_the_events_several_sources_reported_come_first_and_the_newest_selected_fill_up(monkeypatch):
    monkeypatch.setattr(rag, "top_stories", lambda *a, **k: [_hit("多家報導", 0.6)])
    monkeypatch.setattr(rag, "latest_selected", lambda *a, **k: [_hit("多家報導", 0.9), _hit("最新一", 0.9), _hit("最新二", 0.9)])
    assert [h["title"] for h in rag.popular(None, 3)] == ["多家報導", "最新一", "最新二"]  # the one article is not listed twice
    assert [h["title"] for h in rag.popular(None, 2)] == ["多家報導", "最新一"]  # and the list stops at k


def test_a_recommendation_with_no_topic_is_numbered(monkeypatch):
    monkeypatch.setattr(rag, "popular", lambda *a, **k: [_hit("甲", 0.6), _hit("乙", 0.6)])
    assert [h["n"] for h in rag.recommend(None, "")] == [1, 2]


class _Pool:
    @contextmanager
    def connection(self):
        yield None


def _events(monkeypatch, plan, pieces):
    monkeypatch.setattr(chat_route, "plan", lambda *a, **k: plan)
    monkeypatch.setattr(chat_route, "chat_stream", lambda messages: iter(pieces))
    return [json.loads(line) for line in b"".join(chat_route._stream(_Pool(), [], "問題")).splitlines()]


def test_the_stream_lists_only_the_sources_the_answer_points_to(monkeypatch):
    sources = [{"n": i, **_hit(f"文{i}", 0.7)} for i in (1, 2, 3)]
    events = _events(monkeypatch, rag.Plan("ask", sources, messages=[]), ["見 [2]", "，也見 [2]。"])
    assert [e["type"] for e in events] == ["sources", "status", "delta", "delta", "cited", "done"]
    assert events[4]["numbers"] == [2]
    assert [s["n"] for s in events[0]["sources"]] == [1, 2, 3]


def test_the_stream_ignores_a_number_that_is_not_a_source(monkeypatch):
    events = _events(monkeypatch, rag.Plan("ask", [{"n": 1, **_hit("文", 0.7)}], messages=[]), ["[1] 與 [9]"])
    assert next(e for e in events if e["type"] == "cited")["numbers"] == [1]


def test_each_step_of_the_plan_is_told_as_a_status_line_before_the_answer(monkeypatch):
    def planning(conn, question, history, step=None, **k):
        step("搜尋站內相關文章")
        step("找到 2 篇相關文章")
        return rag.Plan("ask", [], reply=rag.NOT_FOUND)

    monkeypatch.setattr(chat_route, "plan", planning)
    events = [json.loads(line) for line in b"".join(chat_route._stream(_Pool(), [], "問題")).splitlines()]
    assert [e["type"] for e in events] == ["status", "status", "sources", "delta", "done"]
    assert [e["text"] for e in events[:2]] == ["搜尋站內相關文章", "找到 2 篇相關文章"]


def test_the_plan_tells_what_it_is_doing(monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: [_hit("甲", 0.7)])
    said: list[str] = []
    rag.plan(None, "甲是什麼", None, chat_fn=_model("ask"), step=said.append)
    assert said == ["判斷你想做什麼", "搜尋站內相關文章", "找到 1 篇相關文章"]


def test_a_fixed_reply_that_lists_related_articles_tells_the_page_to_show_them(monkeypatch):
    sources = [{"n": i, **_hit(f"文{i}", 0.57)} for i in (1, 2)]
    events = _events(monkeypatch, rag.Plan("ask", sources, reply=rag.NOT_ANSWERED), [])
    assert [e["type"] for e in events] == ["sources", "delta", "cited", "done"] and events[2]["numbers"] == [1, 2]


def test_a_fixed_reply_is_one_delta_with_no_model(monkeypatch):
    events = _events(monkeypatch, rag.Plan("ask", [], reply=rag.NOT_FOUND), [])
    assert [e["type"] for e in events] == ["sources", "delta", "done"] and events[1]["text"] == rag.NOT_FOUND


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "嗨"}]},  # must end with the user's question
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": "長" * 501}]},
        {"messages": [{"role": "system", "content": "x"}]},
        {"messages": [{"role": "user", "content": 5}]},
        {"messages": [{"role": "user", "content": "嗨"}] * 41},
    ],
)
def test_the_endpoint_refuses_a_bad_conversation(body):
    # Without `with`, the app's startup (the database) does not run; these answers come before anything needs it.
    res = TestClient(build_app("postgres://unused/unused")).post("/api/chat", json=body)
    assert res.status_code == 400 and res.json()["code"] == "invalid_request"


def test_the_endpoint_refuses_a_body_that_is_not_json():
    res = TestClient(build_app("postgres://unused/unused")).post("/api/chat", content=b"oops")
    assert res.status_code == 400
