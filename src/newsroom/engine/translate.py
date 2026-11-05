"""Full-text Chinese translations: made by the worker after an item is selected, for sources whose full text may be shown on the site; a page read never
translates. Bodies are translated block by block (paragraphs, headings, list items, captions, table cells) so the sanitised structure stays as it is. Inside a
block, images and inline code never reach the model (placeholders) and links keep only their text and an id; a block whose answer loses or repeats any of them
is asked once more, closer to the original wording (rewording is what drops links), then kept in the original. Each batch is a receipt, so a re-run reuses
answers already paid for. A translation missing any block is stored as incomplete, never as whole. The post a selected X post quotes is translated too (once
per quoted post, shared by every quote). Port of editorial/translate.ts."""

from __future__ import annotations

import math
import re
import time

import regex

from newsroom.engine import models as M
from newsroom.engine._readability_p1 import js_trim
from newsroom.engine.analyze import _conn
from newsroom.engine.content import text_to_html
from newsroom.engine.fragment import (
    ancestors,
    attribs,
    elements,
    inner_html,
    outer_html,
    parse,
    replace_with_text,
    set_attribs,
    set_inner_html,
    text_of,
)
from newsroom.engine.lib.ids import sha256
from newsroom.engine.lib.text import collapse_whitespace, is_chinese_text
from newsroom.engine.llm import SchemaError, chat_json, js_length
from newsroom.engine.posts import is_empty_or_link_only
from newsroom.engine.presentation import rebase_translation
from newsroom.engine.prompts import prompt_text, prompt_version
from newsroom.engine.receipts import ProviderRejectedError, complete_receipt, failure_kind, reject_received_response
from newsroom.engine.sanitize import sanitize_body
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.sources.json_list import js_str

TRANSLATE_PROMPT_VERSION = prompt_version("translate-body", "translate-body-retry", "translate-post")
BATCH_CHARS = 3500
MAX_CHARS = 60_000  # longer bodies get their first part translated and are marked incomplete
X_MIN_CHARS = 60  # X posts shorter than this carry their meaning in the Chinese title and summary

BLOCK = {"p", "h2", "h3", "h4", "h5", "li", "blockquote", "figcaption", "td", "th", "dt", "dd", "caption"}
CONTAINER = re.compile(r"^(p|h[2-5]|li|blockquote|figcaption|td|th|dt|dd|caption|ul|ol|table|pre|figure|div)\Z")
_LETTER = regex.compile(r"\p{L}")
_TOKEN = re.compile(r"⟦([0-9]+)⟧")
_LINK_ID = re.compile(r"L([0-9]+)\Z")
_URL = re.compile(r"https?://\S+")


def _output(data) -> dict:
    t = data.get("t") if isinstance(data, dict) else None
    if not isinstance(t, list) or not all(isinstance(x, str) for x in t):
        raise SchemaError("t must be a list of strings")
    return {"t": t}


def ends_run(error: BaseException) -> bool:
    """A failure that ends the run without counting against the item: model calls switched off, a full budget, an account refusal, the same request in flight,
    or the provider down for now. The next scheduled run can try again without consuming an item attempt."""
    return failure_kind(error) in ("off", "budget", "busy", "outage") or (isinstance(error, ProviderRejectedError) and error.access_refused)


def _check_running() -> None:
    if SHUTDOWN.is_set():
        raise InterruptedError("worker is stopping")


def segments_of(root) -> list:
    """Leaf text blocks of a sanitised body, in document order, skipping code."""
    out: list = []

    def visit(nodes) -> None:
        for el in nodes:
            if el.nodeType != 1 or el.tagName in ("pre", "code"):
                continue
            has_block_child = any(c.nodeType == 1 and CONTAINER.match(c.tagName) for c in el.childNodes)
            if el.tagName in BLOCK and not has_block_child:
                if _LETTER.search(text_of(el)):
                    out.append(el)
                continue
            visit(list(el.childNodes))

    visit(list(root.childNodes))
    return out


def _translate_batch(pool, article_id: str, revision: int, index: int, parts: list[str], system: str, attempt_tag: str | None = None) -> dict:
    model = M.model_for(_conn(pool), "translate")
    _check_running()
    res = chat_json(pool, {
        "model": model, "purpose": "translate_body", "subject": f"article:{article_id}@{revision}#{index}", "prompt_version": TRANSLATE_PROMPT_VERSION, "system": system,
        "user": {"segments": parts}, "schema": _output, "temperature": 0.2, "max_tokens": min(8000, math.ceil(js_length("".join(parts)) * 1.2) + 400),
        "timeout_ms": 180_000, "attempt_tag": attempt_tag,
    })  # fmt: skip
    t = res["data"]["t"]
    return {"translated": t if len(t) == len(parts) else None, "receiptId": res["receiptId"]}


def _translate_all(pool, article_id: str, revision: int, parts: list[str], system: str, attempt_tag: str | None = None) -> tuple[list, list[int]]:
    """Translates batches, retaining every paid receipt used before the final body is committed."""
    out: list = [None] * len(parts)
    receipt_ids: list[int] = []
    start = index = 0
    while start < len(parts):
        end = start
        chars = 0
        while end < len(parts) and (end == start or chars + js_length(parts[end]) <= BATCH_CHARS):
            chars += js_length(parts[end])
            end += 1
        batch = parts[start:end]
        first = _translate_batch(pool, article_id, revision, index, batch, system, attempt_tag)
        index += 1
        receipt_ids.append(first["receiptId"])
        done = first["translated"]
        if not done and len(batch) > 1:
            mid = math.ceil(len(batch) / 2)
            left = _translate_batch(pool, article_id, revision, index, batch[:mid], system, attempt_tag)
            right = _translate_batch(pool, article_id, revision, index + 1, batch[mid:], system, attempt_tag)
            index += 2
            receipt_ids += [left["receiptId"], right["receiptId"]]
            done = [*left["translated"], *right["translated"]] if left["translated"] and right["translated"] else None
        if done:
            for i, t in enumerate(done):
                out[start + i] = t
        start = end
    return out, receipt_ids


def shield(inner: str) -> dict:
    """A block as the model sees it: media and inline code as ⟦n⟧, links as <a id="Ln"> with their attributes kept here."""
    root = parse(inner)
    tokens: list[str] = []
    for node in [e for e in elements(root) if e.tagName in ("picture", "video", "img", "code")]:
        if any(a.tagName in ("picture", "video", "code") for a in ancestors(node)):
            continue
        tokens.append(outer_html(node))
        replace_with_text(node, f"⟦{len(tokens) - 1}⟧")
    links: list[list[tuple[str, str]]] = []
    for i, a in enumerate(e for e in elements(root) if e.tagName == "a"):
        links.append(attribs(a))
        set_attribs(a, [("id", f"L{i}")])
    return {"html": inner_html(root), "tokens": tokens, "links": links}


def unshield(translated: str, s: dict) -> str | None:
    """The translated block with its media, code and links put back; None when the answer lost or repeated any."""
    counts: dict[int, int] = {}
    for m in _TOKEN.finditer(translated):
        counts[int(m.group(1))] = counts.get(int(m.group(1)), 0) + 1
    if len(counts) != len(s["tokens"]) or any(counts.get(i) != 1 for i in range(len(s["tokens"]))):
        return None
    root = parse(translated)
    seen: set[int] = set()
    intact = True
    for a in [e for e in elements(root) if e.tagName == "a"]:
        m = _LINK_ID.match(a.getAttribute("id")) if a.hasAttribute("id") else None
        n = int(m.group(1)) if m else None
        if n is None or n >= len(s["links"]) or n in seen:
            intact = False
        else:
            seen.add(n)
            set_attribs(a, s["links"][n])
    if not intact or len(seen) != len(s["links"]):
        return None
    return _TOKEN.sub(lambda m: s["tokens"][int(m.group(1))], inner_html(root))


def translate_article(pool, article_id: str) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT a.revision, p.channel, a.language, a.body_html, a.body_text, a.x_post, a.x_article, p.title, p.selected, p.body_mode, p.visibility
               FROM publications p JOIN articles a ON a.id = p.article_id WHERE p.article_id = %s""",
            (article_id,),
        ).fetchone()
    if not row:
        return {"articleId": article_id, "status": "skipped", "reason": "not published"}

    def result(**r) -> dict:
        return {"articleId": article_id, "revision": row["revision"], **r}

    def unusable(receipt_ids: list[int], reason: str) -> dict:
        # No answer could be used: each is rejected, so the next attempt pays for fresh ones instead of reusing them.
        for rid in dict.fromkeys(receipt_ids):
            reject_received_response(pool, rid, f"unusable translation: {reason}")
        return result(status="failed", reason=reason)

    if not row["selected"] or row["visibility"] != "public" or row["body_mode"] != "full":
        return result(status="skipped", reason="not a selected full-text item")

    if row["channel"] == "x":
        # Pages keep the complete long article in its original language; a post-only translation is unused.
        if (row["x_article"] or {}).get("text"):
            return result(status="skipped", reason="saved long article shown in original")
        raw = (row["x_post"] or {}).get("text")
        text = js_trim(js_str(raw if raw is not None else (row["body_text"] if row["body_text"] is not None else "")))
        if is_empty_or_link_only(text):
            return result(status="skipped", reason="no text to translate")
        meaningful = collapse_whitespace(_URL.sub("", text))
        if is_chinese_text(text, row["language"]):
            return result(status="skipped", reason="already Chinese")
        if js_length(meaningful) < X_MIN_CHARS:
            return result(status="skipped", reason="short post")
        translations, receipt_ids = _translate_all(pool, article_id, row["revision"], [text], prompt_text("translate-post"))
        if not translations[0]:
            return unusable(receipt_ids, "translation did not line up")
        _store(pool, article_id, row["revision"], row["title"], text_to_html(translations[0]), translations[0], True, receipt_ids)
        return result(status="translated", segments=1)

    if not row["body_html"] or is_chinese_text(row["body_text"] or "", row["language"]):
        return result(status="skipped", reason="no foreign-language body")
    root = parse(row["body_html"])
    blocks = segments_of(root)
    if not blocks:
        return result(status="skipped", reason="no translatable text")
    # Beyond the cap, only the leading blocks are translated; the rest keep the original text.
    budget = MAX_CHARS
    chosen: list = []
    for el in blocks:
        n = js_length(inner_html(el))
        if n > budget:
            break
        budget -= n
        chosen.append(el)
    if not chosen:  # no model request was made when even the first block exceeds the text cap
        return result(status="skipped", reason="no batch translated")
    shielded = [shield(inner_html(el)) for el in chosen]

    def restore(answers: list) -> list:
        return [None if t is None else unshield(t, shielded[i]) for i, t in enumerate(answers)]

    first, receipt_ids = _translate_all(pool, article_id, row["revision"], [b["html"] for b in shielded], prompt_text("translate-body"))
    translations = restore(first)
    # Blocks whose answer dropped a link or an image are asked once more, on their own receipt, with the retry prompt.
    missing = [i for i, t in enumerate(translations) if t is None]
    if missing:
        again, again_ids = _translate_all(
            pool, article_id, row["revision"], [shielded[i]["html"] for i in missing], prompt_text("translate-body-retry"), "retry"
        )
        receipt_ids += again_ids
        for k, i in enumerate(missing):
            translations[i] = unshield(again[k], shielded[i]) if again[k] else None
    done = 0
    for i, el in enumerate(chosen):
        if translations[i]:
            set_inner_html(el, translations[i])
            done += 1
    if not done:
        return unusable(receipt_ids, "no batch translated")
    complete = done == len(blocks)
    html = sanitize_body(inner_html(root))
    _store(pool, article_id, row["revision"], row["title"], html, js_trim(text_of(parse(html))), complete, receipt_ids, row["body_html"])
    return result(status="translated" if complete else "partial", segments=done)


def _store(
    pool,
    article_id: str,
    revision: int,
    title: str,
    html: str,
    text: str,
    complete: bool,
    receipt_ids: list[int],
    source_html: str | None = None,
    exact_source: bool = False,
) -> None:
    # Never over a translation of a later revision (a slow run finishing after a newer one).
    with pool.connection() as conn, conn.transaction():
        # A layout refresh can finish while the model is answering the same semantic revision. Serialize with material writes and attach the answer to the
        # current presentation.
        current = conn.execute("SELECT revision, body_html FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone()
        if exact_source and (not current or current["revision"] != revision or current["body_html"] != source_html):
            raise RuntimeError("Translation source changed; import rejected")
        if source_html is not None and current and current["revision"] == revision and current["body_html"] != source_html:
            html = rebase_translation(source_html, current["body_html"] or "", html)
        # emit("articleChanged") has no listener here: the engine runs without site modules.
        conn.execute(
            """INSERT INTO translations (article_id, lang, revision, title, body_html, body_text, complete, origin)
               VALUES (%s, 'zh', %s, %s, %s, %s, %s, 'model')
               ON CONFLICT (article_id, lang) DO UPDATE SET revision = EXCLUDED.revision, title = EXCLUDED.title, body_html = EXCLUDED.body_html,
                 body_text = EXCLUDED.body_text, complete = EXCLUDED.complete, origin = 'model', created_at = now()
               WHERE translations.origin <> 'source' AND translations.revision <= EXCLUDED.revision
                 AND (translations.revision, translations.title, translations.body_html, translations.body_text, translations.complete, translations.origin)
                   IS DISTINCT FROM (EXCLUDED.revision, EXCLUDED.title, EXCLUDED.body_html, EXCLUDED.body_text, EXCLUDED.complete, EXCLUDED.origin)""",
            (article_id, revision, title, html, text, complete),
        )
        for rid in dict.fromkeys(receipt_ids):
            complete_receipt(conn, rid)


def import_body_translation(pool, article_id: str, source_html: str, title: str, html: str, complete: bool) -> None:
    """Restore an externally generated model translation only against its exact source body. Importers keep model credentials and execution outside reader
    requests; publisher translations retain priority."""
    with pool.connection() as conn:
        row = conn.execute("SELECT revision FROM articles WHERE id = %s", (article_id,)).fetchone()
    if not row:
        raise RuntimeError("Translation article missing")
    clean = sanitize_body(html)
    text = js_trim(text_of(parse(clean)))
    if not clean or not text:
        raise RuntimeError("Empty translation")
    _store(pool, article_id, row["revision"], title, clean, text, complete, [], source_html, True)


def _quote_translatable(text: str) -> bool:
    """A quoted post worth translating: at least a few letters beyond its links, and not already Chinese."""
    words = collapse_whitespace(_URL.sub(" ", text))
    return js_length(words) >= 10 and len(_LETTER.findall(words)) >= 2 and not is_chinese_text(words)


def translate_quotes(pool, days: int = 3, limit: int = 30, budget_ms: float = 2 * 60_000) -> int:
    """The posts that selected X posts of the last `days` quote, translated once per quoted post: the translation of the quoted post's own item is reused when
    it was collected and translated, else the translate model translates it. Returns how many were stored."""
    started = time.monotonic()
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (q.tweet_id) q.tweet_id, a.x_post->'quoted'->>'text' AS text, qt.text_hash,
                 (SELECT tr.body_text FROM articles o JOIN translations tr ON tr.article_id = o.id AND tr.lang = 'zh' AND tr.revision >= o.revision
                  WHERE o.identity_key = 'x:' || q.tweet_id AND tr.complete AND coalesce(tr.body_text, '') <> '') AS own_zh
               FROM publications p JOIN articles a ON a.id = p.article_id
               CROSS JOIN LATERAL (SELECT substring(a.x_post->'quoted'->>'url' from '/status/([0-9]+)') AS tweet_id) q
               LEFT JOIN quote_translations qt ON qt.tweet_id = q.tweet_id
               WHERE p.channel = 'x' AND p.selected AND p.visibility = 'public' AND p.body_mode = 'full'
                 AND p.discovered_at > now() - make_interval(days => %s::int)
                 AND q.tweet_id IS NOT NULL AND coalesce(a.x_post->'quoted'->>'text', '') <> ''
               ORDER BY q.tweet_id, p.discovered_at DESC""",
            (days,),
        ).fetchall()
    stored = 0
    for r in rows:
        if stored >= limit or (time.monotonic() - started) * 1000 > budget_ms or SHUTDOWN.is_set():
            break
        digest = sha256(r["text"])
        if r["text_hash"] == digest or not _quote_translatable(r["text"]):
            continue
        zh, origin, receipt_id = r["own_zh"], "reused", None
        if not zh:
            origin = "model"
            try:
                res = chat_json(pool, {
                    "model": M.model_for(_conn(pool), "translate"), "purpose": "translate_quoted", "subject": f"quote:{r['tweet_id']}", "prompt_version": TRANSLATE_PROMPT_VERSION,
                    "system": prompt_text("translate-post"), "user": {"segments": [r["text"]]}, "schema": _output, "temperature": 0.2,
                    "max_tokens": min(4000, math.ceil(js_length(r["text"]) * 1.5) + 200), "timeout_ms": 120_000,
                })  # fmt: skip
                receipt_id = res["receiptId"]
                zh = js_trim(res["data"]["t"][0]) if len(res["data"]["t"]) == 1 else None
            except Exception as error:
                # What ends the run ends it; any other failure skips its post (an answer received is reused next time, so a retry costs nothing).
                if ends_run(error):
                    raise
                continue
        if not zh:
            continue
        with pool.connection() as conn, conn.transaction():
            saved = conn.execute(
                """INSERT INTO quote_translations (tweet_id, text_hash, text_zh, origin) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tweet_id) DO UPDATE SET text_hash = EXCLUDED.text_hash, text_zh = EXCLUDED.text_zh, origin = EXCLUDED.origin, created_at = now()
                   WHERE (quote_translations.text_hash, quote_translations.text_zh, quote_translations.origin)
                     IS DISTINCT FROM (EXCLUDED.text_hash, EXCLUDED.text_zh, EXCLUDED.origin)
                   RETURNING tweet_id""",
                (r["tweet_id"], digest, zh, origin),
            ).fetchall()
            if receipt_id is not None:
                complete_receipt(conn, receipt_id)
        if saved:
            stored += 1
    return stored


def translate_pending(pool, limit: int = 30, budget_ms: float = 4 * 60_000) -> dict:
    """Every few minutes: selected full-text items discovered or revised in the last three days that lack a translation of their current revision, then the
    posts selected X posts quote. Stops after a time budget. A translation of an older revision is not shown, so a revised item is translated again whatever
    its age. Three failed attempts give an item up; only failures of the item itself count."""
    started = time.monotonic()

    def spent() -> float:
        return (time.monotonic() - started) * 1000

    with pool.connection() as conn:
        pending = conn.execute(
            """SELECT p.article_id, a.language,
                 CASE WHEN p.channel = 'x' THEN coalesce(a.x_post->>'text', a.body_text) ELSE a.body_text END AS text
               FROM publications p JOIN articles a ON a.id = p.article_id
               LEFT JOIN translations tr ON tr.article_id = p.article_id AND tr.lang = 'zh'
               WHERE p.selected AND p.visibility = 'public' AND p.body_mode = 'full'
                 AND (p.channel = 'x' OR a.body_html IS NOT NULL)
                 AND (p.channel <> 'x' OR coalesce(a.x_article->>'text', '') = '')
                 AND (p.discovered_at > now() - interval '3 days'
                      OR EXISTS (SELECT 1 FROM article_revisions r WHERE r.article_id = a.id AND r.revision = a.revision AND r.revision > 1
                                 AND r.created_at > now() - interval '3 days'))
                 AND (tr.article_id IS NULL OR (tr.origin <> 'source' AND tr.revision < a.revision))
                 AND NOT EXISTS (SELECT 1 FROM translation_attempts t WHERE t.article_id = p.article_id AND t.revision = a.revision
                                 AND (t.outcome IN ('translated', 'partial') OR t.attempts >= 3
                                   OR (t.outcome = 'skipped' AND t.reason NOT IN ('already Chinese', 'no foreign-language body'))))
               ORDER BY p.discovered_at DESC"""
        ).fetchall()
    # Reconsider language skips with the shared rule. Filter before taking the batch so Chinese originals do not occupy every slot and starve foreign-language
    # bodies behind them.
    rows = [r for r in pending if not is_chinese_text(r["text"] or "", r["language"])][:limit]
    done: list[dict] = []
    for r in rows:
        if spent() > budget_ms or SHUTDOWN.is_set():
            break
        reason = None
        # The attempt is recorded against the revision actually read: when the text was revised while the model was answering, the new revision still has no
        # attempt and is translated on the next run.
        revision = None
        try:
            res = translate_article(pool, r["article_id"])
            done.append(res)
            outcome, reason, revision = res["status"], res.get("reason"), res.get("revision")
        except Exception as error:
            # A deploy stops between paid fragments, never aborts a sent request. Received answers stay in receipts and are reused next run; do not mark an
            # interrupted article terminal/partial. Any error once the stop began is that interruption.
            if SHUTDOWN.is_set() or ends_run(error):
                return {"done": done, "quotes": 0}
            # A request that may have been billed waits for its release (operations/recover), which lets the item's next attempt call again; meanwhile the
            # other items go on.
            if failure_kind(error) == "unknown":
                continue
            message = str(error)
            done.append({"articleId": r["article_id"], "status": "skipped", "reason": message[:200]})
            outcome, reason = "failed", message[:300]
        with pool.connection() as conn:
            conn.execute(
                """INSERT INTO translation_attempts (article_id, revision, attempts, outcome, reason)
                   SELECT %s, coalesce(%s::int, a.revision), 1, %s, %s FROM articles a WHERE a.id = %s
                   ON CONFLICT (article_id) DO UPDATE SET
                     attempts = CASE WHEN translation_attempts.revision = EXCLUDED.revision THEN translation_attempts.attempts + 1 ELSE 1 END,
                     revision = EXCLUDED.revision, outcome = EXCLUDED.outcome, reason = EXCLUDED.reason, updated_at = now()""",
                (r["article_id"], revision, outcome, reason, r["article_id"]),
            )
    quotes = 0
    left = budget_ms - spent()
    if left > 0 and not SHUTDOWN.is_set():
        try:
            quotes = translate_quotes(pool, budget_ms=left)
        except Exception as error:
            if ends_run(error):
                return {"done": done, "quotes": quotes}
            raise
    return {"done": done, "quotes": quotes}
