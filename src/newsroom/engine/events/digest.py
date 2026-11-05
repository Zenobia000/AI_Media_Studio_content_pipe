"""Story digest: rewritten incrementally as reports arrive; contradictions with earlier reporting are stated explicitly. Latest progress is bound to a current
public report, not generated independently. Port of events/digest.ts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from newsroom.engine import models as M
from newsroom.engine.analyze import _conn
from newsroom.engine.llm import SchemaError, chat_json, js_length
from newsroom.engine.prompts import prompt_text, prompt_version
from newsroom.engine.publication.stories import digest_fact_evidence, digest_inputs_hash, digest_reports
from newsroom.engine.receipts import complete_receipt
from newsroom.engine.time import beijing_date, beijing_time

DIGEST_SAMPLING = {"temperature": 0.3, "max_tokens": 1200}  # sampling of every digest request


def digest_prompt_version() -> str:
    return prompt_version("story-digest")


def digest_system() -> str:
    return prompt_text("story-digest")


def digest_schema(data) -> dict:
    if not isinstance(data, dict):
        raise SchemaError("expected an object")
    title = data.get("title")
    title = title if isinstance(title, str) and js_length(title) <= 120 else ""
    digest = data.get("digest")
    if not isinstance(digest, str) or not 10 <= js_length(digest) <= 2000:
        raise SchemaError("digest must be 10 to 2000 characters")
    return {"title": title, "digest": digest}


def build_story_digest_input(story: dict, reports: list[dict], corrected: bool, known_article_ids: list[str] | None = None) -> str:
    """The digest request's input, from one story's reports in report-time order."""
    known = set(known_article_ids or [])
    lines = []
    for r in reports[-40:]:
        mark = "" if corrected or r["id"] in known else "【新】"
        first_party = "（一手）" if r["first_party"] else ""
        evidence = json.dumps(digest_fact_evidence(r), ensure_ascii=False, separators=(",", ":"))
        lines.append(
            f"{mark}報道 {r['id']}｜事實 {r['fact_id']}｜{beijing_date(r['at'])} {beijing_time(r['at'])}｜{r['source_name']}{first_party}｜{r['title']}｜{r['summary'] or ''}\n"
            f"事實條件與來源證據：{evidence}"
        )
    body = "\n".join(lines)
    if corrected:
        return f"事件當前標題：{story['title']}\n\n報道內容或事實證據經過更正。請只依據下面這些報道的當前內容重寫綜述，不要沿用以前版本的說法。\n報道（按時間）：\n{body}"
    previous = f"上一版綜述：{story['digest']}\n" if story["digest"] else ""
    return f"事件當前標題：{story['title']}\n{previous}\n報道（按時間，標【新】的是上一版之後的新報道）：\n{body}"


def compose_story_digest(pool, story_id: int, rewrite: bool = False) -> dict:
    """A story's digest from its current reports. `rewrite` writes it again from the reports as they are now with the current prompt, as after a correction:
    a new wording otherwise reaches a story only when its reports change."""
    now = datetime.now(UTC)
    with pool.connection() as conn:
        story = conn.execute("SELECT id, title, digest, version, origin FROM stories WHERE id = %s AND merged_into IS NULL", (story_id,)).fetchone()
        if not story:
            return {"updated": False}
        reports = digest_reports(conn, [story_id], now)
        if not reports:
            cleared = conn.execute(
                """UPDATE stories SET digest = NULL, latest = NULL, digest_updated_at = NULL, updated_at = now()
                   WHERE id = %s AND version = %s AND merged_into IS NULL AND (digest IS NOT NULL OR latest IS NOT NULL)""",
                (story_id, story["version"]),
            )
            return {"updated": cleared.rowcount > 0}
        reports.sort(key=lambda r: r["at"])
        ids = sorted(r["id"] for r in reports)
        # What this version is written from: the reports and what they currently say (corrections included).
        inputs_hash = digest_inputs_hash(reports)
        last = conn.execute(
            "SELECT article_ids, inputs_hash, digest, receipt_id FROM story_digests WHERE story_id = %s ORDER BY version DESC LIMIT 1", (story_id,)
        ).fetchone()
    same_reports = last is not None and sorted(last["article_ids"]) == ids
    if not rewrite and story["digest"] is not None and same_reports and last["inputs_hash"] == inputs_hash:
        return {"updated": False}
    # Same reports, different content: an editor corrected one. A report gone from the story (withdrawn, or regrouped elsewhere) likewise. Rewrite from the
    # reports as they are now, without the previous digest, so a corrected or withdrawn fact does not survive as "earlier reports said".
    dropped = last is not None and any(i not in ids for i in last["article_ids"])
    corrected = rewrite or same_reports or dropped
    user = build_story_digest_input(story, reports, corrected, last["article_ids"] if last else None)
    latest = reports[-1]["title"]
    # A withdrawal clears the public projection but retains its history. Restoring exactly the same evidence reuses that digest through the normal version/input
    # checks, without another model call.
    if not rewrite and last is not None and last["inputs_hash"] == inputs_hash:
        res = {"data": {"title": "", "digest": last["digest"]}, "receiptId": last["receipt_id"]}
    else:
        res = chat_json(pool, {
            "model": M.model_for(_conn(pool), "digest"), "purpose": "story_digest", "subject": f"story:{story_id}@{len(ids)}", "prompt_version": digest_prompt_version(),
            "system": digest_system(), "user": user, "schema": digest_schema, **DIGEST_SAMPLING,
        })  # fmt: skip
    version = story["version"] + 1
    with pool.connection() as conn, conn.transaction():
        current = conn.execute("SELECT version, merged_into FROM stories WHERE id = %s FOR UPDATE", (story_id,)).fetchone()
        # Report corrections, withdrawals and membership changes do not necessarily bump the story version. Compare the same input identity again after the
        # model returns.
        if (
            not current
            or current["version"] != story["version"]
            or current["merged_into"] is not None
            or digest_inputs_hash(digest_reports(conn, [story_id], now)) != inputs_hash
        ):
            # A paid response remains reusable even when an editor or another digest won the race.
            if res["receiptId"] is not None:
                complete_receipt(conn, res["receiptId"])
            return {"updated": False}
        conn.execute(
            "INSERT INTO story_digests (story_id, version, digest, latest, receipt_id, article_ids, inputs_hash) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (story_id, version, res["data"]["digest"], latest, res["receiptId"], ids, inputs_hash),
        )
        conn.execute(
            """UPDATE stories SET digest = %s, latest = %s, digest_updated_at = now(),
                 title = CASE WHEN origin = 'manual' OR %s = '' THEN title ELSE %s END, version = %s, updated_at = now() WHERE id = %s""",
            (res["data"]["digest"], latest, res["data"]["title"], res["data"]["title"], version, story_id),
        )
        if res["receiptId"] is not None:
            complete_receipt(conn, res["receiptId"])
    return {"updated": True, "version": version}
