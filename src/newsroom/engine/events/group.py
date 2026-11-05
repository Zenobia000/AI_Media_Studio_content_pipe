"""Event grouping. A report attaches to a fact, the same real-world occurrence, and facts hang on a story, an occurrence with its direct developments. Recall
(recall.py): the same title-and-summary embedding on both sides over the reports of the last 14 days (shared bigrams without an embedding key), plus the same URL and
the X post a post replies to or quotes. Identity: one four-way relation judgement over the candidate facts with their representative reports fully described
(relate.py); a merge that is not obvious from similarity is confirmed by a second vendor before it is written; a development attaches only to the fact that started
its story, so stories do not grow by chaining. Manual corrections (corrections.py) are never overwritten; a revision keeps its membership unless an editor asks for
a regroup or its structure identifies it as composite. Composite material only mentions facts, never starts or joins a fact/story and never supplies a root or bridge.
When a report is firmly tied to two stories, their roots are compared directly and the stories merge when both models see one story (consolidate.py); stories that stay
apart though reports keep tying them list each other as related. A story a regrouped report leaves without reports merges into where it went, so its address keeps
working. A discussion post is recalled by reports and by the posts already attached to a fact; one that found no story gets another look, while it still counts for
heat, whenever a fact close to it gains evidence (rematch_signals). History (is_historical) founds no event. Runs serially (queue concurrency 1). Port of events/group.ts."""

from __future__ import annotations

import math
import re

from psycopg.types.json import Jsonb

from newsroom.engine import models as M
from newsroom.engine.analyze import _conn
from newsroom.engine.embeddings import embeddings_available
from newsroom.engine.events.consolidate import consolidate, live_story
from newsroom.engine.events.hot import HEAT_WINDOW_HOURS
from newsroom.engine.events.merge import merge_story_into
from newsroom.engine.events.recall import (
    best_per_fact,
    candidate_views,
    cosine32,
    recall_facts,
    recall_facts_by_posts,
    recall_selected_background,
    related_posts,
    vectors_for,
)  # fmt: skip
from newsroom.engine.events.relate import (
    TIE_MIN_CONFIDENCE,
    batch_prompt_version,
    batch_schema,
    batch_system,
    batch_user,
    firmly_tied,
    pair_schema,
    pair_system,
    pair_user,
    relate_prompt_version,
    report_text,
    same_occurrence,
    signal_schema,
    signal_system,
    signal_target,
    signal_text,
    story_for_development,
    verdicts_by_fact,
)  # fmt: skip
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.ids import new_short_id, new_uuid
from newsroom.engine.lib.text import js_slice
from newsroom.engine.llm import chat_json
from newsroom.engine.materials import NEWS_CONDITION, is_historical
from newsroom.engine.publication.publish import publish_article, publish_article_tx
from newsroom.engine.publication.scope import latest_composite_condition
from newsroom.engine.receipts import complete_receipt, failed_receipt_id, failure_kind
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.sources.collect import grouping_priority
from newsroom.engine.sources.json_list import js_str
from newsroom.engine.time import beijing_date, beijing_midnight

RECALL_MIN_COSINE = 0.6
RECALL_TOP_FACTS = 10
CONFIRM_BELOW_COSINE = 0.85  # a merge with a candidate less similar than this is confirmed by the review model before it is written
SIGNAL_MIN_COSINE = 0.72  # discussion posts are judged only against clear candidates, and attach without a call when nearly identical to a report
SIGNAL_AUTO_COSINE = 0.92
SIGNAL_PEER_MIN_COSINE = 0.78  # a post this close to a post already attached to a fact is judged against that fact (never attached without the judge)
SIGNAL_TOP_FACTS = 4
WAIT_HOURS = 48  # how long a discussion post waits for the post it replies to or quotes
NO_SELECTED_COVERAGE = {"addsValue": True, "reason": "沒有已公開精選的相關報道"}
_STATUS = re.compile(r"/status/([0-9]+)")


class GroupingSupersededError(Exception):
    pass


def participant_key(source: dict) -> str:
    """The participant as recorded when the signal was written. Heat reads the participant from the current source, so a later change of group or owner reaches it
    at once."""
    return f"group:{source['signal_group_id']}" if source.get("signal_group_id") else f"source:{source['id']}"


def _round3(x: float) -> float:
    return math.floor(x * 1000 + 0.5) / 1000


def _verdict_fields(v) -> dict:
    return {"relation": v.relation, "confidence": v.confidence, "note": v.note} if v else {}


# ── judgement ───────────────────────────────────────────────────────────────────


def _route(pool, capability: str) -> dict:
    route = M.model_route(_conn(pool), capability)
    return {"model": route["model"], "fallback_model": route["fallbackModel"]}


def judge_batch(pool, article_id: str, query: dict, cands: list, reading: list) -> dict:
    res = chat_json(pool, {
        **_route(pool, "group"), "purpose": "group_article", "subject": f"article:{article_id}", "prompt_version": batch_prompt_version(), "system": batch_system(),
        "user": batch_user(query, cands, "新報道", reading), "schema": batch_schema(len(cands)), "temperature": 0, "max_tokens": 400 + 120 * len(cands),
    })  # fmt: skip
    selection = res["data"]["selection"] if any(c.selected for c in cands) or reading else NO_SELECTED_COVERAGE
    return {"verdicts": verdicts_by_fact(res["data"]["decisions"], cands), "selection": selection, "receiptId": res["receiptId"]}


def confirm_merge(pool, article_id: str, query: dict, cand) -> dict:
    """The review model reads both reports on their own; a merge stands only when it agrees."""
    res = chat_json(pool, {
        "model": M.model_for(_conn(pool), "groupReview"), "purpose": "group_review", "subject": f"article:{article_id}:fact:{cand.fact_id}", "prompt_version": relate_prompt_version(),
        "system": pair_system(), "user": pair_user(query, cand.report), "schema": pair_schema, "temperature": 0, "max_tokens": 400,
    })  # fmt: skip
    return {"relation": res["data"]["relation"], "receiptId": res["receiptId"]}


def judge_signal(pool, article_id: str, query: dict, cands: list) -> dict:
    res = chat_json(pool, {
        **_route(pool, "group"), "purpose": "group_signal", "subject": f"article:{article_id}", "prompt_version": relate_prompt_version(), "system": signal_system(),
        "user": batch_user(query, cands, "帖子"), "schema": signal_schema(len(cands)), "temperature": 0, "max_tokens": 150 + 60 * len(cands),
    })  # fmt: skip
    return {"verdicts": verdicts_by_fact(res["data"]["decisions"], cands), "receiptId": res["receiptId"]}


# ── writes ──────────────────────────────────────────────────────────────────────


def create_story(conn, title: str, at) -> int:
    return conn.execute(
        "INSERT INTO stories (public_id, title, first_report_at, latest_at, origin) VALUES (%s, %s, %s, %s, 'model') RETURNING id",
        (new_uuid(), title, at, at),
    ).fetchone()["id"]


def create_fact(conn, story_id: int, title: str, frame: dict | None, at) -> int:
    frame = frame or {}
    occurred = None
    day = frame.get("occurredAt")
    if isinstance(day, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        try:
            candidate = beijing_midnight(day)
            occurred = candidate if beijing_date(candidate) == day else None
        except ValueError:
            occurred = None
    raw = frame.get("conditions")
    conditions = ("；".join(c.get("text") for c in raw if isinstance(c, dict) and c.get("text")) or None) if isinstance(raw, list) else None
    return conn.execute(
        """INSERT INTO facts (public_id, story_id, title, subject, action, object, conditions, occurred_at, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (f"f{new_short_id(8)}", story_id, title, frame.get("subject"), frame.get("action"), frame.get("object"), conditions, occurred, at),
    ).fetchone()["id"]


def record_signal(conn, story_id: int, article_id: str, source: dict, kind: str, observed_at) -> None:
    # All callers write in a transaction: lock the story before inserting evidence, so a concurrent merge either carries this row with it or makes this decision retry
    # against the surviving story.
    updated = conn.execute(
        """UPDATE stories SET latest_at = GREATEST(coalesce(latest_at, %(at)s), %(at)s), first_report_at = LEAST(coalesce(first_report_at, %(at)s), %(at)s), updated_at = now()
           WHERE id = %(story)s AND merged_into IS NULL""",
        {"at": observed_at, "story": story_id},
    )
    if not updated.rowcount:
        raise RuntimeError("Grouping target changed; retry against the current story")
    conn.execute(
        """INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (story_id, article_id) DO NOTHING""",
        (story_id, article_id, participant_key(source), source["id"], kind, observed_at),
    )


def record_decision(conn, article_id: str, fact_id, story_id, verdict: str, candidates: list, receipt_id) -> None:
    conn.execute(
        "INSERT INTO grouping_decisions (article_id, fact_id, story_id, verdict, candidates, receipt_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (article_id, fact_id, story_id, verdict, Jsonb(candidates), receipt_id),
    )


def manual_decision(conn, article_id: str) -> dict | None:
    """A manual membership, or "keep standalone": either wins over any model decision."""
    manual = conn.execute("SELECT fact_id FROM fact_articles WHERE article_id = %s AND manual LIMIT 1", (article_id,)).fetchone()
    if manual:
        return {"factId": manual["fact_id"]}
    standalone = conn.execute("SELECT 1 FROM grouping_overrides WHERE article_id = %s", (article_id,)).fetchone()
    return {"factId": None} if standalone else None


def current_membership(pool, article_id: str) -> dict | None:
    """The automatic membership a report already has (a revision keeps it)."""
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT fa.fact_id, f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id JOIN stories st ON st.id = f.story_id
               WHERE fa.article_id = %s AND fa.role IN ('primary', 'report') AND st.merged_into IS NULL
               ORDER BY (fa.role = 'primary') DESC, fa.created_at LIMIT 1""",
            (article_id,),
        ).fetchone()
    return {"factId": int(row["fact_id"]), "storyId": int(row["story_id"])} if row else None


def reset_automatic(conn, article_id: str) -> list[int]:
    """A clean slate (an editor's regroup, a composite, material too thin for an identity): automatic memberships and heat evidence go, manual ones stay. Returns the
    stories the report was a report of. The caller holds the article's row lock."""
    if manual_decision(conn, article_id):
        return []
    left = conn.execute(
        """SELECT DISTINCT f.story_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id
           WHERE fa.article_id = %s AND NOT fa.manual AND fa.role IN ('primary', 'report') AND f.story_id IS NOT NULL""",
        (article_id,),
    ).fetchall()
    conn.execute("DELETE FROM fact_articles WHERE article_id = %s AND NOT manual", (article_id,))
    conn.execute("DELETE FROM story_signals WHERE article_id = %s", (article_id,))
    return [int(r["story_id"]) for r in left]


def redirect_emptied_stories(pool, article_id: str, story_id: int) -> list[int]:
    """Stories the report sat in before (its earlier decisions) that hold no report now keep their address: each merges into the report's story, so its public id
    redirects there."""
    with pool.connection() as conn:
        emptied = conn.execute(
            """SELECT st.id FROM stories st
               WHERE st.id IN (SELECT d.story_id FROM grouping_decisions d WHERE d.article_id = %s)
                 AND st.id <> %s AND st.merged_into IS NULL
                 AND NOT EXISTS (SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id WHERE f.story_id = st.id AND fa.role IN ('primary', 'report'))""",
            (article_id, story_id),
        ).fetchall()
    redirected = []
    for r in emptied:
        if merge_story_into(pool, int(r["id"]), story_id, f"報道已全部移走，舊地址跳到報道所在事件（最後一篇 {article_id}）", "grouping"):
            redirected.append(int(r["id"]))
    return redirected


def lock_current_revision(conn, article_id: str, revision: int) -> None:
    current = conn.execute("SELECT revision FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone()
    if not current or current["revision"] != revision:
        raise GroupingSupersededError("Grouping input changed; the replacement revision has its own analysis")


def mark_grouped(conn, article_id: str, revision: int, selection: dict | None = None) -> None:
    done = conn.execute(
        """UPDATE articles SET grouped_at = coalesce(grouped_at, now()), grouping_status = 'complete', grouping_receipt_id = NULL, grouping_error = NULL,
             selection_adds_value = CASE WHEN %(has)s THEN %(adds)s ELSE selection_adds_value END,
             selection_value_reason = CASE WHEN %(has)s THEN %(reason)s ELSE selection_value_reason END
           WHERE id = %(id)s AND revision = %(revision)s""",
        {
            "has": bool(selection),
            "adds": selection["addsValue"] if selection else None,
            "reason": selection["reason"] if selection else None,
            "id": article_id,
            "revision": revision,
        },
    )
    if not done.rowcount:
        raise GroupingSupersededError("Grouping input changed; the replacement revision has its own analysis")


def _mark(pool, article_id: str, revision: int, selection: dict | None = None) -> None:
    with pool.connection() as conn:
        mark_grouped(conn, article_id, revision, selection)


def _complete_all(conn, receipts: list[int]) -> None:
    for rid in receipts:
        complete_receipt(conn, rid)


# ── entry ───────────────────────────────────────────────────────────────────────


def group_article(pool, article_id: str, signal_only: bool = False, regroup: bool = False) -> dict:
    with pool.connection() as conn, conn.transaction():
        current = conn.execute(
            "SELECT revision, grouping_status, selection_adds_value FROM articles WHERE id = %s FOR UPDATE", (article_id,)
        ).fetchone()
        if current:
            conn.execute(
                "UPDATE articles SET grouping_status = 'pending', grouped_at = NULL, grouping_error = NULL, grouping_receipt_id = NULL WHERE id = %s",
                (article_id,),
            )
            # An unfinished decision (new material, a failure, an explicit regroup) must withdraw its old public seat in this transaction. Ordinary repeats of a
            # completed identity reuse it immediately without a transient withdrawal.
            if current["grouping_status"] != "complete":
                publish_article_tx(conn, article_id)
    if not current:
        return {"verdict": "skipped"}
    revision = current["revision"]
    try:
        result = decide(
            pool, article_id, signal_only, regroup, revision, current["grouping_status"] != "complete" and current["selection_adds_value"] is None
        )
        _mark(pool, article_id, revision)
        publish_article(pool, article_id)
        return result
    except Exception as error:
        # A late failed model call must not undo an editor's completed decision, or a fact already committed before an optional downstream operation failed.
        # Shutdown, budget waits and an unbilled provider outage (every route rejected the request as retryable) leave work pending for the repair sweep.
        kind = failure_kind(error)
        waiting = SHUTDOWN.is_set() or kind in ("budget", "outage")
        with pool.connection() as conn:
            conn.execute(
                """UPDATE articles SET grouping_status = %s, grouped_at = NULL, grouping_receipt_id = %s, grouping_error = %s
                   WHERE id = %s AND revision = %s AND grouping_status <> 'complete' AND %s""",
                (
                    "pending" if waiting else "failed",
                    failed_receipt_id(error),
                    js_slice(_error_text(error), 0, 2000),
                    article_id,
                    revision,
                    not isinstance(error, GroupingSupersededError),
                ),
            )
        publish_article(pool, article_id)
        raise


def _error_text(error: BaseException) -> str:
    """String(error) in JavaScript: the class name and the message."""
    return f"{type(error).__name__}: {error}"


def unsupported_material(output: dict | None) -> bool:
    """An analysis that says the material cannot establish what the current news is: grouping confirms no fact for it."""
    return bool(output) and output.get("scope") == "unknown" and not output.get("fact")


def decide(pool, article_id: str, signal_only: bool, regroup: bool, revision: int, recheck_selection: bool) -> dict:
    with pool.connection() as conn:
        a = conn.execute(
            """SELECT a.id, a.revision, a.title, a.url, a.published_at, a.publication_time, a.discovered_at, a.grouped_at, a.body_text, a.x_post, a.backfill,
                      s.id AS source_id, s.name AS source_name, s.signal_group_id, (s.tier = 'T1') AS first_party, s.participation_mode
               FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s""",
            (article_id,),
        ).fetchone()
        if a and a["revision"] != revision:
            raise GroupingSupersededError("Grouping input changed before it was read")
        if not a:
            return {"verdict": "skipped"}
        observed_at = a["published_at"] or a["discovered_at"]
        source = {"id": a["source_id"], "signal_group_id": a["signal_group_id"]}

        # Manual decisions win over any model decision: a manual membership, or "keep standalone".
        manual = manual_decision(conn, article_id)
        if manual:
            mark_grouped(conn, article_id, a["revision"], {"addsValue": True, "reason": "人工確認的歸屬"})
            return {"verdict": "manual", "factId": manual["factId"]}
        an = conn.execute(
            f"""SELECT input_revision, relevance, title_zh, summary_zh, output, {latest_composite_condition("%(id)s")} AS composite
                FROM analyses WHERE article_id = %(id)s ORDER BY input_revision DESC, id DESC LIMIT 1""",
            {"id": article_id},
        ).fetchone()
    if an and an["input_revision"] != a["revision"] and a["participation_mode"] == "editorial":
        raise GroupingSupersededError("Current material revision is still waiting for analysis")
    composite = bool(an and an["composite"] is True)
    unsupported = unsupported_material(an["output"] if an else None)
    historical = is_historical(a["published_at"], a["discovered_at"], a["backfill"])
    left: list[int] = []
    if composite or unsupported or historical:
        with pool.connection() as conn, conn.transaction():
            lock_current_revision(conn, article_id, a["revision"])
            left = reset_automatic(conn, article_id)

    # History founds no event and adds no heat; a regroup takes it out of any it joined.
    if historical:
        _mark(pool, article_id, a["revision"], {"addsValue": True, "reason": "歷史資料按原文時間歸檔"})
        return {"verdict": "historical"}

    if signal_only or a["participation_mode"] != "editorial":
        return group_signal(pool, a, source, observed_at, regroup)

    # Explicitly insufficient material supplies no event identity. A missing (older) scope is separate.
    if unsupported:
        _mark(pool, article_id, a["revision"], {"addsValue": False, "reason": "材料不足以確認當前訊息"})
        return {"verdict": "standalone"}

    kept = current_membership(pool, article_id)
    # A material revision clears its value decision, but keeps its established membership. Reuse that identity after judging the replacement material below; ordinary
    # repeats and confirmed nulls from before the value check keep their existing value without buying another judgement.
    if kept and not recheck_selection:
        _mark(pool, article_id, a["revision"])
        return {"verdict": "kept", "factId": kept["factId"], "storyId": kept["storyId"]}

    frame = (an["output"] or {}).get("fact") if an else None
    frame = frame if isinstance(frame, dict) else None
    if not an or an["relevance"] != "pass":
        _mark(pool, article_id, a["revision"], {"addsValue": False, "reason": "未通過內容分析"})
        return {"verdict": "standalone"}
    title = an["title_zh"] or a["title"]
    scope = (an["output"] or {}).get("scope")
    query = {
        "title": title, "source": a["source_name"], "firstParty": a["first_party"], "at": a["published_at"], "publicationTime": a["publication_time"], "summary": an["summary_zh"],
        "scope": "composite" if composite else "single" if scope == "single" else "unknown",
        "frame": {"subject": frame.get("subject"), "action": frame.get("action"), "object": frame.get("object"), "occurredAt": frame.get("occurredAt")} if frame else None,
    }  # fmt: skip
    new_title = js_slice(js_str((frame or {}).get("title") or title), 0, 60)

    related = related_posts(pool, a)
    same_url, referenced = related["sameUrl"], related["referenced"]
    # The URL settles identity, but an unseen fact still needs its first reading-value decision.
    selected_same_url = False
    if same_url and not composite and not kept:
        views = candidate_views(
            pool, [{"factId": same_url["fact_id"], "storyId": same_url["story_id"], "factTitle": same_url["fact_title"], "score": 1}]
        )
        selected_same_url = bool(views and views[0].selected is True)
    text = report_text(title, an["summary_zh"])
    cands = (
        []
        if selected_same_url
        else candidate_views(
            pool, recall_facts(pool, article_id, text, RECALL_MIN_COSINE, RECALL_TOP_FACTS, [*referenced, *([same_url] if same_url else [])])
        )
    )
    reading = [] if selected_same_url else recall_selected_background(pool, article_id, text, RECALL_MIN_COSINE, [c.fact_id for c in cands])
    verdicts: dict = {}
    selection = NO_SELECTED_COVERAGE
    receipts: list[int] = []
    if cands or reading:
        judged = judge_batch(pool, article_id, {**query, "sourceText": a["body_text"]}, cands, reading)
        verdicts, selection = judged["verdicts"], judged["selection"]
        receipts.append(judged["receiptId"])

    def with_verdicts(c) -> dict:
        return {"id": c.fact_id, "score": c.score, **_verdict_fields(verdicts.get(c.fact_id))}

    if composite:
        # Use the existing batch judgement only to find mentions. Even a mistaken SAME_OCCURRENCE answer cannot give a composite a primary/report membership or
        # trigger consolidation.
        receipt_id = receipts[0] if receipts else None
        with pool.connection() as conn, conn.transaction():
            lock_current_revision(conn, article_id, a["revision"])
            late = manual_decision(conn, article_id)
            if late:
                if receipt_id is not None:
                    complete_receipt(conn, receipt_id)
            else:
                for c in cands:
                    v = verdicts.get(c.fact_id)
                    if v and v.relation != "UNRELATED" and v.confidence >= TIE_MIN_CONFIDENCE:
                        conn.execute(
                            "INSERT INTO fact_articles (fact_id, article_id, role, created_at) VALUES (%s, %s, 'mention', %s) ON CONFLICT (fact_id, article_id) DO NOTHING",
                            (c.fact_id, article_id, observed_at),
                        )
                record_decision(conn, article_id, None, None, "roundup", [with_verdicts(c) for c in cands], receipt_id)
                mark_grouped(conn, article_id, a["revision"], selection)
                if receipt_id is not None:
                    complete_receipt(conn, receipt_id)
        # A newly identified composite may have supplied an older digest. Rebuild its former stories from their remaining reports, without redirecting an emptied story
        # into an unrelated one.
        for sid in left:
            enqueue(pool, QUEUES["digest"], {"storyId": sid}, singleton_key=f"story:{sid}")
        return {"verdict": "manual", "factId": late["factId"]} if late else {"verdict": "roundup"}
    if kept:
        # A sibling already selected for this same fact still permits representative replacement.
        if any(c.fact_id == kept["factId"] and c.selected for c in cands):
            selection = {"addsValue": True, "reason": "同一新聞的代表報道候選"}
        with pool.connection() as conn, conn.transaction():
            lock_current_revision(conn, article_id, a["revision"])
            late = manual_decision(conn, article_id)
            mark_grouped(conn, article_id, a["revision"], {"addsValue": True, "reason": "人工確認的歸屬"} if late else selection)
            if not late:
                record_decision(
                    conn, article_id, kept["factId"], kept["storyId"], "kept", [with_verdicts(c) for c in cands], receipts[0] if receipts else None
                )
            _complete_all(conn, receipts)
        return {"verdict": "manual", "factId": late["factId"]} if late else {"verdict": "kept", "factId": kept["factId"], "storyId": kept["storyId"]}
    verdict = "new-story"
    fact_id = story_id = None

    if same_url:
        verdict, fact_id, story_id = "same-url", same_url["fact_id"], same_url["story_id"]
    elif cands:
        for pick in same_occurrence(cands, verdicts):
            if pick.score >= CONFIRM_BELOW_COSINE:
                fact_id = pick.fact_id
                break
            review = confirm_merge(pool, article_id, query, pick)
            receipts.append(review["receiptId"])
            if review["relation"] == "SAME_OCCURRENCE":
                fact_id = pick.fact_id
                break
            if review["relation"] == "SAME_STORY" and pick.story_root:
                story_id = pick.story_id
                break
        if fact_id:
            verdict = "same-fact"
            story_id = next(c for c in cands if c.fact_id == fact_id).story_id
        elif story_id:
            verdict = "new-fact-in-story"
        else:
            dev = story_for_development(cands, verdicts)
            if dev:
                verdict, story_id = "new-fact-in-story", dev.story_id

    decision_candidates = [
        {"id": c.fact_id, "score": _round3(c.score), **{k: v for k, v in (("relation", verdicts[c.fact_id].relation), ("confidence", verdicts[c.fact_id].confidence)) if c.fact_id in verdicts}} for c in cands
    ]  # fmt: skip
    if same_url:
        decision_candidates.append({"id": same_url["fact_id"], "score": 1, "relation": "SAME_OCCURRENCE", "confidence": 1})
    # Only a fact already shown in selected has a representative slot to replace. A duplicate of an unselected fact must retain the value judgement, including a prior
    # low-increment rejection.
    if verdict in ("same-fact", "same-url") and (selected_same_url or any(c.fact_id == fact_id and c.selected for c in cands)):
        selection = {"addsValue": True, "reason": "同一新聞的代表報道候選"}

    # Written under the article's row lock after reading the manual state again: a detach or other manual decision made while the model was answering wins
    # (detach_from_fact takes the same lock).
    with pool.connection() as conn, conn.transaction():
        lock_current_revision(conn, article_id, a["revision"])
        late = manual_decision(conn, article_id)
        if late:
            mark_grouped(conn, article_id, a["revision"], {"addsValue": True, "reason": "人工確認的歸屬"})
            _complete_all(conn, receipts)
            return {"verdict": "manual", "factId": late["factId"]}
        if (
            story_id is not None
            and not conn.execute("SELECT id FROM stories WHERE id = %s AND merged_into IS NULL FOR UPDATE", (story_id,)).fetchone()
        ):
            raise RuntimeError("Grouping target changed; retry against the current story")
        story = story_id if story_id is not None else create_story(conn, new_title, observed_at)
        fact = fact_id if fact_id is not None else create_fact(conn, story, new_title, frame, observed_at)
        has_primary = conn.execute("SELECT count(*) AS n FROM fact_articles WHERE fact_id = %s AND role = 'primary'", (fact,)).fetchone()
        role = "primary" if a["first_party"] and int(has_primary["n"] if has_primary else 0) == 0 else "report"
        quotes = (
            [c.get("quote") for c in (frame or {}).get("conditions", []) if isinstance(c, dict)]
            if isinstance((frame or {}).get("conditions"), list)
            else []
        )
        evidence = "\n".join(x for x in [(frame or {}).get("evidence"), *quotes] if isinstance(x, str) and x) or None
        conn.execute(
            "INSERT INTO fact_articles (fact_id, article_id, role, evidence, created_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (fact_id, article_id) DO NOTHING",
            (fact, article_id, role, evidence, observed_at),
        )
        record_signal(conn, story, article_id, source, "editorial", observed_at)
        record_decision(conn, article_id, fact, story, verdict, decision_candidates, receipts[0] if receipts else None)
        mark_grouped(conn, article_id, a["revision"], selection)
        _complete_all(conn, receipts)
    result: dict = {"verdict": verdict, "factId": fact, "storyId": story}

    # Other stories this report is firmly tied to: one story may have grown two roots. Best effort: the report's own decision is written; a failed comparison is
    # reported in the job's result.
    tied: dict[int, None] = {story: None}
    for c in cands:
        v = verdicts.get(c.fact_id)
        if firmly_tied(v.relation if v else None, v.confidence if v else None):
            tied[c.story_id] = None
    if len(tied) > 1:
        try:
            result["consolidated"] = consolidate(pool, list(tied))
            live = live_story(pool, story)
            result["storyId"] = live if live is not None else story
        except Exception as error:
            result["consolidationError"] = js_slice(_error_text(error), 0, 300)
    redirected = redirect_emptied_stories(pool, article_id, result["storyId"])
    if redirected:
        result["redirected"] = redirected
    # Discussion posts that reply to or quote this post and came first now have its story, whichever fact it joined (the posts waiting on an original wake when the
    # original arrives).
    x_post = a["x_post"] or {}
    if x_post.get("tweetId"):
        try:
            result["reclaimed"] = reclaim_waiting(pool, x_post["tweetId"])
        except Exception as error:
            result["reclaimError"] = js_slice(_error_text(error), 0, 300)
    # The fact, new or not, may now be what earlier discussion posts were about in other words.
    rematch(pool, result, article_id, text, fact, SIGNAL_MIN_COSINE)
    return result


# ── discussion posts ────────────────────────────────────────────────────────────

UNATTACHED_SIGNAL = f"""s.participation_mode = 'hot_signal' AND a.processing_state = 'skipped' AND {NEWS_CONDITION}
  AND NOT EXISTS (SELECT 1 FROM story_signals ss WHERE ss.article_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM grouping_decisions d WHERE d.article_id = a.id AND d.verdict <> 'signal-unmatched')
  AND NOT EXISTS (SELECT 1 FROM grouping_overrides o WHERE o.article_id = a.id)"""  # discussion posts not yet attached to any story (a post a person placed or detached is left alone)


def answered_facts(article: str) -> str:
    """The facts the judge already answered for while leaving discussion post `article` unattached: it is not asked about them again (an attachment it made stays open
    to an editor's regroup)."""
    return f"""ARRAY(
  SELECT (c->>'id')::bigint FROM grouping_decisions j CROSS JOIN LATERAL jsonb_array_elements(j.candidates) c
  WHERE j.article_id = {article} AND j.verdict = 'signal-unmatched')"""


def reclaim_waiting(pool, tweet_id: str) -> int:
    """A reaction often comes before the post it quotes is collected. When the original joins a fact, the recent unattached posts that reply to or quote it are
    grouped again; group_signal then attaches them through the reference."""
    with pool.connection() as conn:
        posts = conn.execute(
            f"""SELECT a.id FROM articles a JOIN sources s ON s.id = a.source_id
                WHERE a.discovered_at > now() - make_interval(hours => %(hours)s) AND {UNATTACHED_SIGNAL}
                  AND (a.x_post->>'replyTo' = %(tweet)s OR substring(a.x_post->'quoted'->>'url' from '/status/([0-9]+)') = %(tweet)s)""",
            {"hours": WAIT_HOURS, "tweet": tweet_id},
        ).fetchall()
    for p in posts:
        enqueue(pool, QUEUES["group"], {"articleId": p["id"], "signalOnly": True}, singleton_key=p["id"], priority=grouping_priority(True, False))
    return len(posts)


def rematch_signals(pool, article_id: str, query_text: str, fact_id: int, min_score: float) -> int:
    """Discussion posts often come before what ties them to a fact. Whenever a fact gains evidence (a report founds or joins it, a post attaches to it), the unattached
    posts close to that evidence that still count for heat are grouped again, each judged the usual way against all candidates. A post the judge already answered for
    this fact is left as it is."""
    if not embeddings_available():
        return 0
    mine = vectors_for(pool, [{"id": article_id, "text": query_text}]).get(article_id)
    if mine is None:
        return 0
    with pool.connection() as conn:
        posts = conn.execute(
            f"""SELECT a.id, a.title, left(a.body_text, 300) AS body_text FROM articles a JOIN sources s ON s.id = a.source_id
                WHERE a.discovered_at > now() - make_interval(hours => %(hours)s)
                  AND coalesce(a.published_at, a.discovered_at) > now() - make_interval(hours => %(hours)s) AND {UNATTACHED_SIGNAL}
                  AND NOT %(fact)s::bigint = ANY({answered_facts("a.id")})""",
            {"hours": HEAT_WINDOW_HOURS, "fact": fact_id},
        ).fetchall()
    vectors = vectors_for(pool, [{"id": p["id"], "text": signal_text(p)} for p in posts])
    close = 0
    for p in posts:
        v = vectors.get(p["id"])
        if v is None or cosine32(mine, v) < min_score:
            continue
        # A post still waiting in the queue keeps that job (same key): it will meet the new evidence anyway.
        enqueue(pool, QUEUES["group"], {"articleId": p["id"], "signalOnly": True}, singleton_key=p["id"], priority=grouping_priority(True, False))
        close += 1
    return close


def rematch(pool, result: dict, article_id: str, text: str, fact_id: int, min_score: float) -> None:
    """Best effort: the decision that brought the evidence is written; a failed look is reported in the job's result."""
    try:
        result["rematched"] = rematch_signals(pool, article_id, text, fact_id, min_score)
    except Exception as error:
        result["rematchError"] = js_slice(_error_text(error), 0, 300)


def group_signal(pool, a: dict, source: dict, observed_at, regroup: bool) -> dict:
    """Discussion evidence (hot_signal sources): the post the item replies to or quotes decides first; otherwise clear candidates, recalled by reports and by the posts
    already attached, are judged, and a nearly identical report attaches without a call."""
    text = signal_text(a)

    def write(target, verdict: str, candidates: list, receipt_id=None) -> dict:
        # Discussion evidence obeys the same article lock and last-minute manual check as reports. The evidence and decision commit together; a retry cannot leave half
        # of a signal attached.
        with pool.connection() as conn, conn.transaction():
            lock_current_revision(conn, a["id"], a["revision"])
            manual = manual_decision(conn, a["id"])
            if not manual:
                if target:
                    record_signal(conn, target["storyId"], a["id"], source, "signal", observed_at)
                record_decision(
                    conn, a["id"], target["factId"] if target else None, target["storyId"] if target else None, verdict, candidates, receipt_id
                )
            if receipt_id is not None:
                complete_receipt(conn, receipt_id)
            result = (
                {"verdict": "manual", "factId": manual["factId"]}
                if manual
                else {"verdict": verdict, **({"storyId": target["storyId"]} if target else {})}
            )
        # The posts that came first and say what this one says may now be about its fact too.
        if target and result["verdict"] != "manual":
            rematch(pool, result, a["id"], text, target["factId"], SIGNAL_PEER_MIN_COSINE)
        return result

    referenced = related_posts(pool, a)["referenced"]
    if referenced:
        t = referenced[0]
        return write(
            {"factId": t["fact_id"], "storyId": t["story_id"]},
            "signal-native",
            [{"id": t["fact_id"], "score": 1, "relation": "SAME_STORY", "confidence": 1}],
        )
    if not embeddings_available():
        return {"verdict": "signal-unmatched"}

    def as_candidates(recalled: list[dict], verdicts: dict | None = None) -> list[dict]:
        out = []
        for r in recalled:
            v = verdicts.get(r["factId"]) if verdicts else None
            out.append({"id": r["factId"], "score": _round3(r["score"]), **({"relation": v.relation, "confidence": v.confidence} if v else {})})
        return out

    answered: set[int] = set()
    if not regroup:
        with pool.connection() as conn:
            answered = {r["id"] for r in conn.execute(f"SELECT unnest({answered_facts('%s')}) AS id", (a["id"],)).fetchall()}

    def open_(recalled: list[dict]) -> list[dict]:
        return [r for r in recalled if r["factId"] not in answered]

    by_reports = open_(recall_facts(pool, a["id"], text, SIGNAL_MIN_COSINE, math.inf))
    if by_reports and by_reports[0]["score"] >= SIGNAL_AUTO_COSINE:
        top = by_reports[0]
        return write({"factId": top["factId"], "storyId": top["storyId"]}, "signal", as_candidates(by_reports[:SIGNAL_TOP_FACTS]))
    by_posts = open_(recall_facts_by_posts(pool, a["id"], text, SIGNAL_PEER_MIN_COSINE, HEAT_WINDOW_HOURS))
    recalled = best_per_fact([*by_reports, *by_posts], SIGNAL_TOP_FACTS)
    if not recalled:
        # Recorded, so a post that found nothing is told apart from one never decided.
        return write(None, "signal-unmatched", [])
    cands = candidate_views(pool, recalled)
    if not cands:
        return {"verdict": "signal-unmatched"}
    query = {
        "title": a["title"],
        "source": a["source_name"],
        "firstParty": False,
        "at": a["published_at"],
        "publicationTime": a["publication_time"],
        "summary": js_slice(a["body_text"], 0, 300) if a["body_text"] else None,
    }
    judged = judge_signal(pool, a["id"], query, cands)
    verdicts = judged["verdicts"]
    target = signal_target(cands, verdicts)
    return write(
        {"factId": target.fact_id, "storyId": target.story_id} if target else None,
        "signal" if target else "signal-unmatched",
        as_candidates(recalled, verdicts),
        judged["receiptId"],
    )
