"""The writing side of the analysis: the prefilter's and the content understanding's inputs, the title/summary prompts for everything else, the output parsing and the
deterministic guards. The wording lives in the industry pack (pack/prompts); a failed guard falls back without a repair call. Port of editorial/writing.ts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import regex

from newsroom.engine._readability_p1 import js_trim, jsre
from newsroom.engine.lib.text import is_chinese_text as looks_zh
from newsroom.engine.lib.text import strip_tag_markup
from newsroom.engine.llm import js_length
from newsroom.engine.pack import load
from newsroom.engine.prompts import prompt_text
from newsroom.engine.sources.dates import describe_publication_time
from newsroom.engine.sources.x_article import only_x_article_link

MAX_BODY_CHARS = 60_000  # a body longer than this is cut (whole bodies are sent; a few run past the context)
_HAN = re.compile("[一-鿿]")
_WS = jsre(r"\s+")


def prefilter_system() -> str:
    return prompt_text("prefilter")


def understand_system() -> str:
    return prompt_text("understand")


def cap_body(s: str) -> str:
    return s[:MAX_BODY_CHARS] if js_length(s) > MAX_BODY_CHARS else s


def clamp_text(s: str, max_chars: int) -> str:
    return s if len(s) <= max_chars else s[:max_chars] + "…"


def is_short_tweet(text: str) -> bool:
    """Short tweet: under 100 characters of Chinese, under 500 of other text."""
    if not text:
        return False
    cjk = len(_HAN.findall(text))
    return js_length(text) < (100 if cjk > js_length(text) * 0.3 else 500)


_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>[\s\S]*?</\1>", re.I)


def clean_article_text_for_llm(s: str) -> str:
    """HTML, URLs (whose /2025/ paths models took for years) and entities out of article text."""
    if not s:
        return ""
    out = strip_tag_markup(_SCRIPT_STYLE.sub(" ", s))
    out = re.sub(r"https?://\S+", " ", out, flags=re.I)
    for entity, repl in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        out = re.sub(re.escape(entity), repl.replace("\\", "\\\\"), out, flags=re.I)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return js_trim(out)


def chinese_density(s: str) -> float:
    chinese = len(_HAN.findall(s))
    total = js_length(_WS.sub("", s))
    return 0 if total == 0 else chinese / total


def _strip_noise(s: str) -> str:
    return re.sub(r"#[A-Za-z0-9_]+", " ", re.sub(r"@[A-Za-z0-9_]+", " ", re.sub(r"https?://\S+", " ", s)))


_ENGLISH_RUN = re.compile(r"[A-Za-z][A-Za-z0-9+.#/-]*(?:\s+[A-Za-z][A-Za-z0-9+.#/-]*)+")


def needs_short_tweet_translation(text: str) -> bool:
    """A short tweet in Chinese needs no translation; mixed or English ones do."""
    clean = _strip_noise(text)
    if not looks_zh(clean):
        return True
    if chinese_density(clean) < 0.65:
        return True
    return any(js_length(_WS.sub("", run)) >= 10 for run in _ENGLISH_RUN.findall(clean))


# -- the material as the prefilter and the content understanding read it ---------------------------------------------------------------------------


def _unfetched_x_article(a: dict) -> bool:
    """The post is an X Article's link whose article could not be fetched."""
    return bool(a.get("xPost")) and a.get("bodyStatus") != "ok" and only_x_article_link(str(a["xPost"].get("text") if a["xPost"].get("text") is not None else ""))


def material_quality(a: dict) -> str:
    if a.get("xPost"):
        return "完整正文（來自 RSS / API 自帶的 content 欄位）"
    if a.get("bodyText"):
        return "完整正文（抓自原始網頁）" if a["source"].get("fetchesBody") else "完整正文（來自 RSS / API 自帶的 content 欄位）"
    if a.get("excerpt"):
        return "僅摘要（feed 未提供完整正文）"
    if a.get("bodyStatus") == "unconfirmed":
        return "抓取失敗，僅標題可用"
    return "無有效文本"


def _truthy(v) -> bool:
    return not (v is None or v is False or v == 0 or v == "")


def render_context(a: dict, annotate_quoted: bool = False) -> str:
    """The material as the prefilter and the content understanding read it."""
    src = a["source"]
    x = a.get("xPost")
    lines = [f"【來源】{src['name']}（{src['kind']}，tier={src.get('tier') or '未分級'}）"]
    if src.get("tags"):
        lines.append(f"【來源標籤】{', '.join(src['tags'])}")
    name = (x or {}).get("authorName") or a.get("author")
    handle = (x or {}).get("handle")
    if name or handle:
        lines.append(f"【作者】{' · '.join(p for p in [name, f'@{handle}' if handle else None] if p)}")
    if a.get("publishedAt"):
        lines.append(f"【發布時間】{describe_publication_time(a['publishedAt'], a.get('publicationTime'))}")
    media = ((x or {}).get("media") if (x or {}).get("media") is not None else a.get("media")) or []
    images = sum(1 for m in media if m.get("kind") == "image")
    videos = sum(1 for m in media if m.get("kind") == "video")
    parts = [p for p in [f"{images} 張圖" if images else None, f"{videos} 個影片" if videos else None, "含 X 長文連結（正文未抓到）" if _unfetched_x_article(a) else None] if p]
    if parts:
        lines.append(f"【媒體】{' · '.join(parts)}")
    lines.append(f"【原文連結】{a['url']}")
    lines.append(f"【標題】{a['title']}")
    quoted = x["quoted"] if x and x.get("quoted") and x["quoted"].get("text") else None
    if quoted:
        label = f"@{quoted['handle']}" if quoted.get("handle") else "原推"
        if annotate_quoted:
            lines.append(f"【引用 {label}】（以下是作者轉發/引用的**他人**內容，不是作者本人的產出）")
            lines.append(str(quoted["text"]))
        else:
            lines.append(f"【引用 {label}】{quoted['text']}")
    lines.append("")
    lines.append("【正文（作者自己的內容）】" if annotate_quoted and quoted else "【正文】")
    if x:
        body = str(x.get("text") if x.get("text") is not None else a["title"])
    else:
        body = a.get("bodyText") if a.get("bodyText") is not None else a.get("excerpt") if a.get("excerpt") is not None else "(無正文)"
    lines.append(cap_body(body))
    lines.append("")
    lines.append(f"【材料質量】{material_quality(a)}")
    return "\n".join(lines)


def prefilter_user(a: dict) -> str:
    """The prefilter's user message: the context as a JSON string (the prompt was tuned on this form)."""
    import json

    return json.dumps(render_context(a), ensure_ascii=False, separators=(",", ":"))


def missing_evidence(a: dict) -> bool:
    """No text beyond the title: no body, feed excerpt, post or quoted text."""
    x = a.get("xPost") or {}
    text = x.get("text")
    quoted = (x.get("quoted") or {}).get("text")
    return (
        not js_trim(a.get("bodyText") or "") and not js_trim(a.get("excerpt") or "") and not js_trim(str(text if text is not None else ""))
        and not js_trim(str(quoted if quoted is not None else ""))
    )  # fmt: skip


def understand_user(a: dict) -> str:
    return "\n\n".join(["請按系統規則理解以下單篇材料，一次返回全部六個欄位。", render_context(a, annotate_quoted=True)])


# -- identity context and guard ----------------------------------------------------------------------------------------------------------------


def _lexicon() -> list[dict]:
    return load("taxonomy")["IDENTITY_LEXICON"]


def lexicon_name(entity_id: str) -> str | None:
    return next((e["name"] for e in _lexicon() if e["id"] == entity_id), None)


def match_entity_ids(texts: list) -> list[str]:
    """Known companies the texts name, by the pack's identity lexicon (each text on its own)."""
    items = [t for t in texts if isinstance(t, str) and js_trim(t)]
    return [e["id"] for e in _lexicon() if any(p.search(t) for p in e["patterns"] for t in items)]


_GPT_JOIN = re.compile(r"\b(gpt|glm)\s+(?=[o\d])", re.I)


def surface_variants(texts: list) -> list[str]:
    """A translation may rejoin a name (GPT 5.5 → GPT-5.5): the input counts in both spellings."""
    exact = [t for t in texts if isinstance(t, str) and js_trim(t)]
    return [*exact, *[_GPT_JOIN.sub(lambda m: m.group(1) + "-", t) for t in exact]]


def publisher_entity_id(url: str | None) -> str | None:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return None
    if not host or not urlsplit(url or "").scheme:
        return None
    for e in load("taxonomy")["PUBLISHER_DOMAINS"]:
        if any(host == d or host.endswith(f".{d}") for d in e["domains"]):
            return e["entityId"]
    return None


@dataclass
class TranslateInput:
    title: str
    text: str
    source_kind: str
    source_name: str | None = None
    document_url: str | None = None
    source_owner_entity_id: str | None = None
    main_text: str | None = None  # the main post of a tweet (not the quoted one), for the short/long decision
    quoted_text: str | None = None
    quoted_author: str | None = None
    published_at: datetime | None = None
    publication_time: str | None = None
    extra: dict = field(default_factory=dict)


def translate_input_of(a: dict) -> TranslateInput:
    x = a.get("xPost")
    is_x = a["source"]["kind"] == "x_search" or bool(x)
    main_text = str(x.get("text") if x and x.get("text") is not None else a["title"]) if is_x else None
    quoted = (x or {}).get("quoted") or {}
    return TranslateInput(
        title=a["title"],
        text=(main_text or "") if is_x else (a.get("bodyText") if a.get("bodyText") is not None else a.get("excerpt") if a.get("excerpt") is not None else ""),
        source_kind="x_search" if is_x else a["source"]["kind"],
        source_name=a["source"]["name"], document_url=a["url"], source_owner_entity_id=a["source"].get("ownerEntityId"), main_text=main_text,
        quoted_text=str(quoted["text"]) if quoted.get("text") else None, quoted_author=str(quoted["handle"]) if quoted.get("handle") else None,
        published_at=a.get("publishedAt"), publication_time=a.get("publicationTime"),
    )  # fmt: skip


def identity_context(inp: TranslateInput) -> dict:
    publisher = publisher_entity_id(inp.document_url)
    owner = inp.source_owner_entity_id if inp.source_owner_entity_id and lexicon_name(inp.source_owner_entity_id) else None
    texts = [inp.title, inp.text, inp.main_text, inp.quoted_text, inp.source_name]
    allowed = set(match_entity_ids(surface_variants(texts)))
    joined = "\n".join(t for t in texts if t)
    for alias in load("taxonomy")["IDENTITY_CONTEXT_ALIASES"]:
        if alias["pattern"].search(joined):
            allowed.add(alias["entityId"])
    if publisher:
        allowed.add(publisher)
    if owner:
        allowed.add(owner)
    return {"allowed": sorted(allowed), "publisher": publisher, "owner": owner}


def identity_prompt(inp: TranslateInput) -> str:
    ctx = identity_context(inp)
    facts = []
    if ctx["publisher"]:
        facts.append(f"文件發布域主體={lexicon_name(ctx['publisher']) or ctx['publisher']}")
    if ctx["owner"]:
        facts.append(f"來源賬號主體={lexicon_name(ctx['owner']) or ctx['owner']}")
    return prompt_text("identity-context", {"facts": "；".join(facts) if facts else "未識別到明確發布主體"})


def enforce_identity(inp: TranslateInput, copy: dict) -> dict:
    """A model only words the copy; it cannot introduce a company the input does not name. A title that does falls back to the original Chinese title (or
    nothing), a summary that does is dropped."""
    allowed = set(identity_context(inp)["allowed"])
    bad_title = [i for i in match_entity_ids([copy["titleZh"]]) if i not in allowed]
    bad_summary = [i for i in match_entity_ids([copy["summaryZh"]]) if i not in allowed]
    return {
        "titleZh": (inp.title if looks_zh(inp.title) else "") if bad_title else copy["titleZh"],
        "summaryZh": "" if bad_summary else copy["summaryZh"],
        "identityGuard": {"outcome": "fallback" if bad_title or bad_summary else "pass", "unsupportedTitleEntityIds": bad_title, "unsupportedSummaryEntityIds": bad_summary},
    }


# -- answer-first summary length ----------------------------------------------------------------------------------------------------------------

_SENTENCES = regex.compile(r"[^。！？!?]+[。！？!?]?")
_CLAUSES = regex.compile(r"[^，；,;]+[，；,;]?")


def compact_answer_first_summary(summary: str, max_chars: int = 190) -> str:
    text = re.sub(r"\s*\n+\s*", " ", js_trim(summary))
    if js_length(text) <= max_chars:
        return text
    result = ""
    for sentence in _SENTENCES.findall(text) or [text]:
        if js_length(result + sentence) > max_chars:
            break
        result += sentence
        if js_length(result) >= 80:
            break
    # Whole sentences, even a short one, read better than half of the next.
    if result:
        return js_trim(result)
    # The first sentence alone is too long: cut at a clause boundary, never inside a name, number or enumeration.
    for clause in _CLAUSES.findall(text) or [text]:
        if js_length(result + clause) + 1 > max_chars:
            break
        result += clause
        if js_length(result) >= 80:
            break
    return re.sub(r"[，；,;]$", "", result) + "。" if js_length(result) >= 50 else text


def answer_first_summary_length_ok(summary: str, inp: TranslateInput) -> bool:
    trimmed = js_trim(summary)
    source_length = js_length(js_trim(inp.text if inp.source_kind == "x_search" else clean_article_text_for_llm(inp.text)))
    sentences = len([p for p in (js_trim(x) for x in re.split(r"[。！？!?]+", trimmed)) if p])
    rich = source_length >= 500
    return js_length(trimmed) <= 200 and js_length(trimmed) >= (80 if rich else 50) and sentences <= 3 and (not rich or sentences >= 2)


def is_short_tweet_input(inp: TranslateInput) -> bool:
    return inp.source_kind == "x_search" and is_short_tweet(inp.main_text or inp.title)


def finalize_copy(inp: TranslateInput, copy: dict) -> dict:
    """The length rule (compacted without another call) and the identity guard, for any writing model."""
    summary = copy["summaryZh"]
    if not is_short_tweet_input(inp) and summary and not answer_first_summary_length_ok(summary, inp):
        summary = compact_answer_first_summary(summary)
    return enforce_identity(inp, {"titleZh": copy["titleZh"], "summaryZh": summary})


# -- title/summary prompts for items the content understanding does not write -------------------------------------------------------------------


def _source_name(name: str | None) -> str:
    return (name.strip() if name else "") or "（未註明）"


def anchor_date(d: datetime | None) -> str:
    if d is None:
        return "未註明"
    return d.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def build_article_prompt(inp: TranslateInput) -> str:
    return prompt_text("summarize-article", {
        "publishedDate": inp.publication_time if inp.publication_time is not None and len(inp.publication_time) == 10 else anchor_date(inp.published_at),
        "today": anchor_date(datetime.now().astimezone()),
        "sourceName": _source_name(inp.source_name),
        "identity": identity_prompt(inp),
        "title": inp.title,
        "body": clamp_text(clean_article_text_for_llm(inp.text), 6000) if inp.text else prompt_text("summarize-article-empty"),
    })  # fmt: skip


def _quoted_block(inp: TranslateInput, name: str) -> str:
    """The quoted post's block, appended after a blank line when there is one."""
    if not inp.quoted_text:
        return ""
    return "\n\n" + prompt_text(name, {"quotedLabel": f"@{inp.quoted_author}" if inp.quoted_author else "引用推文", "quotedText": clamp_text(inp.quoted_text, 1500)})


def build_short_tweet_prompt(inp: TranslateInput) -> str:
    post = clamp_text(inp.main_text or inp.text or inp.title, 4000)
    return prompt_text("summarize-short-post", {"sourceName": _source_name(inp.source_name), "identity": identity_prompt(inp), "post": post}) + _quoted_block(inp, "summarize-short-post-quoted")


def build_long_tweet_prompt(inp: TranslateInput) -> str:
    post = clamp_text(inp.main_text or inp.text or inp.title, 4000)
    return prompt_text("summarize-long-post", {"sourceName": _source_name(inp.source_name), "identity": identity_prompt(inp), "post": post}) + _quoted_block(inp, "summarize-long-post-quoted")


_ECHO_LINE = re.compile(r"^(來源[:：]|【已核驗身份上下文】|這些事實只用於防止|原始標題[:：]|【時間錨點】)")


def strip_echo(text: str) -> str:
    """The answer without prompt lines repeated at its end."""
    lines = text.split("\n")
    while lines and (js_trim(lines[-1]) == "" or _ECHO_LINE.search(js_trim(lines[-1]))):
        lines.pop()
    return js_trim("\n".join(lines))


_LABELS = {"title": re.compile(r"^title_zh\s*[:：]\s*(.*)$"), "summary": re.compile(r"^summary_zh\s*[:：]\s*(.*)$"), "body": re.compile(r"^body_zh\s*[:：]\s*(.*)$")}
_ANY_LABEL = re.compile(r"^(title_zh|summary_zh|body_zh)\s*[:：]")


def parse_translate_output(text: str) -> dict:
    """`title_zh:` / `summary_zh:` / `body_zh:` lines, with fallbacks for answers that drop the labels."""
    title = summary = body = ""
    title_line = summary_line = body_line = -1
    lines = re.split(r"\r?\n", text)
    for i, raw_line in enumerate(lines):
        t = js_trim(raw_line)
        m = _LABELS["title"].match(t)
        if m:
            title, title_line = js_trim(m.group(1)), i
            continue
        m = _LABELS["summary"].match(t)
        if m:
            summary, summary_line = js_trim(m.group(1)), i
            continue
        m = _LABELS["body"].match(t)
        if m:
            body, body_line = js_trim(m.group(1)), i
    # A title without a labelled summary or body: the lines after it are the summary.
    if title and not summary and body_line < 0 and title_line >= 0:
        rest = [x for x in (js_trim(line) for line in lines[title_line + 1 :]) if x]
        if rest:
            summary = "\n".join(rest)
    # A summary split over lines: join the unlabelled lines after it.
    if summary_line >= 0:
        more: list[str] = []
        for line in lines[summary_line + 1 :]:
            t = js_trim(line)
            if not t:
                continue
            if _ANY_LABEL.match(t):
                break
            more.append(t)
        parts = [p for p in [summary, *more] if p]
        if parts:
            summary = "\n".join(parts)
    # A body over lines keeps its paragraph breaks.
    if body_line >= 0:
        more = []
        for line in lines[body_line + 1 :]:
            if _ANY_LABEL.match(js_trim(line)):
                break
            more.append(line)
        while more and js_trim(more[-1]) == "":
            more.pop()
        parts = [body, *more] if body else more
        if parts:
            body = "\n".join(parts)
    if not title and not summary and not body:
        rest = [x for x in re.split(r"\r?\n", js_trim(text)) if x]
        if len(rest) >= 2:
            title = js_trim(rest[0])
            summary = js_trim("\n".join(rest[1:]))
        elif len(rest) == 1:
            title = js_trim(rest[0])
    return {"titleZh": title, "summaryZh": strip_echo(summary), "bodyZh": strip_echo(body)}
