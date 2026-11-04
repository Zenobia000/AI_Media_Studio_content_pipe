"""Stories (events) and the hot ranking through the public read layer: ports of publication/{stories,hot,representative,story-text,
story-evidence,rules(publicSourceName)}.ts and events/related.ts, the parts v1 uses. The website sees heat values; v1 and MCP only see ranks
and counts."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from newsroom.engine import pack
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.ids import sha256, stable_json
from newsroom.engine.publication.scope import (
    composite_condition,
    evidence_condition,
    listed_condition,
    scope_params,
    story_report_condition,
)  # fmt: skip
from newsroom.engine.publication.v1 import iso
from newsroom.engine.vocabulary import entity_identity

RELATED_MIN_REPORTS = 2
TIE_MIN_CONFIDENCE = 0.8
TIE_RELATIONS = ["SAME_OCCURRENCE", "SAME_STORY"]
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def public_source_name(name: str) -> str:
    """The name readers see: parenthetical notes dropped, and an X source's "X: Name (@handle)" reduced to the name."""
    bare = name
    while re.search(r"（[^（）]*）", bare):
        bare = re.sub(r"（[^（）]*）", " ", bare)
    x = re.match(r"^X[:：]\s*(.*?)\s*(?:\(@[^)]*\))?\s*$", bare)
    cleaned = re.sub(r"\s+", " ", x.group(1) if x else bare).strip()
    handle = re.search(r"@\w+", name)
    return cleaned or (handle.group(0) if handle else None) or name.strip()


def story_url(public_id: str) -> str:
    return site_url(f"/story/{public_id}")


def item_url(article_id: str) -> str:
    return site_url(f"/items/{article_id}")


def v1_story_api_url(public_id: str) -> str:
    """A story's v1 address as v1 links it (related stories' links.api)."""
    return site_url(f"/api/v1/stories/{public_id}")


# ── representative report ───────────────────────────────────────────────────────────────

REPRESENTATIVE_COLUMNS = "s.tier AS source_tier, s.config->>'publisherRole' AS publisher_role, s.owner_entity_id, f.subject AS fact_subject"


def representative_priority(row: dict) -> int:
    """A source's T1 label and its authority to speak for an event are separate inputs."""
    if row.get("source_tier") == "T1":
        return 0
    owner = entity_identity(row.get("owner_entity_id"))
    subjects = [entity_identity(s) for s in re.split(r"[,，、;；+＋&＆/]", row["fact_subject"])] if row.get("fact_subject") else []
    if not owner or any(not s for s in subjects) or owner not in subjects:
        return 3
    return {"organization": 1, "person": 2}.get(row.get("publisher_role") or "", 3)


def pick_representative(rows: list[dict]) -> dict:
    """Within the caller's existing fact/candidate scope: T1, verified publisher, full text, score, time."""
    return sorted(
        rows,
        key=lambda r: (
            representative_priority(r), 0 if r["body_mode"] == "full" else 1, -float(r["score"] or 0), r["timeline_at"].timestamp(), r.get("article_id") or r.get("id") or "",
        ),
    )[0]  # fmt: skip


# ── lookup ──────────────────────────────────────────────────────────────────────────────


def resolve_story(conn, public_id: str) -> dict:
    """{"kind": "found", storyId, publicId} | {"kind": "merged", target} | {"kind": "not_found"}."""
    if not _UUID.match(public_id):
        return {"kind": "not_found"}
    s = conn.execute("SELECT id, merged_into FROM stories WHERE public_id = %s", (public_id,)).fetchone()
    target_id = s["merged_into"] if s else None
    if not s:
        alias = conn.execute("SELECT story_id FROM story_aliases WHERE public_id = %s", (public_id,)).fetchone()
        if not alias:
            return {"kind": "not_found"}
        target_id = alias["story_id"]
    if target_id:
        for _ in range(10):  # follow merge chains to the surviving story
            t = conn.execute("SELECT id, public_id::text AS public_id, merged_into FROM stories WHERE id = %s", (target_id,)).fetchone()
            if not t:
                return {"kind": "not_found"}
            if not t["merged_into"]:
                return {"kind": "merged", "target": t["public_id"]}
            target_id = t["merged_into"]
        return {"kind": "not_found"}
    return {"kind": "found", "storyId": s["id"], "publicId": public_id}


# ── the latest hot ranking ──────────────────────────────────────────────────────────────


def stored_hot_ranking(conn) -> dict | None:
    """Administrative invalidation needs the saved references, including ones already hidden publicly."""
    row = conn.execute(
        "SELECT id, computed_at, rule_version, entries, evidence FROM hot_rankings WHERE published ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "computedAt": iso(row["computed_at"]),
        "ruleVersion": row["rule_version"],
        "entries": row["entries"],
        "coverage": row["evidence"],
    }


def latest_hot_ranking(conn) -> dict | None:
    """A stored entry is shown only while its representative is still listed evidence of the story (regrouping or a withdrawal can undo it
    before the next ranking)."""
    row = stored_hot_ranking(conn)
    if not row:
        return None
    now = datetime.now(UTC)
    entries = row["entries"]
    current = []
    if entries:
        current = conn.execute(
            f"""SELECT DISTINCT ON (f.story_id, p.article_id) f.story_id, p.article_id, st.title AS story_title, p.url, s.name AS source_name, latest.at AS latest_at
                FROM facts f JOIN stories st ON st.id = f.story_id JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                JOIN sources s ON s.id = p.source_id
                JOIN LATERAL (
                  SELECT max(coalesce(p.published_at, p.discovered_at)) AS at
                  FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                  WHERE f.story_id = st.id AND {evidence_condition()} AND {listed_condition()}
                ) latest ON true
                WHERE f.story_id = ANY(%(story_ids)s) AND p.article_id = ANY(%(reps)s) AND {evidence_condition()} AND {listed_condition()}
                ORDER BY f.story_id, p.article_id, (fa.role = 'primary') DESC, f.id""",
            {**scope_params(now), "story_ids": [e["storyId"] for e in entries], "reps": [e.get("representativeItemId") or "" for e in entries]},
        ).fetchall()
    valid = {(r["story_id"], r["article_id"]): r for r in current}
    visible = []
    for entry in entries:
        report = valid.get((entry["storyId"], entry.get("representativeItemId")))
        # Regrouping can invalidate a saved representative before the next heat refresh: do not link the event to a report it no longer holds.
        if report:
            visible.append({**entry, "title": report["story_title"], "latestAt": iso(report["latest_at"]), "representativeUrl": report["url"],
                            "representativeSource": report["source_name"]})  # fmt: skip
    return {**row, "entries": visible}


# ── story evidence and text ─────────────────────────────────────────────────────────────


def digest_fact_evidence(r: dict) -> dict:
    """Conditions stay next to their object and source quote; no ownership is inferred from subject tags."""
    fact = r["structured_fact"] if isinstance(r.get("structured_fact"), dict) else {}
    conditions = [
        {"text": c["text"], "quote": c["quote"]}
        for c in (fact.get("conditions") if isinstance(fact.get("conditions"), list) else [])
        if isinstance(c, dict) and isinstance(c.get("text"), str) and isinstance(c.get("quote"), str)
    ]
    return {"subject": r["fact_subject"], "action": r["fact_action"], "object": r["fact_object"], "conditions": r["fact_conditions"], "evidence": r["evidence"],
            "extractedConditions": conditions, "extractedEvidence": fact.get("evidence") if isinstance(fact.get("evidence"), str) else None}  # fmt: skip


def digest_reports(conn, story_ids: list[int], now: datetime) -> list[dict]:
    if not story_ids:
        return []
    return conn.execute(
        f"""SELECT DISTINCT ON (f.story_id, p.article_id) f.story_id, p.article_id AS id, p.title, p.summary, s.name AS source_name, (s.tier = 'T1') AS first_party,
              coalesce(p.published_at, p.discovered_at) AS at, f.id AS fact_id, f.subject AS fact_subject, f.action AS fact_action, f.object AS fact_object,
              f.conditions AS fact_conditions, fa.evidence, an.output->'fact' AS structured_fact
            FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
            JOIN sources s ON s.id = p.source_id LEFT JOIN analyses an ON an.id = p.analysis_id
            WHERE f.story_id = ANY(%(ids)s) AND {evidence_condition()} AND {listed_condition()}
            ORDER BY f.story_id, p.article_id, (fa.role = 'primary') DESC, f.id""",
        {**scope_params(now), "ids": story_ids},
    ).fetchall()


def digest_inputs_hash(reports: list[dict]) -> str:
    rows = sorted(reports, key=lambda r: r["id"])
    return sha256(
        stable_json(
            [
                [r["id"], r["fact_id"], r["title"], r["summary"] or "", r["source_name"], r["first_party"], iso(r["at"]), digest_fact_evidence(r)]
                for r in rows
            ]
        )
    )


def story_texts(conn, story_ids: list[int], now: datetime) -> dict[int, dict]:
    """What the site tells readers about an event, by the rule the digest is written by: its listed evidence."""
    if not story_ids:
        return {}
    rows = conn.execute(
        f"""SELECT st.id, st.digest, st.digest_updated_at, saved.article_ids, saved.inputs_hash,
              CASE WHEN EXISTS (
                SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                WHERE f.story_id = st.id AND p.summary = st.summary AND {evidence_condition()} AND {listed_condition()}
              ) THEN st.summary END AS summary,
              coalesce(st.digest = saved.digest, false) AND NOT EXISTS (
                SELECT 1 FROM unnest(saved.article_ids) AS input(article_id)
                WHERE NOT EXISTS (SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                  WHERE f.story_id = st.id AND p.article_id = input.article_id AND {evidence_condition()} AND {listed_condition()})
              ) AS current,
              latest.id AS latest_id, latest.title AS latest_title, latest.at AS latest_at
            FROM stories st LEFT JOIN LATERAL (
              SELECT article_ids, inputs_hash, digest FROM story_digests WHERE story_id = st.id ORDER BY version DESC LIMIT 1
            ) saved ON true LEFT JOIN LATERAL (
              SELECT p.article_id AS id, p.title, coalesce(p.published_at, p.discovered_at) AS at
              FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
              WHERE f.story_id = st.id AND {evidence_condition()} AND {listed_condition()}
              ORDER BY coalesce(p.published_at, p.discovered_at) DESC, p.article_id LIMIT 1
            ) latest ON true
            WHERE st.id = ANY(%(ids)s)""",
        {**scope_params(now), "ids": story_ids},
    ).fetchall()
    reports = digest_reports(conn, [r["id"] for r in rows if r["current"] and r["digest"] and r["inputs_hash"]], now)
    out: dict[int, dict] = {}
    for r in rows:
        saved = set(r["article_ids"] or [])
        # New reports may extend an event without changing the evidence behind its saved digest.
        current = bool(r["current"] and saved and r["inputs_hash"]
                       and digest_inputs_hash([p for p in reports if p["story_id"] == r["id"] and p["id"] in saved]) == r["inputs_hash"])  # fmt: skip
        out[r["id"]] = {
            "digest": r["digest"] if current else None,
            "digestUpdatedAt": r["digest_updated_at"] if current else None,
            "summary": r["summary"],
            "latest": {"id": r["latest_id"], "title": r["latest_title"], "at": r["latest_at"]} if r["latest_id"] else None,
        }
    return out


# ── related stories ─────────────────────────────────────────────────────────────────────


def _related_evidence(story: str, other: str) -> str:
    """One row per known bridge report, with its current validity. Missing history proves no revocation."""
    return f"""(
      WITH judged AS (
        SELECT DISTINCT d.id, d.article_id, d.fact_id FROM facts own
        JOIN grouping_decisions d ON d.fact_id = own.id
        CROSS JOIN LATERAL jsonb_array_elements(d.candidates) c
        JOIN facts candidate ON candidate.id = (c->>'id')::bigint
        WHERE own.story_id IN ({story}, {other})
          AND d.verdict IN ('same-fact', 'same-url', 'new-fact-in-story', 'new-story')
          AND c->>'relation' = ANY(%(tie_relations)s) AND (c->>'confidence')::numeric >= %(tie_min)s
          AND ((own.story_id = {story} AND candidate.story_id = {other}) OR (own.story_id = {other} AND candidate.story_id = {story}))),
      latest AS (
        SELECT DISTINCT ON (d.article_id) d.id, d.article_id, d.verdict, d.candidates FROM grouping_decisions d
        WHERE d.verdict <> 'kept' AND d.article_id IN (SELECT article_id FROM judged) ORDER BY d.article_id, d.id DESC)
      SELECT j.article_id, bool_or((j.id = latest.id
        AND {story_report_condition()} AND NOT {latest_composite("j.article_id")}
        AND EXISTS (SELECT 1 FROM fact_articles fa WHERE fa.article_id = j.article_id
          AND fa.fact_id = j.fact_id AND fa.role IN ('primary', 'report'))) IS TRUE) AS valid
      FROM judged j JOIN latest ON latest.article_id = j.article_id
      LEFT JOIN publications p ON p.article_id = j.article_id LEFT JOIN sources s ON s.id = p.source_id
      GROUP BY j.article_id
    )"""


def latest_composite(article: str) -> str:
    """The composite rule over the latest analysis of report `article` (missing scope is unknown, never composite)."""
    return f"""(SELECT scope_analysis.output->>'scope' = 'composite' FROM analyses scope_analysis
      WHERE scope_analysis.article_id = {article} ORDER BY scope_analysis.input_revision DESC, scope_analysis.id DESC LIMIT 1) IS TRUE"""


def related_stories(conn, story_id: int, now: datetime) -> list[dict]:
    return conn.execute(
        f"""SELECT st.public_id::text AS public_id, st.title, l.relation FROM story_links l JOIN stories st ON st.id = l.other_id
            LEFT JOIN LATERAL (
              SELECT count(*) FILTER (WHERE valid) AS reports, bool_and(valid) AS unchanged
              FROM {_related_evidence("l.story_id", "l.other_id")} evidence
            ) support ON l.relation = 'related'
            WHERE l.story_id = %(story)s AND st.merged_into IS NULL
              AND (l.relation <> 'related' OR support.unchanged IS NOT FALSE OR support.reports >= %(min_reports)s)
              AND EXISTS (
              SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
              JOIN sources s ON s.id = p.source_id
              WHERE f.story_id = st.id AND {evidence_condition()} AND {story_report_condition()}
            ) ORDER BY st.latest_at DESC NULLS LAST, st.id DESC LIMIT 8""",
        {**scope_params(now), "story": story_id, "min_reports": RELATED_MIN_REPORTS, "tie_relations": TIE_RELATIONS, "tie_min": TIE_MIN_CONFIDENCE},
    ).fetchall()


# ── a story's reports ───────────────────────────────────────────────────────────────────


def story_reports(conn, story_id: int, now: datetime) -> list[dict]:
    """Every report linked to the story's facts that has a public page (editorial source, summarised or not), mentions included. hot_signal
    material only adds heat and is not listed."""
    return conn.execute(
        f"""SELECT DISTINCT ON (p.article_id) p.article_id AS id, p.title, p.summary, p.url, p.selected, coalesce(p.published_at, p.discovered_at) AS at,
              s.id AS source_id, s.name AS source_name, (s.tier = 'T1') AS first_party, f.id AS fact_id,
              CASE WHEN {composite_condition()} THEN 'mention' ELSE fa.role END AS role, p.body_mode, p.score, p.timeline_at, {REPRESENTATIVE_COLUMNS}
            FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
            JOIN sources s ON s.id = p.source_id
            WHERE f.story_id = %(story)s AND {story_report_condition()}
            ORDER BY p.article_id, (fa.role = 'primary') DESC, (fa.role <> 'mention') DESC, f.id""",
        {**scope_params(now), "story": story_id},
    ).fetchall()


def story_content(conn, story_id: int, now: datetime) -> dict | None:
    """The story's reports newest first, its developments, and its latest and first non-mention reports."""
    s = conn.execute("SELECT public_id::text AS public_id, title FROM stories WHERE id = %s", (story_id,)).fetchone()
    if not s:
        return None
    reports = story_reports(conn, story_id, now)
    if not reports:
        return None
    reports.sort(key=lambda r: (-r["at"].timestamp(), r["id"]))
    primary = [r for r in reports if r["role"] != "mention"]
    by_fact: dict[int, list[dict]] = {}
    for r in primary:
        by_fact.setdefault(r["fact_id"], []).append(r)
    facts = conn.execute("SELECT id, public_id, title FROM facts WHERE story_id = %s", (story_id,)).fetchall()
    developments = []
    for f in facts:
        if f["id"] not in by_fact:
            continue
        members = by_fact[f["id"]]
        selected = [r for r in members if r["selected"]]
        developments.append({"factId": f["public_id"], "title": f["title"], "firstReportAt": iso(min(r["at"] for r in members)), "reportCount": len(members),
                             "representative": pick_representative(selected or members)})  # fmt: skip
    developments.sort(key=lambda d: d["firstReportAt"], reverse=True)
    if not primary:
        return None
    return {
        "s": s,
        "reports": reports,
        "primaryReports": primary,
        "developments": developments,
        "latestReport": primary[0],
        "firstReportAt": primary[-1]["at"],
    }


def story_status_for(latest_at: datetime | None, now: datetime) -> str:
    if not latest_at:
        return "settled"
    age = (now - latest_at).total_seconds()
    return "active" if age < 24 * 3600 else "watching" if age < 72 * 3600 else "settled"


# ── v1 shapes (ranks and counts only; no heat values) ───────────────────────────────────


def v1_hot_topics(conn) -> dict:
    ranking = latest_hot_ranking(conn)
    items = []
    for e in ranking["entries"] if ranking else []:
        links = {
            "aihot": item_url(e["representativeItemId"]) if e.get("representativeItemId") else story_url(e["storyPublicId"]),
            "original": e.get("representativeUrl") or story_url(e["storyPublicId"]),
            "story": story_url(e["storyPublicId"]),
        }
        items.append({
            "rank": e["rank"], "id": e.get("representativeItemId") or e["storyPublicId"], "title": e["title"],
            "source": {"name": e.get("representativeSource") or (e["sourceNames"][0] if e["sourceNames"] else None) or pack.site()["SITE"]["name"]},
            "links": links, "sourceCount": e["sourceCount"], "signalCount": e["signalCount"], "participantCount": e["participantCount"],
            "sourceNames": e["sourceNames"], "latestAt": iso(datetime.fromisoformat(e["latestAt"].replace("Z", "+00:00"))),
        })  # fmt: skip
    return {"schemaVersion": 1, "count": len(items), "items": items}


def v1_story(conn, story_id: int) -> dict | None:
    now = datetime.now(UTC)
    content = story_content(conn, story_id, now)
    if not content:
        return None
    s, reports, latest_report, first_report_at = content["s"], content["reports"], content["latestReport"], content["firstReportAt"]
    # The latest development and digest follow the same evidence as the website.
    text = story_texts(conn, [story_id], now)[story_id]
    latest = text["latest"] or latest_report
    latest_at = latest["at"]
    neighbors = [
        {"publicId": r["public_id"], "title": r["title"], "relation": r["relation"], "links": {"aihot": story_url(r["public_id"]), "api": v1_story_api_url(r["public_id"])}}
        for r in related_stories(conn, story_id, now)
    ]  # fmt: skip
    status = story_status_for(latest_at, now)
    return {
        "schemaVersion": 1,
        "story": {
            "publicId": s["public_id"], "title": s["title"], "status": "settled" if status == "settled" else "active",
            "sourceCount": len({r["source_id"] for r in reports}), "reportCount": len(reports),
            "firstReportAt": iso(first_report_at), "latestAt": iso(latest_at), "latest": latest["title"],
            "digest": text["digest"], "digestUpdatedAt": iso(text["digestUpdatedAt"]) if text["digestUpdatedAt"] else None,
            "links": {"aihot": story_url(s["public_id"])},
            "reports": [
                {"id": r["id"], "title": r["title"], "summary": r["summary"], "source": {"name": r["source_name"], "firstParty": r["first_party"]},
                 "publishedAt": iso(r["at"]), "links": {"aihot": item_url(r["id"]), "original": r["url"]}}
                for r in reports[:50]
            ],  # fmt: skip
            "storyline": [n for n in neighbors if n["relation"] == "storyline"],
            "related": [n for n in neighbors if n["relation"] == "related"],
        },
    }  # fmt: skip
