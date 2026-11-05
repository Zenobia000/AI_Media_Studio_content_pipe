"""Story consolidation (group.py calls it after a decision). Stories a report is firmly tied to may be one story that grew two roots: their roots are compared
directly and merge only when both models see one story. Stories that stay apart though reports keep tying them list each other as related events
(link_related_stories, hourly). Port of events/consolidate.ts."""

from __future__ import annotations

from dataclasses import dataclass

from newsroom.engine import models as M
from newsroom.engine.analyze import _conn
from newsroom.engine.events.merge import merge_story_into
from newsroom.engine.events.recall import RECALL_DAYS, root_fact_of
from newsroom.engine.events.relate import (
    STORY_REVIEW_MIN_CONFIDENCE,
    firmly_tied,
    pair_schema,
    pair_system,
    pair_user,
    relate_prompt_version,
)  # fmt: skip
from newsroom.engine.events.related import RELATED_MIN_REPORTS, firm_tie, latest_judgements
from newsroom.engine.llm import chat_json
from newsroom.engine.publication.scope import grouping_evidence_condition
from newsroom.engine.receipts import complete_receipt
from newsroom.engine.time import beijing_date


@dataclass
class StoryRoot:
    story_id: int
    at: object
    roundup: bool  # the story started as a multi-topic digest: never merged
    report: dict


def story_root(pool, story_id: int) -> StoryRoot | None:
    """A model-managed story's root; editor-confirmed identities are excluded before paid comparison."""
    with pool.connection() as conn:
        row = conn.execute(
            f"""SELECT f.subject, f.action, f.object, f.occurred_at, p.title, p.summary, s.name AS source, (s.tier = 'T1') AS first_party,
                       p.published_at AS at, a.publication_time AS "publicationTime",
                       (SELECT min(coalesce(q.published_at, q.discovered_at)) FROM fact_articles z JOIN publications q ON q.article_id = z.article_id
                        WHERE z.fact_id = f.id AND z.role IN ('primary', 'report') AND {grouping_evidence_condition("z.article_id")}) AS started_at,
                       EXISTS (SELECT 1 FROM grouping_decisions d WHERE d.article_id = fa.article_id AND d.verdict = 'roundup') AS roundup
                FROM facts f
                JOIN stories st ON st.id = f.story_id AND st.origin <> 'manual'
                JOIN fact_articles fa ON fa.fact_id = f.id AND fa.role IN ('primary', 'report') AND {grouping_evidence_condition("fa.article_id")}
                JOIN publications p ON p.article_id = fa.article_id
                JOIN articles a ON a.id = p.article_id
                JOIN sources s ON s.id = p.source_id
                WHERE f.id = {root_fact_of("%(story)s")}
                ORDER BY (fa.role = 'primary') DESC, p.timeline_at ASC
                LIMIT 1""",
            {"story": story_id},
        ).fetchone()
    if not row:
        return None
    return StoryRoot(
        story_id, row["started_at"], row["roundup"],
        {
            "title": row["title"], "source": row["source"], "firstParty": row["first_party"], "at": row["at"], "publicationTime": row["publicationTime"], "summary": row["summary"],
            "frame": {"subject": row["subject"], "action": row["action"], "object": row["object"], "occurredAt": beijing_date(row["occurred_at"]) if row["occurred_at"] else None},
        },
    )  # fmt: skip


def _judge_stories(pool, capability: str, a: StoryRoot, b: StoryRoot) -> dict:
    route = M.model_route(_conn(pool), capability)
    res = chat_json(pool, {
        "model": route["model"], "fallback_model": route["fallbackModel"], "purpose": "group_story" if capability == "group" else "group_story_review",
        "subject": f"story:{a.story_id}:{b.story_id}", "prompt_version": relate_prompt_version(), "system": pair_system(), "user": pair_user(a.report, b.report),
        "schema": pair_schema, "temperature": 0, "max_tokens": 400,
    })  # fmt: skip
    return {
        "relation": res["data"]["relation"],
        "confidence": res["data"]["confidence"],
        "difference": res["data"]["difference"],
        "receiptId": res["receiptId"],
    }


def live_story(pool, story_id: int) -> int | None:
    current = story_id
    with pool.connection() as conn:
        for _ in range(20):
            st = conn.execute("SELECT merged_into FROM stories WHERE id = %s", (current,)).fetchone()
            if not st:
                return None
            if st["merged_into"] is None:
                return current
            current = int(st["merged_into"])
    return None


def _complete(pool, receipt_id: int) -> None:
    with pool.connection() as conn:
        complete_receipt(conn, receipt_id)


def consolidate(pool, story_ids: list[int]) -> list[dict]:
    """Stories a report is firmly tied to may be one story that grew two roots. Their roots are compared directly, the earliest against each of the others, and a
    story merges into the earliest only when the judge and then the review model both see one occurrence or a direct development: a report tied to two different
    events (a comparison, a roundup) cannot fuse them on its own."""
    live: dict[int, None] = {}
    for sid in story_ids:
        s = live_story(pool, sid)
        if s is not None:
            live[s] = None
    if len(live) < 2:
        return []
    roots = [r for r in (story_root(pool, sid) for sid in live) if r and not r.roundup]
    if len(roots) < 2:
        return []
    roots.sort(key=lambda r: (r.at, r.story_id))
    anchor, *others = roots
    out: list[dict] = []
    for other in others:
        base = {"from": other.story_id, "into": anchor.story_id, "fromTitle": other.report["title"], "intoTitle": anchor.report["title"]}
        first = _judge_stories(pool, "group", anchor, other)
        _complete(pool, first["receiptId"])
        if not firmly_tied(first["relation"], first["confidence"]):
            out.append({**base, "merge": False, "first": first["relation"], "second": None, "difference": first["difference"]})
            continue
        # The review model reads the pair the other way round.
        second = _judge_stories(pool, "groupReview", other, anchor)
        _complete(pool, second["receiptId"])
        merge = firmly_tied(second["relation"], second["confidence"], STORY_REVIEW_MIN_CONFIDENCE) and bool(
            merge_story_into(
                pool,
                other.story_id,
                anchor.story_id,
                f"同一事件（{first['relation']}，複核 {second['relation']}）：{other.report['title']}｜{anchor.report['title']}",
                "grouping",
            )
        )
        out.append(
            {
                **base,
                "merge": merge,
                "first": first["relation"],
                "second": second["relation"],
                "difference": first["difference"] or second["difference"],
            }
        )
    return out


def _started_by_roundup(story: str) -> str:
    """A multi-topic digest is a report of the story's root fact (digests now only mention facts, so this marks older stories; one that mentions the root does not
    count): the story is neither merged nor linked."""
    return f"""EXISTS (
  SELECT 1 FROM fact_articles r JOIN grouping_decisions d ON d.article_id = r.article_id AND d.verdict = 'roundup'
  WHERE r.fact_id = {root_fact_of(story)} AND r.role <> 'mention' AND {grouping_evidence_condition("r.article_id")})"""


def link_related_stories(pool) -> dict:
    """Stories that stay apart although reports tie them (a reaction, a development of a later fact, a comparison) list each other as related events: at least two
    reports whose latest judgement falls in the recall window, each firmly tied to a fact of the other story, none of them a roundup, neither story started by one.
    A single tie is too often a stray answer about one candidate among many. Links are only added; public reads check saved bridge evidence after explicit changes,
    independently of the recall window. A merged story drops out where links are read."""
    with pool.connection() as conn:
        row = conn.execute(
            f"""WITH latest AS ({latest_judgements("d.created_at > now() - make_interval(days => %(days)s)")}),
                ties AS (
                  SELECT DISTINCT l.article_id, own.story_id AS a, other.story_id AS b
                  FROM latest l
                  JOIN fact_articles fa ON fa.article_id = l.article_id AND fa.role IN ('primary', 'report') AND {grouping_evidence_condition("fa.article_id")}
                  JOIN facts own ON own.id = fa.fact_id
                  CROSS JOIN LATERAL jsonb_array_elements(l.candidates) c
                  JOIN facts other ON other.id = (c->>'id')::bigint
                  WHERE {firm_tie("l", "c")} AND other.story_id <> own.story_id),
                pairs AS (
                  SELECT least(a, b) AS x, greatest(a, b) AS y FROM ties GROUP BY 1, 2 HAVING count(DISTINCT article_id) >= %(min)s),
                linked AS (
                  SELECT p.x, p.y FROM pairs p
                  JOIN stories sx ON sx.id = p.x AND sx.merged_into IS NULL
                  JOIN stories sy ON sy.id = p.y AND sy.merged_into IS NULL
                  WHERE NOT {_started_by_roundup("p.x")} AND NOT {_started_by_roundup("p.y")}),
                added AS (
                  INSERT INTO story_links (story_id, other_id, relation)
                  SELECT x, y, 'related' FROM linked UNION ALL SELECT y, x, 'related' FROM linked
                  ON CONFLICT (story_id, other_id) DO NOTHING RETURNING 1)
                SELECT count(*)::int AS added FROM added""",
            {"days": RECALL_DAYS, "min": RELATED_MIN_REPORTS},
        ).fetchone()
    return {"added": row["added"] if row else 0}
