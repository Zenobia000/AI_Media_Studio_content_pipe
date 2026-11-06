"""analyzeArticle: the judging and writing steps, each with its own prompt from the industry pack (pack/prompts).

Without material (only a title) or a displayable original post, the item waits for a new material revision before any step.
  1. prefilter: does the material belong to this industry at all (wide recall). Only BLOCK stops an item;
  2. score: two independent scores against the source's threshold (tier and class) decide 精選;
  3. structure: category, tags, subjects and the current news fact, beside scoring;
  4. writing, once the structure is in: the Chinese title, summary and reason by the content understanding for selected and near-selected items, by the cheaper
     title/summary prompts for the rest; copy that uses a word the site keeps from readers goes back once to change only those.
Port of editorial/analyze.ts."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from newsroom.engine import models as M
from newsroom.engine import vocabulary as V
from newsroom.engine import writing as W
from newsroom.engine._readability_p1 import js_trim
from newsroom.engine.analyze_input import build_material, first_image_part, load_analyze_input
from newsroom.engine.extract import page_fetchable
from newsroom.engine.lib.text import collapse_whitespace
from newsroom.engine.llm import SchemaError, chat_json, js_length
from newsroom.engine.pack import load
from newsroom.engine.posts import original_post_copy
from newsroom.engine.prompts import prompt_text, prompt_version
from newsroom.engine.receipts import complete_receipt, failure_kind
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.sources.dates import describe_publication_time
from newsroom.engine.sources.json_list import _js_number, js_str

SCORE_CALLS = 2  # independent score calls per article; their sum decides, their mean (floored) is shown


def prompt_versions() -> dict[str, str]:
    return {
        "prefilter": prompt_version("prefilter"),
        "score": prompt_version("selection-score"),
        "understand": prompt_version("understand"),
        "summarize": prompt_version(
            "summarize-article", "summarize-article-empty", "summarize-short-post", "summarize-short-post-quoted", "summarize-long-post", "summarize-long-post-quoted", "identity-context"
        ),
        "structure": prompt_version("structure"),
        "wording": prompt_version("mend-wording"),
    }


def analyze_prompt_version() -> str:
    return "+".join(prompt_versions().values())


def selection_prompt_version() -> str:
    v = prompt_versions()
    return "+".join([v["prefilter"], v["score"]])


def _selection() -> dict:
    return load("selection")["SELECTION"]


def selection_threshold(source: dict) -> float | None:
    """The threshold on the mean score for a source: its tier's, raised to its class's floor; a class never lowers it. Tiers without a threshold are not
    scored for 精選."""
    sel = _selection()
    tier = sel["thresholds"].get(source["tier"])
    if tier is None:
        return None
    cls = (sel["classes"].get(source.get("selectionClass")) or {}).get("threshold") if source.get("selectionClass") else None
    return max(tier, cls or 0)


def understand_floor() -> float:
    return _selection()["understandFloor"]


def score_call(model: str) -> dict:
    return M.models().get(model, {}).get("scoreCall") or {"temperature": 0.2, "maxTokens": 1024, "timeoutMs": 120_000}


def score_system() -> str:
    return prompt_text("selection-score")


# -- score input ----------------------------------------------------------------------------------------------------------------------------


def score_input_time(at: datetime) -> str:
    """The score input's time: Beijing time, ISO 8601 with +08:00 (the form the prompt was tuned on)."""
    local = at.astimezone(ZoneInfo("Asia/Shanghai"))
    ms = local.microsecond // 1000
    return local.strftime("%Y-%m-%dT%H:%M:%S") + (f".{ms:03d}" if ms else "") + "+08:00"


def build_score_input(a: dict) -> str:
    """The score input: no source facts (the prompt forbids guessing them), the publication time, the original title and the whole body."""
    x = a.get("xPost")
    if x:
        quoted = x.get("quoted") or {}
        quoted_text = f"\n\n[引用 {'@' + quoted['handle'] if quoted.get('handle') else '原推文'}]：{quoted['text']}" if quoted.get("text") else ""
        body = js_trim(f"{js_trim(str(x.get('text') if x.get('text') is not None else ''))}{quoted_text}")
    else:
        body = js_trim(cast(str, a.get("bodyText") if a.get("bodyText") is not None else a.get("excerpt") if a.get("excerpt") is not None else ""))
    if not body:
        body = a["title"]
    at = a.get("publishedAt")
    if at:
        pt = a.get("publicationTime")
        when = score_input_time(at) if "publicationTime" not in a or (pt is not None and "T" in pt) else describe_publication_time(at, pt)
    else:
        when = "未知（收錄時間不代表發布時間）"
    return "\n\n".join([
        "請按系統規則評估以下單篇材料所代表的事件。只輸出 attentionScore。",
        f"【發布時間（北京時間）】\n{when}",
        f"【標題】\n{js_trim(a['title'])}",
        f"【完整正文】\n{body[: W.MAX_BODY_CHARS] if js_length(body) > W.MAX_BODY_CHARS else body}",
    ])  # fmt: skip


# -- the step outputs (zod schemas of the original) -----------------------------------------------------------------------------------------------


def _is_str(v, max_len: int | None = None, min_len: int = 0, trim: bool = False):
    if not isinstance(v, str):
        return None
    s = js_trim(v) if trim else v
    if js_length(s) < min_len or (max_len is not None and js_length(s) > max_len):
        return None
    return s


def _object(v) -> dict:
    if not isinstance(v, dict):
        raise SchemaError("expected an object")
    return v


def prefilter_schema(data) -> dict:
    d = _object(data)
    raw = d.get("label")
    label = js_trim(js_str("" if raw is None else raw)).upper()
    if label not in ("PASS", "BLOCK", "UNKNOWN"):
        raise SchemaError("label must be PASS, BLOCK or UNKNOWN")
    reason = _is_str(d.get("reason"), 200)
    return {"label": label, "reason": reason if reason is not None else ""}


def score_schema(data) -> dict:
    d = _object(data)
    n = _js_number(d.get("attentionScore")) if "attentionScore" in d else float("nan")
    if n != n or n in (float("inf"), float("-inf")) or n != int(n) or n < 0 or n > 100:
        raise SchemaError("attentionScore must be an integer from 0 to 100")
    return {"attentionScore": int(n)}


def _fact(v) -> dict | None:
    """FactSchema: nullable, and a fact that does not fit is none."""
    if not isinstance(v, dict):
        return None
    title = _is_str(v.get("title"), 80)
    if title is None:
        return None
    out: dict = {"title": title}
    for key, limit in (("subject", 80), ("action", 80), ("object", 160), ("occurredAt", None)):
        if key in v:
            if v[key] is None:
                out[key] = None
            else:
                s = _is_str(v[key], limit)
                if s is None:
                    return None
                out[key] = s
    ev = _is_str(v.get("evidence"), 600, trim=True)
    out["evidence"] = ev if v.get("evidence") is not None and ev is not None else None
    conditions: list[dict[str, Any] | None] = []
    if isinstance(v.get("conditions"), list):
        for item in v["conditions"]:
            if not isinstance(item, dict):
                conditions.append(None)
                continue
            quote = _is_str(item.get("quote"), 400, min_len=1, trim=True)
            if quote is None:
                conditions.append(None)
                continue
            text = _is_str(item.get("text"), 200, min_len=1, trim=True)
            conditions.append({**({"text": text} if text is not None else {}), "quote": quote})
    out["conditions"] = conditions
    return out


def _string_list(v, max_items: int) -> list[str]:
    return v if isinstance(v, list) and all(isinstance(x, str) for x in v) and len(v) <= max_items else []


def structure_schema(data) -> dict:
    d = _object(data)
    scope = d.get("scope") if d.get("scope") in ("single", "composite", "unknown") else "unknown"
    from newsroom.engine import taxonomy as T

    category = d.get("category") if isinstance(d.get("category"), str) and d["category"] in T.category_keys() else None
    return {"scope": scope, "category": category, "tags": _string_list(d.get("tags"), 12), "subjects": _string_list(d.get("subjects"), 6), "fact": _fact(d.get("fact"))}


def understand_schema(data) -> dict:
    d = _object(data)
    if d.get("itemType") not in V.item_types():
        raise SchemaError("itemType must be one of the pack's item types")
    title = _is_str(d.get("titleZh"), 200, min_len=1, trim=True)
    summary = _is_str(d.get("summaryZh"), 4000, min_len=1, trim=True)
    if title is None or summary is None:
        raise SchemaError("titleZh and summaryZh are required")
    judgment = _is_str(d.get("editorialJudgment"), 400)
    return {
        "itemType": d["itemType"], "authorRole": d.get("authorRole") if d.get("authorRole") in ("principal", "observer", "relayer") else "relayer",
        "tags": _string_list(d.get("tags"), 12), "editorialJudgment": judgment if judgment is not None else "", "titleZh": title, "summaryZh": summary,
    }  # fmt: skip


def summarize_schema(data) -> dict:
    d = _object(data)
    if not all(isinstance(d.get(k), str) for k in ("titleZh", "summaryZh", "bodyZh")):
        raise SchemaError("titleZh, summaryZh and bodyZh must be strings")
    return d


def mend_schema(data) -> dict:
    d = _object(data)
    title = _is_str(d.get("titleZh"), min_len=1, trim=True)
    summary = _is_str(d.get("summaryZh"), min_len=1, trim=True)
    if title is None or summary is None:
        raise SchemaError("titleZh and summaryZh are required")
    reason = d.get("reasonZh")
    return {"titleZh": title, "summaryZh": summary, "reasonZh": js_trim(reason) if isinstance(reason, str) else None}


ZH_COUNT = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]


def structure_system() -> str:
    """The structure step owns the public category and tags, as well as grouping evidence (filled from the pack's vocabulary)."""
    n = len(load("taxonomy")["CATEGORIES"])
    return prompt_text("structure", {
        "categoryCount": ZH_COUNT[n] if n < len(ZH_COUNT) else str(n),
        "categoryGuide": V.category_guide(),
        "categoryTags": "、".join(V.category_tags()),
        "topicTags": "、".join(V.topic_tags()),
        "entityTags": "、".join(V.entity_tags()),
        "entities": "，".join(f"{i}（{'/'.join(e['aliases'][:3])}）" for i, e in V.entities().items()),
    })  # fmt: skip


def normalize_structure(data: dict, a: dict) -> dict:
    """Keep only quotes present both in the original material and in the text the structure model saw."""
    visible = collapse_whitespace(build_material(a))
    x = a.get("xPost")
    if x:
        originals = [str(x.get("text") if x.get("text") is not None else a["title"]), str((x.get("quoted") or {}).get("text") if (x.get("quoted") or {}).get("text") is not None else "")]
    else:
        originals = [cast(str, a.get("bodyText") if a.get("bodyText") is not None else a.get("excerpt") if a.get("excerpt") is not None else "")]
    originals = [collapse_whitespace(o) for o in originals]
    # A title is not the unseen contents of a video/article, regardless of the model's confidence.
    scope = data["scope"] if any(originals) else "unknown"

    def grounded(quote):
        text = collapse_whitespace(quote if quote is not None else "")
        return text if text and text in visible and any(text in o for o in originals) else None

    fact = None
    if any(originals) and scope != "composite" and data["fact"]:
        conditions = []
        for c in data["fact"]["conditions"]:
            if not c:
                continue
            quote = grounded(c["quote"])
            if quote:
                conditions.append({"text": c.get("text") or quote, "quote": quote})
        fact = {**data["fact"], "evidence": grounded(data["fact"]["evidence"]), "conditions": conditions[:4]}
    entities = V.entities()
    subjects = list(dict.fromkeys(s for s in (x_.strip().lower() for x_ in data["subjects"]) if s in entities))
    return {"category": data["category"], "tags": V.normalize_tags(data["tags"]), "subjects": subjects, "scope": scope, "fact": fact}


# -- the steps ---------------------------------------------------------------------------------------------------------------------------------


def is_content_filter(error: BaseException) -> bool:
    return failure_kind(error) == "refused" and re.search(r'contentFilter|"1301"', str(error)) is not None


def waits_for_page(a: dict) -> bool:
    """Only a title or a feed summary, and a page to fetch: the article is judged on the page."""
    return a.get("bodyStatus") == "pending" and not a.get("bodyText") and not a.get("xPost") and page_fetchable(a["url"], a["source"]["kind"])


def _check_running() -> None:
    """A stopping worker finishes the stage it is in, never starts the next."""
    if SHUTDOWN.is_set():
        raise InterruptedError("worker is stopping")


def _subject(a: dict) -> str:
    return f"article:{a['id']}@{a['revision']}"


def _tagged(attempt_tag: str | None, step: str) -> str | None:
    return ":".join(p for p in [attempt_tag, step] if p) or None


def run_prefilter(pool, a: dict, opts: dict) -> dict:
    model = M.model_for(_conn(pool), "prefilter")
    _check_running()
    res = chat_json(pool, {
        "model": model, "purpose": "prefilter_article", "subject": _subject(a), "prompt_version": prompt_versions()["prefilter"], "system": W.prefilter_system(),
        "user": W.prefilter_user(a), "schema": prefilter_schema, "temperature": 0, "max_tokens": 512, "attempt_tag": opts.get("attemptTag"),
    })  # fmt: skip
    # A BLOCK without text to back it (an original post that is only media or a link) counts as UNKNOWN (which goes on).
    label = "UNKNOWN" if res["data"]["label"] == "BLOCK" and W.missing_evidence(a) else res["data"]["label"]
    return {"label": label, "reason": res["data"]["reason"], "model": res["model"], "receiptId": res["receiptId"], "reused": res["reused"]}


def _conn(pool):
    """A short-lived connection for the settings reads the model choice makes."""
    return _PoolConn(pool)


class _PoolConn:
    def __init__(self, pool) -> None:
        self.pool = pool

    def execute(self, *args, **kwargs):
        with self.pool.connection() as conn:
            cur = conn.execute(*args, **kwargs)
            return _Rows(cur.fetchall() if cur.description else [])


class _Rows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def run_scores(pool, a: dict, threshold: float, opts: dict) -> dict:
    model = opts.get("scoreModel") or M.model_for(_conn(pool), "score")
    call = score_call(model)
    user = build_score_input(a)
    values: list[int] = []
    receipt_ids: list[int] = []
    reused = True
    # One after the other: the second call reuses the provider's cached prompt.
    for i in range(SCORE_CALLS):
        _check_running()
        try:
            res = chat_json(pool, {
                "model": model, "purpose": "score_article", "subject": _subject(a), "prompt_version": prompt_versions()["score"], "system": score_system(), "user": user,
                "schema": score_schema, "temperature": call["temperature"], "max_tokens": call["maxTokens"], "timeout_ms": call["timeoutMs"],
                # Each call is its own paid request; an explicit re-evaluation gets new ones.
                "attempt_tag": _tagged(opts.get("attemptTag"), f"score-{i + 1}"),
            })  # fmt: skip
            values.append(res["data"]["attentionScore"])
            receipt_ids.append(res["receiptId"])
            reused = reused and res["reused"]
        except Exception as error:
            # The model's content filter declines the material (Zhipu 1301): not scored, so not selected.
            if is_content_filter(error):
                return {"model": model, "threshold": threshold, "values": values, "receiptIds": receipt_ids, "reused": False, "refused": True}
            raise
    return {"model": model, "threshold": threshold, "values": values, "receiptIds": receipt_ids, "reused": reused}


def run_selection_scores(pool, a: dict, opts: dict) -> dict | None:
    threshold = selection_threshold(a["source"])
    return None if threshold is None else run_scores(pool, a, threshold, opts)


def run_structure(pool, a: dict, opts: dict) -> dict:
    model = M.model_for(_conn(pool), "structure")
    _check_running()
    res = chat_json(pool, {
        "model": model, "purpose": "structure_article", "subject": _subject(a), "prompt_version": prompt_versions()["structure"], "system": structure_system(),
        "user": build_material(a), "schema": structure_schema, "temperature": 0.2, "max_tokens": 1200, "attempt_tag": _tagged(opts.get("attemptTag"), "structure"),
    })  # fmt: skip
    return {**normalize_structure(res["data"], a), "model": res["model"], "receiptId": res["receiptId"], "reused": res["reused"]}


def run_understand(pool, a: dict, opts: dict) -> dict | None:
    """Content understanding grounded in the original; None on a content-filter refusal."""
    model = M.model_for(_conn(pool), "understand")
    text = W.understand_user(a)

    def call(image):
        _check_running()
        return chat_json(pool, {
            "model": model, "purpose": "understand_article", "subject": _subject(a), "prompt_version": prompt_versions()["understand"], "system": W.understand_system(),
            "user": [{"type": "text", "text": text}, image] if image else text, "schema": understand_schema, "temperature": 0.2, "max_tokens": 16_384,
            "timeout_ms": 180_000, "attempt_tag": _tagged(opts.get("attemptTag"), "understand"),
        })  # fmt: skip

    # Image input is opt-in: an unspecified capability must not become a paid provider probe.
    image = first_image_part(a) if M.models().get(model, {}).get("vision") is True else None
    try:
        res = call(image)
    except Exception as error:
        if is_content_filter(error):
            return None
        # The model refused the image (download, format): the text is written without it.
        if not image or failure_kind(error) != "refused":
            raise
        try:
            res = call(None)
        except Exception as retry_error:
            if is_content_filter(retry_error):
                return None
            raise
    d = res["data"]
    copy = W.finalize_copy(W.translate_input_of(a), {"titleZh": d["titleZh"], "summaryZh": d["summaryZh"]})
    return {
        "kind": "understand", "model": res["model"], "titleZh": copy["titleZh"], "summaryZh": copy["summaryZh"], "reasonZh": js_trim(d["editorialJudgment"]) or None,
        "itemType": d["itemType"], "authorRole": d["authorRole"], "identityGuard": copy["identityGuard"], "receiptIds": [res["receiptId"]], "reused": res["reused"],
    }  # fmt: skip


def mend_system() -> str:
    return prompt_text("mend-wording", {"reasonLabel": load("site")["ITEM_COPY"]["reasonLabel"]})


def wording_problems(copy: dict) -> list[str]:
    """Every use of a word of the list in a copy, each as a line the model can act on: where, the words around it, and the fix."""
    reason_label = load("site")["ITEM_COPY"]["reasonLabel"]
    problems: list[str] = []
    for field_name, name in (("titleZh", "標題"), ("summaryZh", "摘要"), ("reasonZh", reason_label)):
        text = copy.get(field_name)
        if not text:
            continue
        for pattern, fix in load("wording")["READER_WORDING"]:
            for hit in pattern.finditer(text):
                problems.append(f"{name}用了“{hit.group(0)}”（“{text[max(0, hit.start() - 8) : hit.end() + 8]}”）：{fix}")
    return problems


def _numbers(c: dict) -> str:
    return " ".join(sorted(re.findall(r"\d+(?:[.,]\d+)*", f"{c['titleZh']} {c['summaryZh']} {c['reasonZh'] or ''}")))


def mend_wording(pool, a: dict, w: dict, opts: dict) -> dict:
    """Written copy that uses a word the site keeps from its readers goes back once, those words named, to change only them. The mended copy keeps the identity
    guard and is used only when it leaves fewer such words and the same numbers."""
    if w["kind"] not in ("understand", "summarize") or not w["titleZh"] or not w["summaryZh"]:
        return w
    problems = wording_problems(w)
    if not problems:
        return w
    t = W.translate_input_of(a)
    _check_running()
    try:
        res = chat_json(pool, {
            "model": M.model_for(_conn(pool), "wording"), "purpose": "mend_wording", "subject": _subject(a), "prompt_version": prompt_versions()["wording"], "system": mend_system(),
            "user": "\n\n".join([
                f"來源：{t.source_name or '（未註明）'}", f"原題：{t.title}",
                "中文稿：\n" + json.dumps({"titleZh": w["titleZh"], "summaryZh": w["summaryZh"], "reasonZh": w["reasonZh"]}, ensure_ascii=False, separators=(",", ":")),
                "問題：\n" + "\n".join(f"- {p}" for p in problems),
            ]),
            "schema": mend_schema, "temperature": 0.1, "max_tokens": 2048, "attempt_tag": _tagged(opts.get("attemptTag"), "wording"),
        })  # fmt: skip
    except Exception as error:
        # An answer that is not the copy asked for, or a refusal the same request would meet again, leaves the first copy; a busy provider is tried again later.
        if failure_kind(error) in ("unusable", "refused"):
            return w
        raise
    copy = W.finalize_copy(t, {"titleZh": res["data"]["titleZh"], "summaryZh": res["data"]["summaryZh"]})
    mended = {"titleZh": copy["titleZh"], "summaryZh": copy["summaryZh"], "reasonZh": None if w["reasonZh"] is None else (res["data"]["reasonZh"] or w["reasonZh"])}
    paid = {"receiptIds": [*w["receiptIds"], res["receiptId"]], "reused": w["reused"] and res["reused"]}
    # A guard that emptied the title or summary, a number changed or dropped, or no fewer such words: the first copy stands.
    if not mended["titleZh"] or not mended["summaryZh"] or _numbers(mended) != _numbers(w) or len(wording_problems(mended)) >= len(problems):
        return {**w, **paid}
    return {**w, **mended, "identityGuard": copy["identityGuard"] or w.get("identityGuard"), **paid}


def run_summarize(pool, a: dict, opts: dict) -> dict:
    """The title/summary prompts (articles, long and short posts)."""
    t = W.translate_input_of(a)
    is_x = t.source_kind == "x_search"
    short = W.is_short_tweet_input(t)
    main = collapse_whitespace(t.main_text or t.title)
    plain: dict[str, Any] = {"reasonZh": None, "receiptIds": [], "reused": True}
    # A short post already in Chinese is its own copy, and too little text is not written up from a title.
    if short and not W.needs_short_tweet_translation(main):
        return {"kind": "verbatim", "model": None, "titleZh": main, "summaryZh": main, **plain}
    if not short and js_length(js_trim(t.text)) < 20:
        return {"kind": "none", "model": None, "titleZh": t.title if W.looks_zh(t.title) else "", "summaryZh": "", **plain}
    route = M.model_route(_conn(pool), "summarize")
    _check_running()
    res = chat_json(pool, {
        "model": route["model"], "fallback_model": route["fallbackModel"], "purpose": "summarize_article", "subject": _subject(a), "prompt_version": prompt_versions()["summarize"],
        "system": "", "user": W.build_short_tweet_prompt(t) if short else W.build_long_tweet_prompt(t) if is_x else W.build_article_prompt(t),
        "schema": summarize_schema, "json": False, "parse": W.parse_translate_output, "temperature": 0.2, "max_tokens": 2048, "attempt_tag": _tagged(opts.get("attemptTag"), "summarize"),
    })  # fmt: skip
    p = res["data"]
    if short:
        draft = {"titleZh": p["titleZh"] or (main if W.looks_zh(main) else ""), "summaryZh": p["bodyZh"] or p["summaryZh"]}
    elif is_x:
        draft = {"titleZh": p["titleZh"], "summaryZh": p["summaryZh"] or p["bodyZh"]}
    else:
        draft = {"titleZh": p["titleZh"] or (t.title if W.looks_zh(t.title) else ""), "summaryZh": p["summaryZh"]}
    copy = W.finalize_copy(t, draft)
    return {"kind": "summarize", "model": res["model"], "titleZh": copy["titleZh"], "summaryZh": copy["summaryZh"], "reasonZh": None, "identityGuard": copy["identityGuard"],
            "receiptIds": [res["receiptId"]], "reused": res["reused"]}  # fmt: skip


def awaits_material(a: dict) -> bool:
    """Only a title, and no displayable original post: nothing to judge until a material revision arrives."""
    return W.missing_evidence(a) and not original_post_copy(a.get("xPost"), a["url"])


def score_sum(scores: dict | None) -> int | None:
    return sum(scores["values"]) if scores and not scores.get("refused") and len(scores["values"]) == SCORE_CALLS else None


def shown_score(scores: dict | None) -> int | None:
    """The score shown: the two scores' mean, floored (it never decides a half point on its own)."""
    total = score_sum(scores)
    return None if total is None else total // SCORE_CALLS


def scores_select(scores: dict | None, threshold: float | None = None) -> bool:
    """The selection rule on the scores: they add up to twice the source's threshold. The item must also pass relevance (normalize_analysis)."""
    total = score_sum(scores)
    limit = threshold if threshold is not None else (scores or {}).get("threshold")
    return total is not None and limit is not None and total >= limit * SCORE_CALLS


def run_analysis(pool, a: dict, opts: dict | None = None) -> dict:
    """Runs the steps on the material as it is (or reuses their receipts) without writing business results."""
    opts = opts or {}
    _check_running()
    # Missing article text waits for a later revision, before any paid step; displayable original posts keep their judgement.
    if awaits_material(a):
        return {"prefilter": None, "scores": None, "writing": None, "structure": None}
    prefilter = run_prefilter(pool, a, opts)
    if prefilter["label"] == "BLOCK":
        return {"prefilter": prefilter, "scores": None, "writing": None, "structure": None}
    original = original_post_copy(a.get("xPost"), a["url"])
    # The structure step needs nothing from the scores: it runs beside them.
    with ThreadPoolExecutor(max_workers=1) as executor:
        structure = executor.submit(run_structure, pool, a, opts)
        try:
            scores = run_selection_scores(pool, a, opts)
            total = score_sum(scores)
            near = scores_select(scores) or (total is not None and total > understand_floor() * SCORE_CALLS)
            structure_value = structure.result()
            # Writing checks the material itself and is never handed the structure's fact: given those extracted conclusions, it rewrote copy that had been right.
            if original:
                writing = {"kind": "verbatim", "model": None, "titleZh": original["title"], "summaryZh": original["summary"] or "", "reasonZh": None, "receiptIds": [], "reused": True}
            else:
                first = run_understand(pool, a, opts) if near else None
                writing = mend_wording(pool, a, first or run_summarize(pool, a, opts), opts)
            return {"prefilter": prefilter, "scores": scores, "writing": writing, "structure": structure_value}
        finally:
            # A score/writing error or deploy must not let the job finish while a paid structure request still owns a response.
            structure.exception()


def normalize_analysis(run: dict) -> dict:
    """One judgement from the steps: the selection rule, the reader-facing copy and the structure."""
    label = (run["prefilter"] or {}).get("label")
    writing = run["writing"]
    title_zh = collapse_whitespace((writing or {}).get("titleZh") or "")
    summary_zh = js_trim((writing or {}).get("summaryZh") or "")
    relevance = "block" if label == "BLOCK" else "unknown" if not writing or not title_zh or (not summary_zh and writing["kind"] != "verbatim") else "pass"
    scores = run["scores"]
    values = scores["values"] if scores and not scores.get("refused") else None
    structure = run["structure"]
    subjects = (structure or {}).get("subjects") or []
    tags = list((structure or {}).get("tags") or [])
    for s in subjects:
        display = (V.entities().get(s) or {}).get("displayTag")
        if display and display not in tags:
            tags.append(display)
    return {
        "relevance": relevance, "selected": relevance == "pass" and scores_select(scores), "score": shown_score(scores), "scores": values,
        "scoreModel": scores["model"] if scores else None, "scoreRefused": bool((scores or {}).get("refused")), "threshold": scores["threshold"] if scores else None,
        "category": (structure or {}).get("category"), "tags": tags, "subjects": subjects, "titleZh": title_zh, "summaryZh": summary_zh,
        "reasonZh": (writing or {}).get("reasonZh"), "scope": (structure or {}).get("scope") or "unknown", "fact": (structure or {}).get("fact"),
    }  # fmt: skip


def analyze_article(pool, article_id: str, opts: dict | None = None) -> dict | None:
    """Analyses the current revision and commits the judgement. A result computed for an older revision is kept for traceability but never overwrites a newer
    input (stale = True)."""
    opts = opts or {}
    inp = load_analyze_input(pool, article_id)
    if not inp:
        return None
    # Its page first; extraction queues the analysis again (normally the queue already routed it there).
    if waits_for_page(inp):
        return {"analysisId": None, "stale": False, "needsBody": True, "output": None, "receiptIds": [], "reused": True}
    run = run_analysis(pool, inp, opts)
    out = normalize_analysis(run)
    receipt_ids = [
        *([run["prefilter"]["receiptId"]] if run["prefilter"] else []), *((run["scores"] or {}).get("receiptIds") or []), *((run["writing"] or {}).get("receiptIds") or []),
        *([run["structure"]["receiptId"]] if run["structure"] else []),
    ]  # fmt: skip
    w = run["writing"]
    detail = {
        "prefilter": run["prefilter"] and {"label": run["prefilter"]["label"], "reason": run["prefilter"]["reason"]},
        "scores": out["scores"], "scoreModel": out["scoreModel"], "threshold": out["threshold"], **({"scoreRefused": True} if out["scoreRefused"] else {}),
        **({"writer": w["kind"], "writerModel": w["model"], "itemType": w.get("itemType"), "authorRole": w.get("authorRole")} if w else {}),
        **({"identityGuard": w["identityGuard"]} if w and (w.get("identityGuard") or {}).get("outcome") == "fallback" else {}),
        "scope": out["scope"], "fact": out["fact"],
    }  # fmt: skip
    with pool.connection() as conn, conn.transaction():
        current = conn.execute("SELECT revision FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone()
        stale = not current or current["revision"] != inp["revision"]
        analysis_id = conn.execute(
            """INSERT INTO analyses (article_id, input_revision, origin, model, prompt_version, receipt_ids, relevance, category, tags, subjects, title_zh, summary_zh, reason_zh, score, selected, output)
               VALUES (%s, %s, 'model', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                article_id, inp["revision"], (w or {}).get("model") or (run["prefilter"] or {}).get("model"), analyze_prompt_version(), receipt_ids, out["relevance"], out["category"],
                out["tags"], out["subjects"], out["titleZh"], out["summaryZh"], out["reasonZh"], out["score"], out["selected"], Jsonb(detail),
            ),
        ).fetchone()["id"]
        for rid in receipt_ids:
            complete_receipt(conn, rid)
        if not stale:
            conn.execute("UPDATE articles SET processing_state = %s, processing_error = NULL WHERE id = %s", ("blocked" if out["relevance"] == "block" else "analyzed", article_id))
    reused = all(x is None or x["reused"] for x in (run["prefilter"], run["scores"], run["writing"], run["structure"]))
    return {"analysisId": analysis_id, "stale": stale, "output": out, "receiptIds": receipt_ids, "reused": reused}
