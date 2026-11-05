"""Candidate recall for grouping (group.py): the reports of the last RECALL_DAYS days that belong to a live fact, compared with the report being decided by the same
title-and-summary embedding on both sides (without an embedding key, by their shared character bigrams), plus the same URL and the X post it replies to or quotes. A
membership counts as evidence only when its report is not a composite or explicitly withdrawn. Selected reports are reading context for READING_DAYS: a reader who
saw a launch in 精選 still knows it when its maker introduces it again. A discussion post is also compared with the posts already attached to a fact that still
count for its heat (recall_facts_by_posts). Port of events/recall.ts."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

import numpy as np

from newsroom.engine.embeddings import embeddings_available, ensure_embeddings
from newsroom.engine.events.relate import GROUP_BODY_CHARS, CandidateView, lexical_similarity, report_text, signal_text
from newsroom.engine.lib.ids import sha256
from newsroom.engine.publication.scope import grouping_evidence_condition, own_fact_evidence_condition, scope_params, selected_condition
from newsroom.engine.publication.stories import REPRESENTATIVE_COLUMNS, pick_representative
from newsroom.engine.time import beijing_date

RECALL_DAYS = 14  # reports discovered this recently are candidates (keyed on discovery, so an old page found today still meets its peers)
READING_DAYS = 60  # selected reports stay reader coverage this long
LEXICAL_MIN = 0.25  # the lexical fallback's bar: shared bigrams are not on the cosine scale, so it has its own


def root_fact_of(story: str) -> str:
    """The fact that starts a story for a later identity decision: the earliest eligible report, excluding composites and withdrawals. Fact ids do not follow
    time after a merge or an import; a fact without eligible reports starts nothing. `story` is an SQL expression."""
    return f"""(
  SELECT y.id FROM facts y
  JOIN fact_articles z ON z.fact_id = y.id AND z.role IN ('primary', 'report') AND {grouping_evidence_condition("z.article_id")}
  JOIN publications q ON q.article_id = z.article_id
  WHERE y.story_id = {story}
  ORDER BY coalesce(q.published_at, q.discovered_at), y.id
  LIMIT 1)"""


def _recall_pool(pool) -> list[dict]:
    """Reports of the recall window that belong to a live fact."""
    with pool.connection() as conn:
        return conn.execute(
            f"""SELECT fa.article_id, fa.fact_id, f.story_id, f.title AS fact_title, coalesce(p.revision, 0) AS revision
                FROM fact_articles fa
                JOIN facts f ON f.id = fa.fact_id
                JOIN stories st ON st.id = f.story_id AND st.merged_into IS NULL
                JOIN articles a ON a.id = fa.article_id
                LEFT JOIN publications p ON p.article_id = a.id
                WHERE fa.role IN ('primary', 'report') AND {grouping_evidence_condition("a.id")} AND a.discovered_at > now() - make_interval(days => %(days)s)""",
            {"days": RECALL_DAYS},
        ).fetchall()


def _attached_posts_pool(pool, hours: int) -> list[dict]:
    """Discussion posts attached to a live fact (the one their attaching decision named) whose source time is within the last `hours`, from sources that are
    discussion evidence now. A post an editor placed is a report of the fact they chose (recall pool), never evidence of the one the model picked."""
    with pool.connection() as conn:
        return conn.execute(
            f"""SELECT ss.article_id, f.id AS fact_id, f.story_id, f.title AS fact_title, a.revision
                FROM story_signals ss
                JOIN articles a ON a.id = ss.article_id
                JOIN sources s ON s.id = a.source_id AND s.participation_mode = 'hot_signal'
                JOIN LATERAL (SELECT d.fact_id FROM grouping_decisions d
                              WHERE d.article_id = ss.article_id AND d.verdict IN ('signal', 'signal-native') ORDER BY d.id DESC LIMIT 1) d ON true
                JOIN facts f ON f.id = d.fact_id AND f.story_id = ss.story_id
                JOIN stories st ON st.id = f.story_id AND st.merged_into IS NULL
                WHERE ss.observed_at > now() - make_interval(hours => %(hours)s) AND {grouping_evidence_condition("ss.article_id")}
                  AND NOT EXISTS (SELECT 1 FROM fact_articles m WHERE m.article_id = ss.article_id AND m.manual)""",
            {"hours": hours},
        ).fetchall()


def _selected_reading_pool(pool, candidate_facts: list[int]) -> list[dict]:
    """Already displayed reports supply reading context, never identity evidence: those without a fact, and, past the recall window, those whose fact is not an
    identity candidate (shown there already)."""
    with pool.connection() as conn:
        return conn.execute(
            f"""SELECT p.article_id, p.revision, p.fact_id, p.title, p.summary, s.name AS source, (s.tier = 'T1') AS "firstParty",
                  p.published_at AS at, a.publication_time AS "publicationTime", an.output->>'scope' AS scope
                FROM publications p JOIN articles a ON a.id = p.article_id JOIN sources s ON s.id = p.source_id LEFT JOIN analyses an ON an.id = p.analysis_id
                WHERE {selected_condition()} AND p.discovered_at > now() - make_interval(days => %(reading)s)
                  AND (p.fact_id IS NULL OR (p.discovered_at <= now() - make_interval(days => %(recall)s) AND NOT p.fact_id = ANY(%(facts)s::bigint[])))""",
            {**scope_params(datetime.now(UTC)), "reading": READING_DAYS, "recall": RECALL_DAYS, "facts": candidate_facts},
        ).fetchall()


def _report_texts(pool, ids: list[str]) -> dict[str, str]:
    """The public title and summary of reports (the analysis when a report has no publication yet)."""
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT a.id, coalesce(p.title, an.title_zh, a.title) AS title, coalesce(p.summary, an.summary_zh, '') AS summary
               FROM articles a
               LEFT JOIN publications p ON p.article_id = a.id
               LEFT JOIN LATERAL (SELECT title_zh, summary_zh FROM analyses x WHERE x.article_id = a.id ORDER BY input_revision DESC, id DESC LIMIT 1) an ON true
               WHERE a.id = ANY(%s)""",
            (ids,),
        ).fetchall()
    return {r["id"]: report_text(r["title"], r["summary"]) for r in rows}


def _post_texts(pool, ids: list[str]) -> dict[str, str]:
    """The texts of discussion posts."""
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, title, left(body_text, 300) AS body_text FROM articles WHERE id = ANY(%s)", (ids,)).fetchall()
    return {r["id"]: signal_text(r) for r in rows}


# Vectors of the recall window stay in the worker process; only new or changed texts are embedded (and stored) again. Grouping is serial, so one process holds the
# whole window. They are kept as float32, the precision the embeddings table stores (real[]), so a vector straight from the provider and the same vector read back
# later give the same cosine.
_vector_cache: dict[str, dict] = {}


def vectors_for(pool, items: list[dict]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    missing = []
    for it in items:
        digest = sha256(it["text"])
        cached = _vector_cache.get(it["id"])
        if cached and cached["hash"] == digest:
            if it.get("revision") is not None:
                cached["revision"] = it["revision"]
            out[it["id"]] = cached["vector"]
        else:
            missing.append({**it, "hash": digest})
    if missing:
        got = ensure_embeddings(pool, [{"id": m["id"], "text": m["text"]} for m in missing])
        for m in missing:
            v = got.get(m["id"])
            if v is None:
                continue
            vector = np.asarray(v, dtype=np.float32)
            _vector_cache[m["id"]] = {"hash": m["hash"], "vector": vector, "revision": m.get("revision")}
            out[m["id"]] = vector
    if len(_vector_cache) > 30_000:
        _vector_cache.clear()
    return out


def cosine32(a: np.ndarray, b: np.ndarray) -> float:
    # Vectors of different sizes (EMBEDDING_DIMS changed, or left to the provider) are not comparable.
    if a.shape != b.shape:
        return 0.0
    x, y = a.astype(np.float64), b.astype(np.float64)
    dot, na, nb = float(np.dot(x, y)), float(np.dot(x, x)), float(np.dot(y, y))
    return dot / math.sqrt(na * nb) if na and nb else 0.0


def _similar_reports(pool, query_id: str, query_text: str, rows: list[dict], min_score: float, texts=None) -> dict[str, float]:
    """Both identity candidates and reading context use the same stored vectors (or the lexical fallback)."""
    texts = texts or _report_texts
    scores: dict[str, float] = {}
    if rows and not embeddings_available():
        for id_, text in texts(pool, list(dict.fromkeys(r["article_id"] for r in rows))).items():
            s = lexical_similarity(query_text, text)
            if s >= LEXICAL_MIN:
                scores[id_] = s
        return scores
    if rows:
        # Publication revisions invalidate changed reports without fetching every cached text. The text hash still avoids paying again when a revision changed only
        # non-text public fields.
        ids = list(dict.fromkeys(r["article_id"] for r in rows))
        revisions = {r["article_id"]: r.get("revision") for r in rows}
        cached: dict[str, np.ndarray] = {}  # keep this call's hits even if loading new vectors clears the process cache
        uncached = []
        for id_ in ids:
            hit = _vector_cache.get(id_)
            if not hit or hit["revision"] != revisions.get(id_):
                uncached.append(id_)
            else:
                cached[id_] = hit["vector"]
        uncached_texts = texts(pool, uncached)
        fresh = vectors_for(
            pool,
            [
                {"id": query_id, "text": query_text},
                *[x for x in ({"id": i, "text": uncached_texts.get(i, ""), "revision": revisions.get(i)} for i in uncached) if x["text"]],
            ],
        )
        mine = fresh.get(query_id)
        if mine is not None:
            for r in rows:
                v = fresh.get(r["article_id"])
                if v is None:
                    v = cached.get(r["article_id"])
                if v is None:
                    continue
                s = cosine32(mine, v)
                if s >= min_score:
                    scores[r["article_id"]] = s
    return scores


def _to_recalled(r: dict, score: float) -> dict:
    return {"factId": r["fact_id"], "storyId": r["story_id"], "factTitle": r["fact_title"], "score": score}


def _scored_rows(rows: list[dict], scores: dict[str, float]) -> list[dict]:
    return [_to_recalled(r, scores[r["article_id"]]) for r in rows if r["article_id"] in scores]


def best_per_fact(items: list[dict], top: float = math.inf) -> list[dict]:
    """Each fact once, at its best score, most similar first."""
    best: dict[int, dict] = {}
    for it in items:
        prev = best.get(it["factId"])
        if prev is None or it["score"] > prev["score"]:
            best[it["factId"]] = it
    ordered = sorted(best.values(), key=lambda x: -x["score"])
    return ordered if top == math.inf else ordered[: int(top)]


def recall_facts(pool, query_id: str, query_text: str, min_score: float, top: float, boost: list[dict] | None = None) -> list[dict]:
    """The best report of each fact counts; boosted facts (quoted/replied-to posts) are always included."""
    rows = [r for r in _recall_pool(pool) if r["article_id"] != query_id]
    scores = _similar_reports(pool, query_id, query_text, rows, min_score)
    return best_per_fact([*_scored_rows(rows, scores), *[_to_recalled(r, 1) for r in (boost or [])]], top)


def recall_facts_by_posts(pool, query_id: str, query_text: str, min_score: float, hours: int) -> list[dict]:
    """The facts the discussion posts attached within the last `hours` point to; the closest post of each fact counts."""
    rows = [r for r in _attached_posts_pool(pool, hours) if r["article_id"] != query_id]
    return best_per_fact(_scored_rows(rows, _similar_reports(pool, query_id, query_text, rows, min_score, _post_texts)))


def recall_selected_background(
    pool, query_id: str, query_text: str, min_score: float, candidate_facts: list[int] | None = None, top: int = 3
) -> list[dict]:
    """A small separate context lets a single report compare with earlier selected coverage, one report per fact."""
    rows = [r for r in _selected_reading_pool(pool, candidate_facts or []) if r["article_id"] != query_id]
    scores = _similar_reports(pool, query_id, query_text, rows, min_score)
    picked: list[dict] = []
    facts: set[int] = set()
    for r in sorted((r for r in rows if r["article_id"] in scores), key=lambda r: -scores[r["article_id"]]):
        if len(picked) == min(top, 3):
            break
        fact = None if r["fact_id"] is None else int(r["fact_id"])
        if fact is not None and fact in facts:
            continue
        if fact is not None:
            facts.add(fact)
        picked.append(r)
    if not picked:
        return []
    # Read only the saved text of the few matching reports; never fetch or extract for this comparison.
    with pool.connection() as conn:
        bodies = {
            r["id"]: r["text"]
            for r in conn.execute(
                "SELECT id, left(body_text, %s) AS text FROM articles WHERE id = ANY(%s)", (GROUP_BODY_CHARS, [r["article_id"] for r in picked])
            ).fetchall()
        }
    return [
        {"report": {k: v for k, v in r.items() if k not in ("article_id", "revision", "fact_id")}, "sourceText": bodies.get(r["article_id"])}
        for r in picked
    ]  # fmt: skip


def candidate_views(pool, recalled: list[dict]) -> list[CandidateView]:
    """What the judge sees of a candidate fact: its representative report (first-party first, else the earliest), its size, and whether it started its story."""
    if not recalled:
        return []
    ids = [r["factId"] for r in recalled]
    now = datetime.now(UTC)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT ON (fa.fact_id) fa.fact_id, f.story_id, f.title AS fact_title, f.subject, f.action, f.object, f.occurred_at,
                       p.title, p.summary, s.name AS source, (s.tier = 'T1') AS first_party, p.published_at AS at, a.publication_time AS "publicationTime",
                       (SELECT count(*) FROM fact_articles x WHERE x.fact_id = fa.fact_id AND x.role IN ('primary', 'report') AND {grouping_evidence_condition("x.article_id")}) AS members,
                       {root_fact_of("f.story_id")} AS root_fact_id
                FROM fact_articles fa
                JOIN facts f ON f.id = fa.fact_id
                JOIN publications p ON p.article_id = fa.article_id
                JOIN articles a ON a.id = p.article_id
                JOIN sources s ON s.id = p.source_id
                WHERE fa.fact_id = ANY(%(ids)s) AND fa.role IN ('primary', 'report') AND {grouping_evidence_condition("fa.article_id")}
                ORDER BY fa.fact_id, (fa.role = 'primary') DESC, p.timeline_at ASC""",
            {"ids": ids},
        ).fetchall()
        selected_rows = conn.execute(
            f"""SELECT p.fact_id, p.article_id, p.title, p.summary, p.body_mode, p.score, p.timeline_at,
                  s.name AS source, (s.tier = 'T1') AS first_party, p.published_at AS at, a.publication_time AS "publicationTime",
                  {REPRESENTATIVE_COLUMNS}
                FROM publications p JOIN articles a ON a.id = p.article_id JOIN sources s ON s.id = p.source_id JOIN facts f ON f.id = p.fact_id
                WHERE p.fact_id = ANY(%(ids)s) AND {selected_condition()} AND {own_fact_evidence_condition()}""",
            {**scope_params(now), "ids": ids},
        ).fetchall()
    by_fact = {int(r["fact_id"]): r for r in rows}
    out: list[CandidateView] = []
    for r in recalled:
        row = by_fact.get(r["factId"])
        if not row:
            continue
        # Identity uses the eligible reports above. For reading value show exactly the representative already visible in 精選; a merely scored or still-pending report
        # is not prior reader coverage.
        members = [p for p in selected_rows if int(p["fact_id"]) == r["factId"]]
        selected = pick_representative(members) if members else None
        out.append(
            CandidateView(
                fact_id=r["factId"], story_id=int(row["story_id"]), fact_title=row["fact_title"], members=int(row["members"]), story_root=int(row["root_fact_id"]) == r["factId"], score=r["score"],
                selected=selected is not None,
                selected_report={"title": selected["title"], "summary": selected["summary"], "source": selected["source"], "firstParty": selected["first_party"], "at": selected["at"], "publicationTime": selected["publicationTime"]} if selected else None,
                report={
                    "title": row["title"], "source": row["source"], "firstParty": row["first_party"], "at": row["at"], "publicationTime": row["publicationTime"], "summary": row["summary"],
                    "frame": {"subject": row["subject"], "action": row["action"], "object": row["object"], "occurredAt": beijing_date(row["occurred_at"]) if row["occurred_at"] else None},
                },
            )
        )  # fmt: skip
    return out


def related_posts(pool, a: dict) -> dict:
    """The live fact another report of the same page, or the X post this one replies to or quotes, belongs to."""
    with pool.connection() as conn:
        same_url = conn.execute(
            f"""SELECT fa.article_id, fa.fact_id, f.story_id, f.title AS fact_title
                FROM articles b JOIN fact_articles fa ON fa.article_id = b.id AND fa.role IN ('primary', 'report')
                JOIN facts f ON f.id = fa.fact_id JOIN stories st ON st.id = f.story_id AND st.merged_into IS NULL
                WHERE b.url = %s AND b.id <> %s AND {grouping_evidence_condition("fa.article_id")} ORDER BY fa.created_at LIMIT 1""",
            (a["url"], a["id"]),
        ).fetchone()
        x_post = a.get("x_post") or {}
        quoted_url = (x_post.get("quoted") or {}).get("url")
        match = re.search(r"/status/([0-9]+)", quoted_url) if quoted_url else None
        ids = [x for x in (x_post.get("replyTo"), match.group(1) if match else None) if x]
        referenced = (
            conn.execute(
                f"""SELECT fa.article_id, fa.fact_id, f.story_id, f.title AS fact_title
                    FROM articles b JOIN fact_articles fa ON fa.article_id = b.id AND fa.role IN ('primary', 'report')
                    JOIN facts f ON f.id = fa.fact_id JOIN stories st ON st.id = f.story_id AND st.merged_into IS NULL
                    WHERE b.identity_key = ANY(%s) AND b.id <> %s AND {grouping_evidence_condition("fa.article_id")}""",
                ([f"x:{i}" for i in ids], a["id"]),
            ).fetchall()
            if ids
            else []
        )
    return {"sameUrl": same_url, "referenced": referenced}
