"""A conversation with the site's articles. Each message is first sorted by a local Qwen into one of three kinds:
chat (a greeting, a thanks, "what can you do", a request to reword the last answer): Qwen answers like any assistant, but the site's articles are the only
  knowledge it may state as fact;
recommend ("recommend some articles", "what is new", "anything on video models?"): articles are picked from the site (the newest selected ones, or the
  closest to the topic) and Qwen presents them;
ask (a question about the news): the articles scoring RAG_MIN_SCORE or more go to Qwen with the earlier turns; with none, the answer is "not found" and the
  model is not asked, so it cannot answer from what it knows instead of from the site.
A follow-up is first rewritten into a question that stands alone, so the search finds its articles."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from newsroom.engine.publication.scope import browsed_condition, evidence_condition, listed_condition, scope_params
from newsroom.rag.index import EmbedError, EmbedFn, embed, embed_url, search

Messages = list[dict[str, str]]
ChatFn = Callable[..., str]
NOT_FOUND = "資料庫裡找不到相關的文章。"
NO_RECOMMENDATION = "目前站上沒有可以推薦的文章。"
HISTORY_TURNS = 6  # the last turns sent to the model and to the rewrite
RECOMMEND_COUNT = 5
STORY_DAYS = 14  # an event counts as current for this long after its latest report
RECOMMEND_MIN_SCORE = 0.5  # for a topic: lower than for answers, a recommendation only has to be near

PERSONA = "你是這個新聞網站的問答助手，用繁體中文，語氣自然親切，回答簡潔。"
CHAT_SYSTEM = (
    PERSONA
    + "你可以閒聊、問候、說明自己能做什麼，也可以依對話內容改寫、縮短、翻譯你先前的回答。"
    "你能提供的新聞與資訊只來自這個網站收錄的文章：使用者問到需要外部知識的事實（天氣、股價、常識、其他新聞），"
    "不要回答，也不要猜，友善地說明你只能回答這個網站收錄的內容，並建議他可以問站內的新聞，或請你推薦幾篇文章。"
)
ASK_SYSTEM = (
    PERSONA
    + "你只能根據下面「文章」裡的內容回答，不可以用你自己知道的資訊，文章沒提到的就說文章沒提到。"
    "每個重點後面用 [編號] 標出它來自哪一篇。可以參考前面的對話。"
)
RECOMMEND_SYSTEM = (
    PERSONA
    + "下面是網站上的文章清單。依使用者的要求，從清單裡推薦，每篇用一兩句話說它在講什麼、為什麼值得看，並在後面用 [編號] 標出是哪一篇。"
    "只能推薦清單裡的文章，不要自己加。"
)
REWRITE = (
    "把使用者最後一個問題改寫成一個不需要上下文也看得懂的搜尋問題，補上前面對話裡提到的人事物。"
    "只輸出改寫後的問題，一行，不要解釋。如果它本來就完整，原樣輸出。"
)
ROUTE = (
    "判斷使用者最後一句話屬於哪一類，只輸出 JSON。\n"
    'chat：問候、道謝、再見、問你是誰或能做什麼、請你改寫或縮短先前的回答、純粹閒聊。\n'
    'recommend：要你推薦或列出文章，或問最近有什麼新聞、有哪些文章；topic 填他想看的主題，沒有指定就填空字串。\n'
    'ask：問某件事、某個產品、某家公司的內容或細節（需要查文章才能回答）。\n'
    '例：「你好」→chat；「推薦幾篇文章」→recommend，topic 空；「有 AI 影片的文章嗎」→recommend，topic「AI 影片」；'
    '「NVIDIA 發布了什麼」→ask。'
)
ROUTE_FORMAT = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["chat", "recommend", "ask"]}, "topic": {"type": "string"}},
    "required": ["intent", "topic"],
}


def min_score() -> float:
    try:
        return float(os.environ.get("RAG_MIN_SCORE", "0.6"))
    except ValueError:
        return 0.6


def chat_model() -> str:
    return os.environ.get("RAG_CHAT_MODEL") or "qwen2.5:14b"


def _body(messages: Messages, stream: bool, temperature: float, fmt: dict | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": chat_model(), "stream": stream, "options": {"temperature": temperature}, "messages": messages}
    if fmt:
        body["format"] = fmt
    return body


def chat(messages: Messages, temperature: float = 0.2, fmt: dict | None = None) -> str:
    """One answer from an Ollama server (/api/chat); `fmt` is a JSON schema the answer must follow."""
    try:
        res = httpx.post(f"{embed_url()}/api/chat", json=_body(messages, False, temperature, fmt), timeout=600)
        res.raise_for_status()
        return str(res.json()["message"]["content"]).strip()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise EmbedError(f"chat failed ({embed_url()}, model {chat_model()}): {e}") from e


def chat_stream(messages: Messages) -> Iterator[str]:
    """The answer in pieces as the model writes it."""
    try:
        with httpx.stream("POST", f"{embed_url()}/api/chat", json=_body(messages, True, 0.3), timeout=600) as res:
            res.raise_for_status()
            for line in res.iter_lines():
                if line:
                    piece = json.loads(line).get("message", {}).get("content")
                    if piece:
                        yield piece
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise EmbedError(f"chat failed ({embed_url()}, model {chat_model()}): {e}") from e


def _recent(history: Messages | None, n: int = HISTORY_TURNS) -> Messages:
    return [m for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content")][-n:]


def _talk(history: Messages | None) -> str:
    return "\n".join(f"{'使用者' if m['role'] == 'user' else '助手'}：{m['content'][:300]}" for m in _recent(history))


def route(question: str, history: Messages | None, chat_fn: ChatFn = chat) -> tuple[str, str]:
    """(kind, topic) of the message; anything the model returns that is not one of the three kinds is treated as a question about the news."""
    talk = _talk(history)
    prompt = f"對話：\n{talk}\n\n最後一句話：{question}" if talk else f"最後一句話：{question}"
    try:
        out = json.loads(chat_fn([{"role": "system", "content": ROUTE}, {"role": "user", "content": prompt}], 0, ROUTE_FORMAT))
        intent, topic = str(out.get("intent")), str(out.get("topic") or "").strip()
    except (ValueError, AttributeError, EmbedError):
        return "ask", ""
    return (intent, topic) if intent in ("chat", "recommend", "ask") else ("ask", "")


def standalone_question(question: str, history: Messages | None, chat_fn: ChatFn = chat) -> str:
    """The question as the search should see it: with no earlier turns it is the question itself."""
    talk = _talk(history)
    if not talk:
        return question
    rewritten = chat_fn([{"role": "system", "content": REWRITE}, {"role": "user", "content": f"對話：\n{talk}\n\n最後一個問題：{question}"}])
    return rewritten.splitlines()[0].strip() if rewritten.strip() else question


def _numbered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"n": i, **h} for i, h in enumerate(items, 1)]


def retrieve(conn, question: str, history: Messages | None = None, k: int = 5, embed_fn: EmbedFn = embed, chat_fn: ChatFn = chat) -> list[dict[str, Any]]:
    query = standalone_question(question, history, chat_fn)
    return _numbered([h for h in search(conn, query, k, embed_fn) if h["score"] >= min_score()])


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {"article_id": r["article_id"], "score": float(r["score"] or 0), "title": r["title_zh"] or r["title"], "summary": r["summary_zh"] or "", "url": r["url"]}


def latest_selected(conn, k: int = RECOMMEND_COUNT, now: datetime | None = None) -> list[dict[str, Any]]:
    """The newest articles the site selected and lists publicly, as a recommendation list."""
    rows = conn.execute(
        f"""SELECT p.article_id, p.url, p.title, p.score, an.title_zh, an.summary_zh
            FROM publications p
            LEFT JOIN LATERAL (SELECT title_zh, summary_zh FROM analyses WHERE article_id = p.article_id AND origin = 'model' ORDER BY id DESC LIMIT 1) an ON true
            WHERE p.selected AND {listed_condition()} ORDER BY p.timeline_at DESC LIMIT %(k)s""",
        {**scope_params(now or datetime.now(UTC)), "k": k},
    ).fetchall()
    return [_row(r) for r in rows]


def top_stories(conn, k: int = RECOMMEND_COUNT, now: datetime | None = None) -> list[dict[str, Any]]:
    """Events that two or more different sources reported in the last STORY_DAYS days, the most reported first. A score cannot tell news from a tutorial (the
    model scored both 95 to 100), but a tutorial is not reported by several sources. Each is shown by its best-scored report, under the event's title."""
    rows = conn.execute(
        f"""WITH stories_now AS (
              SELECT st.id, st.title AS story_title, count(DISTINCT p.source_id) AS sources, max(coalesce(p.published_at, p.discovered_at)) AS latest
              FROM stories st JOIN facts f ON f.story_id = st.id JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
              WHERE st.merged_into IS NULL AND {evidence_condition()} AND {browsed_condition()}
              GROUP BY st.id, st.title
              HAVING count(DISTINCT p.source_id) >= 2 AND max(coalesce(p.published_at, p.discovered_at)) >= %(now)s - make_interval(days => {STORY_DAYS})
            )
            SELECT s.story_title, s.sources, best.article_id, best.url, best.title, best.score, an.title_zh, an.summary_zh
            FROM stories_now s
            CROSS JOIN LATERAL (
              SELECT p.article_id, p.url, p.title, p.score FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
              WHERE f.story_id = s.id AND {evidence_condition()} AND {browsed_condition()} ORDER BY p.score DESC NULLS LAST, p.article_id LIMIT 1
            ) best
            LEFT JOIN LATERAL (SELECT title_zh, summary_zh FROM analyses WHERE article_id = best.article_id AND origin = 'model' ORDER BY id DESC LIMIT 1) an ON true
            ORDER BY s.sources DESC, s.latest DESC LIMIT %(k)s""",
        {**scope_params(now or datetime.now(UTC)), "k": k},
    ).fetchall()
    return [{**_row(r), "title": r["story_title"], "summary": f"（{r['sources']} 個來源報導）{r['title_zh'] or r['title']}。{r['summary_zh'] or ''}"} for r in rows]


def popular(conn, k: int = RECOMMEND_COUNT, now: datetime | None = None) -> list[dict[str, Any]]:
    """What to recommend with no topic: the events several sources reported, then the newest selected articles to fill the list."""
    picked = top_stories(conn, k, now)
    seen = {h["article_id"] for h in picked}
    return (picked + [h for h in latest_selected(conn, k, now) if h["article_id"] not in seen])[:k]


def recommend(conn, topic: str, k: int = RECOMMEND_COUNT, embed_fn: EmbedFn = embed) -> list[dict[str, Any]]:
    """Articles near `topic`, or the events several sources reported when there is no topic."""
    if not topic:
        return _numbered(popular(conn, k))
    return _numbered([h for h in search(conn, topic, k, embed_fn) if h["score"] >= RECOMMEND_MIN_SCORE])


def cited_numbers(text: str) -> list[int]:
    """The article numbers an answer points to with [1], [2] ..., in order, once each."""
    return list(dict.fromkeys(int(n) for n in re.findall(r"\[(\d{1,2})\]", text)))


def _article_text(hits: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{h['n']}] {h['title']}\n{h['summary']}" for h in hits)


Step = Callable[[str], None]


@dataclass
class Plan:
    """What to do with a message: show `reply` as it is, or give `messages` to the model; `sources` are the articles it draws on."""

    kind: str
    sources: list[dict[str, Any]]
    messages: Messages | None = None
    reply: str | None = None


def plan(conn, question: str, history: Messages | None = None, k: int = 5, embed_fn: EmbedFn = embed, chat_fn: ChatFn = chat, step: Step | None = None) -> Plan:
    """`step`, when given, is told in words what is being done as it happens ("searching the articles", "found 5"), for a page that shows progress."""
    say = step or (lambda text: None)
    say("判斷你想做什麼")
    kind, topic = route(question, history, chat_fn)
    past = _recent(history)
    if kind == "chat":
        return Plan(kind, [], [{"role": "system", "content": CHAT_SYSTEM}, *past, {"role": "user", "content": question}])
    if kind == "recommend":
        say("挑選站內文章")
        items = recommend(conn, topic, RECOMMEND_COUNT, embed_fn)
        if not items:
            return Plan(kind, [], reply=NO_RECOMMENDATION)
        say(f"找到 {len(items)} 篇可以推薦的文章")
        return Plan(kind, items, [{"role": "system", "content": RECOMMEND_SYSTEM}, *past, {"role": "user", "content": f"文章清單：\n{_article_text(items)}\n\n要求：{question}"}])
    say("搜尋站內相關文章")
    hits = retrieve(conn, question, history, k, embed_fn, chat_fn)
    if not hits:
        return Plan(kind, [], reply=NOT_FOUND)
    say(f"找到 {len(hits)} 篇相關文章")
    return Plan(kind, hits, [{"role": "system", "content": ASK_SYSTEM}, *past, {"role": "user", "content": f"文章：\n{_article_text(hits)}\n\n問題：{question}"}])


def ask(conn, question: str, k: int = 5, embed_fn: EmbedFn = embed, chat_fn: ChatFn = chat, history: Messages | None = None) -> dict[str, Any]:
    p = plan(conn, question, history, k, embed_fn, chat_fn)
    answer = p.reply if p.reply is not None else chat_fn(p.messages or [])
    return {"question": question, "kind": p.kind, "found": bool(p.sources), "answer": answer, "sources": p.sources}
