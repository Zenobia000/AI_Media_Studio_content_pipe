"""Hot ranking: attention over the last 48 hours from independent participants. Each participant counts once per window (repeat collection does
not add heat), decays with a 24-hour half-life, and the source time (not collection time) places evidence in the window. Port of events/hot.ts
(the read side, heat_series and the evidence query are in publication/site_story.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from newsroom.engine.publication.scope import evidence_condition, listed_condition, scope_params
from newsroom.engine.publication.site_lists import tier_rank
from newsroom.engine.publication.site_story import (
    HALF_LIFE_HOURS,
    HEAT_WINDOW_HOURS,
    behind_sources,
    current_signals,
    heat_index,
    signal_params,
    source_clocks,
)
from newsroom.engine.publication.stories import REPRESENTATIVE_COLUMNS, pick_representative
from newsroom.engine.publication.v1 import iso

HOT_RULE_VERSION = "heat-v1-48h-halflife24h"
MIN_PARTICIPANTS = 2
HOUR_MS = 3600 * 1000


def _number(value):
    """A JSON number as JavaScript writes it: 5, not 5.0."""
    if isinstance(value, (float, Decimal)):
        f = float(value)
        return int(f) if f.is_integer() else f
    if isinstance(value, dict):
        return {k: _number(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_number(v) for v in value]
    return value


def js_round(n: float) -> int:
    import math  # noqa: PLC0415

    return math.floor(n + 0.5)


def heat_rows(conn, at: datetime, behind: list[str] | None = None, story_ids: list[int] | None = None) -> list[dict]:
    """Heat of every story at `at` from the current evidence; `behind` marks sources not fully observed. The change compares with six hours
    before over that time's own 48-hour window, and only over participants observed throughout."""
    prev = at - timedelta(hours=6)
    now_from = f"%(at)s::timestamptz - make_interval(hours => {HEAT_WINDOW_HOURS})"
    prev_from = f"%(prev)s::timestamptz - make_interval(hours => {HEAT_WINDOW_HOURS})"
    decay_now = f"power(0.5, extract(epoch FROM (%(at)s::timestamptz - last_at)) / 3600.0 / {HALF_LIFE_HOURS})"
    decay_prev = f"power(0.5, extract(epoch FROM (%(prev)s::timestamptz - last_prev)) / 3600.0 / {HALF_LIFE_HOURS})"
    comparable = "(NOT behind AND NOT late)"
    only = "AND story_id = ANY(%(story_ids)s::bigint[])" if story_ids else ""
    return conn.execute(
        f"""WITH obs AS (
              SELECT story_id, participant_key,
                     max(observed_at) FILTER (WHERE observed_at > {now_from}) AS last_at,
                     min(observed_at) FILTER (WHERE observed_at > {now_from}) AS first_at,
                     coalesce(bool_or(kind = 'editorial') FILTER (WHERE observed_at > {now_from}), false) AS editorial,
                     max(observed_at) FILTER (WHERE observed_at <= %(prev)s) AS last_prev,
                     bool_or(source_id = ANY(%(behind)s::text[])) AS behind,
                     bool_or(source_since > {prev_from}) AS late
              FROM {current_signals()} cs
              WHERE observed_at > {prev_from} AND observed_at <= %(at)s {only}
              GROUP BY story_id, participant_key
            ), agg AS (
              SELECT story_id,
                count(*) FILTER (WHERE last_at IS NOT NULL) AS participants,
                coalesce(sum({decay_now}) FILTER (WHERE last_at IS NOT NULL), 0) AS heat,
                coalesce(sum({decay_prev}) FILTER (WHERE last_prev IS NOT NULL), 0) AS heat_prev,
                coalesce(sum({decay_now}) FILTER (WHERE last_at IS NOT NULL AND {comparable}), 0) AS heat_obs,
                coalesce(sum({decay_prev}) FILTER (WHERE last_prev IS NOT NULL AND {comparable}), 0) AS heat_prev_obs,
                count(*) FILTER (WHERE last_at IS NOT NULL AND behind) AS behind_participants,
                count(*) FILTER (WHERE (last_at IS NOT NULL OR last_prev IS NOT NULL) AND NOT {comparable}) AS uncomparable,
                count(*) FILTER (WHERE first_at > %(prev)s) AS recent6h,
                count(*) FILTER (WHERE last_at IS NOT NULL AND editorial) AS editorial_participants,
                count(*) FILTER (WHERE last_at IS NOT NULL AND NOT editorial) AS signal_participants
              FROM obs GROUP BY story_id
              HAVING count(*) FILTER (WHERE last_at IS NOT NULL) > 0
            )
            SELECT a.story_id, st.public_id::text AS public_id, st.title, st.first_report_at, st.latest_at,
                   a.participants, a.heat, a.heat_prev, a.heat_obs, a.heat_prev_obs, a.behind_participants, a.uncomparable, a.recent6h,
                   a.editorial_participants, a.signal_participants
            FROM agg a JOIN stories st ON st.id = a.story_id
            WHERE st.merged_into IS NULL""",
        {**signal_params(), "at": at, "prev": prev, "behind": behind or [], "story_ids": story_ids},
    ).fetchall()


def compute_hot_ranking(pool, at: datetime | None = None) -> dict:
    at = at or datetime.now(UTC)
    with pool.connection() as conn:
        behind = behind_sources(source_clocks(conn), at.timestamp() * 1000, True)
        rows = [r for r in heat_rows(conn, at, behind) if int(r["participants"]) >= MIN_PARTICIPANTS and int(r["editorial_participants"]) >= 1]
        rows.sort(key=lambda r: (-float(r["heat"]), -(r["latest_at"].timestamp() if r["latest_at"] else 0)))
        entries: list[dict] = []
        for r in rows:
            if len(entries) >= 10:
                break
            reports = conn.execute(
                f"""SELECT DISTINCT ON (p.article_id) p.article_id AS id, p.url, p.title, s.id AS source_id, s.name AS source_name, p.selected, p.score,
                      p.timeline_at, p.body_mode, f.id AS fact_id, {REPRESENTATIVE_COLUMNS}, coalesce(p.published_at, p.discovered_at) AS at
                    FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                    JOIN sources s ON s.id = p.source_id
                    WHERE f.story_id = %(story)s AND {evidence_condition()} AND {listed_condition()}
                    ORDER BY p.article_id, (fa.role = 'primary') DESC, f.id""",
                {**scope_params(at), "story": r["story_id"]},
            ).fetchall()
            if not reports:
                continue
            # The event is shown under its own title, linked to the fact most sources report: the launch, not the leak or teaser that came
            # first. Between equally reported facts the earlier one stands.
            by_fact: dict[int, list[dict]] = {}
            for report in reports:
                by_fact.setdefault(report["fact_id"], []).append(report)
            members = sorted(
                by_fact.values(),
                key=lambda lst: (-len({x["source_id"] for x in lst}), min(x["timeline_at"].timestamp() for x in lst), lst[0]["fact_id"]),
            )[0]
            selected = [x for x in members if x["selected"]]
            rep = pick_representative(selected or members)
            participants = conn.execute(
                f"""SELECT DISTINCT ON (cs.participant_key) s.name, cs.kind, s.tier, cs.observed_at AS at
                    FROM {current_signals()} cs JOIN sources s ON s.id = cs.source_id
                    WHERE cs.story_id = %(story)s AND cs.observed_at > %(at)s::timestamptz - make_interval(hours => {HEAT_WINDOW_HOURS}) AND cs.observed_at <= %(at)s
                    ORDER BY cs.participant_key, (cs.kind = 'editorial') DESC, cs.observed_at DESC""",
                {**signal_params(), "story": r["story_id"], "at": at},
            ).fetchall()
            # The reporting sources of the window, latest first (signal participants are counted separately).
            reporting = sorted((p for p in participants if p["kind"] == "editorial"), key=lambda p: -p["at"].timestamp())
            heat = heat_index(float(r["heat"]))
            # The change against six hours before compares only participants whose sources were observed throughout.
            prev_all = heat_index(float(r["heat_prev"]))
            cur, prev = (heat_index(float(r["heat_obs"])), heat_index(float(r["heat_prev_obs"]))) if int(r["uncomparable"]) > 0 else (heat, prev_all)
            pct = (cur - prev) / prev if prev > 0 else None
            first_at = r["first_report_at"] or reports[0]["at"]
            is_new = at.timestamp() * 1000 - first_at.timestamp() * 1000 < 6 * HOUR_MS
            recent = int(r["recent6h"])
            surge = recent >= 3 and recent / int(r["participants"]) >= 0.5
            badges = (["surge"] if surge else []) + (["new"] if is_new else []) + (["rising"] if not surge and pct is not None and pct > 0.15 else [])
            trend = "new" if prev_all <= 0 else "unknown" if pct is None else "up" if pct > 0.1 else "down" if pct < -0.1 else "flat"
            ordered = sorted(
                participants,
                key=lambda p: (0 if p["kind"] == "editorial" else 1, tier_rank(p["tier"]), -p["at"].timestamp()),
            )
            entries.append({
                "rank": len(entries) + 1, "storyId": r["story_id"], "storyPublicId": r["public_id"], "title": r["title"], "heat": heat, "trend": trend,
                "trendPct": None if pct is None else js_round(pct * 1000) / 10, "badges": badges, "participantCount": int(r["participants"]),
                "sourceCount": int(r["editorial_participants"]), "signalCount": int(r["signal_participants"]), "reportCount": len(reports),
                "sourceNames": list(dict.fromkeys(x["name"] for x in reporting))[:8], "latestAt": iso(r["latest_at"] or at), "firstReportAt": iso(first_at),
                "representativeItemId": rep["id"], "representativeUrl": rep["url"], "representativeSource": rep["source_name"],
                "participants": [{"name": p["name"], "kind": p["kind"], "tier": p["tier"]} for p in ordered[:40]],
            })  # fmt: skip
        evidence = {"windowHours": HEAT_WINDOW_HOURS, "halfLifeHours": HALF_LIFE_HOURS, "minParticipants": MIN_PARTICIPANTS, "candidates": len(rows)}
        saved = conn.execute(
            "INSERT INTO hot_rankings (computed_at, rule_version, entries, evidence, published) VALUES (%s, %s, %s, %s, true) RETURNING id",
            (at, HOT_RULE_VERSION, Jsonb(_number(entries)), Jsonb(evidence)),
        ).fetchone()
        # Keep a bounded history of rankings.
        conn.execute("DELETE FROM hot_rankings WHERE computed_at < now() - interval '30 days'")
    return {"id": saved["id"], "entries": len(entries)}


def _save_heat(conn, rows: list[dict], hour: datetime) -> int:
    """One bounded batch per hour, shared by live snapshots and historical repair; all derived fields are replaced together."""
    for i in range(0, len(rows), 1000):
        values = [
            (
                r["story_id"],
                hour,
                heat_index(float(r["heat"])),
                int(r["participants"]),
                int(r["editorial_participants"]),
                int(r["behind_participants"]) == 0,
            )
            for r in rows[i : i + 1000]
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO story_heat_hourly (story_id, hour, heat, participants, cohort, complete) VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (story_id, hour) DO UPDATE SET heat = EXCLUDED.heat, participants = EXCLUDED.participants,
                     cohort = EXCLUDED.cohort, complete = EXCLUDED.complete""",
                values,
            )
    return len(rows)


def snapshot_heat(pool, at: datetime | None = None) -> dict:
    """Hourly heat snapshot for active stories (event page trend). An hour taken while a participant's source was behind is marked
    incomplete (the charts leave it out) and recomputed once the sources have fetched past it."""
    at = at or datetime.now(UTC)
    hour = datetime.fromtimestamp(int(at.timestamp() * 1000 // HOUR_MS * HOUR_MS) / 1000, UTC)
    with pool.connection() as conn:
        clocks = source_clocks(conn)
        rows = heat_rows(conn, hour, behind_sources(clocks, hour.timestamp() * 1000, True))
        _save_heat(conn, rows, hour)
        repaired = 0
        stale = conn.execute(
            """SELECT hour, array_agg(story_id) AS stories FROM story_heat_hourly
               WHERE NOT complete AND hour < %s AND hour > %s GROUP BY hour ORDER BY hour""",
            (hour, hour - timedelta(hours=HEAT_WINDOW_HOURS)),
        ).fetchall()
        for s in stale:
            again = heat_rows(conn, s["hour"], behind_sources(clocks, s["hour"].timestamp() * 1000, False), [int(x) for x in s["stories"]])
            repaired += _save_heat(conn, [r for r in again if int(r["behind_participants"]) == 0], s["hour"])
    return {"stories": len(rows), "repaired": repaired}


def stored_hot_ranking(conn) -> dict | None:
    """Administrative invalidation needs the saved references, including ones already hidden publicly."""
    row = conn.execute(
        "SELECT id, computed_at, rule_version, entries, evidence FROM hot_rankings WHERE published ORDER BY computed_at DESC LIMIT 1"
    ).fetchone()
    return (
        None
        if not row
        else {
            "id": row["id"],
            "computedAt": row["computed_at"],
            "ruleVersion": row["rule_version"],
            "entries": row["entries"],
            "coverage": row["evidence"],
        }
    )
