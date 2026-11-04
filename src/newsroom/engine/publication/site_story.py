"""The website's event pages and hot list: ports of events/hot.ts (the read side: current signals, heat series, source clocks), the site half of
publication/stories.ts (loadStoryDetail, loadHot), publication/followups.ts and publication/groups.ts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from newsroom.engine import pack
from newsroom.engine.media.imgproxy import proxied_image, proxied_image_set
from newsroom.engine.publication.items import filter_sql
from newsroom.engine.publication.scope import (
    evidence_condition,
    listed_condition,
    own_fact_evidence_condition,
    scope_params,
    selected_condition,
)  # fmt: skip
from newsroom.engine.publication.site_lists import _hot_extras
from newsroom.engine.publication.stories import (
    REPRESENTATIVE_COLUMNS,
    latest_hot_ranking,
    pick_representative,
    public_source_name,
    related_stories,
    story_content,
    story_status_for,
    story_texts,
)  # fmt: skip
from newsroom.engine.publication.topics import topics_of_story
from newsroom.engine.publication.v1 import iso, js_round

HEAT_WINDOW_HOURS = 48
HALF_LIFE_HOURS = 24
SPARK_HOURS = 24
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def heat_index(heat: float) -> float:
    return js_round(heat * 100) / 10  # one decimal on the 10× scale shown to readers


def source_clocks(conn) -> list[dict]:
    """Scheduled sources and their last successful fetch (screenshot-reported and pushed sources cannot visibly fall behind)."""
    rows = conn.execute(
        "SELECT id, last_ok_at, interval_minutes FROM sources WHERE enabled AND kind IN ('rss', 'web_list', 'json_list', 'x_search')"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "lastOk": r["last_ok_at"].timestamp() * 1000 if r["last_ok_at"] else None,
            "graceMs": max(r["interval_minutes"] * 3, 90) * 60_000,
        }
        for r in rows
    ]


def behind_sources(clocks: list[dict], at_ms: float, grace: bool) -> list[str]:
    """Sources whose evidence up to `at` may be incomplete: behind after three intervals (at least 90 minutes) without a successful fetch."""
    return [c["id"] for c in clocks if c["lastOk"] is None or c["lastOk"] < at_ms - (c["graceMs"] if grace else 0)]


def current_signals() -> str:
    """The heat evidence as it stands now, for every reader of story_signals (parameters %(dev_feeds)s and %(hn_feeds)s)."""
    return f"""(
  SELECT ss.story_id, ss.article_id, s.id AS source_id, ss.observed_at, s.created_at AS source_since,
    CASE WHEN s.participation_mode = 'editorial' THEN 'editorial' ELSE 'signal' END AS kind,
    CASE
      WHEN s.id = ANY(%(dev_feeds)s::text[]) THEN coalesce('dev:account:' || lower(substring(a.url from '^https://dev\\.to/([A-Za-z0-9_-]{{1,64}})/[^/?#]+/?$')), 'unresolved:' || s.id)
      WHEN s.id = ANY(%(hn_feeds)s::text[]) THEN coalesce('hn:account:' || substring(a.author from '^[A-Za-z0-9_-]{{1,64}}$'), 'unresolved:' || s.id)
      WHEN s.signal_group_id IS NOT NULL THEN 'group:' || s.signal_group_id
      WHEN s.owner_entity_id IS NOT NULL THEN 'owner:' || s.owner_entity_id
      ELSE 'source:' || s.id
    END AS participant_key
  FROM story_signals ss JOIN articles a ON a.id = ss.article_id JOIN sources s ON s.id = a.source_id
  LEFT JOIN publications p ON p.article_id = ss.article_id
  WHERE s.participation_mode <> 'isolated' AND coalesce(p.visibility, 'public') <> 'withdrawn'
    AND NOT EXISTS (SELECT 1 FROM grouping_overrides go WHERE go.article_id = ss.article_id AND go.mode = 'standalone')
    AND (s.participation_mode <> 'editorial' OR EXISTS (SELECT 1 FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id
      WHERE fa.article_id = ss.article_id AND f.story_id = ss.story_id AND {evidence_condition()}))
)"""


def signal_params() -> dict:
    feeds = pack.site()["COMMUNITY_FEEDS"]
    return {"dev_feeds": feeds["dev"], "hn_feeds": feeds["hn"]}


def heat_series(conn, story_id: int, now: datetime, days: int = 7) -> list[dict]:
    """A story's heat hour by hour for its page chart: the hours observed in full over the last `days`, each computed from the current evidence
    of one group of participants, those whose sources were all collecting before the first plotted hour's window began."""
    hours = [
        h["hour"].timestamp() * 1000
        for h in conn.execute(
            "SELECT hour FROM story_heat_hourly WHERE story_id = %s AND complete AND hour > %s AND hour <= %s ORDER BY hour",
            (story_id, now - timedelta(days=days), now),
        ).fetchall()
    ]
    if not hours:
        return []
    since = hours[0] - HEAT_WINDOW_HOURS * 3600_000
    rows = conn.execute(
        f"""SELECT participant_key, observed_at, source_since FROM {current_signals()} cs
            WHERE story_id = %(story)s AND observed_at > %(since)s AND observed_at <= %(now)s""",
        {**scope_params(now), **signal_params(), "story": story_id, "since": datetime.fromtimestamp(since / 1000, UTC), "now": now},
    ).fetchall()
    late = {r["participant_key"] for r in rows if r["source_since"].timestamp() * 1000 > since}
    times: dict[str, list[float]] = {}
    for r in rows:
        if r["participant_key"] not in late:
            times.setdefault(r["participant_key"], []).append(r["observed_at"].timestamp() * 1000)
    series = []
    for hour in hours:
        heat = 0.0
        participants = 0
        for lst in times.values():
            last = float("-inf")
            for t in lst:
                if t <= hour and t > hour - HEAT_WINDOW_HOURS * 3600_000 and t > last:
                    last = t
            if last == float("-inf"):
                continue
            heat += 0.5 ** ((hour - last) / 3600_000 / HALF_LIFE_HOURS)
            participants += 1
        series.append({"hour": datetime.fromtimestamp(hour / 1000, UTC), "heat": heat_index(heat), "participants": participants})
    return series if any(p["heat"] > 0 for p in series) else []


def _report_view(r: dict) -> dict:
    return {"id": r["id"], "title": r["title"], "summary": r["summary"], "source": {"name": public_source_name(r["source_name"]), "firstParty": r["first_party"]},
            "publishedAt": iso(r["at"]), "selected": r["selected"]}  # fmt: skip


def load_story_detail(pool, story_id: int, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        content = story_content(conn, story_id, now)
        if not content:
            return None
        s, reports, developments, latest_report, first_report_at = (
            content["s"],
            content["reports"],
            content["developments"],
            content["latestReport"],
            content["firstReportAt"],
        )
        why = conn.execute(
            f"""WITH cs AS (SELECT * FROM {current_signals()} c WHERE story_id = %(story)s AND observed_at <= %(now)s)
                SELECT count(DISTINCT participant_key) FILTER (WHERE observed_at > %(now)s::timestamptz - interval '48 hours') AS p48,
                       count(DISTINCT participant_key) FILTER (WHERE observed_at > %(now)s::timestamptz - interval '6 hours'
                         AND participant_key NOT IN (SELECT participant_key FROM cs x WHERE x.observed_at <= %(now)s::timestamptz - interval '6 hours')) AS p6,
                       count(*) FILTER (WHERE kind = 'editorial' AND observed_at > %(now)s::timestamptz - interval '24 hours') AS r24
                FROM cs""",
            {**scope_params(now), **signal_params(), "story": story_id, "now": now},
        ).fetchone()
        ranking = latest_hot_ranking(conn)
        entry = next((e for e in (ranking["entries"] if ranking else []) if e["storyId"] == story_id), None)
        # Only hours observed in full are drawn (the chart leaves a gap otherwise), over one comparable group.
        heat = heat_series(conn, story_id, now)
        # Complete when none of the sources behind the last 48 hours' participants is behind on collection.
        behind = behind_sources(source_clocks(conn), now.timestamp() * 1000, True)
        partial = 0
        if behind:
            partial = conn.execute(
                f"""SELECT count(*)::int AS n FROM {current_signals()} cs WHERE story_id = %(story)s AND source_id = ANY(%(behind)s::text[])
                    AND observed_at > %(now)s::timestamptz - interval '48 hours' AND observed_at <= %(now)s""",
                {**scope_params(now), **signal_params(), "story": story_id, "behind": behind, "now": now},
            ).fetchone()["n"]
        related = related_stories(conn, story_id, now)
        topics = topics_of_story(conn, story_id, now)
        text = story_texts(conn, [story_id], now)[story_id]
    # Historical stories without listed evidence retain their latest readable report.
    latest = text["latest"] or latest_report
    latest_at = latest["at"]
    # Without a digest or a summary of its own, the story opens with its first development's representative report.
    origin = developments[-1]["representative"] if developments else None
    return {
        "publicId": s["public_id"], "title": s["title"], "status": story_status_for(latest_at, now), "reportCount": len(reports),
        "sourceCount": len({r["source_id"] for r in reports}), "firstReportAt": iso(first_report_at), "latestAt": iso(latest_at),
        "digest": text["digest"], "digestUpdatedAt": iso(text["digestUpdatedAt"]) if text["digestUpdatedAt"] else None, "summary": text["summary"],
        "excerpt": {"text": origin["summary"], "sourceName": public_source_name(origin["source_name"])} if not text["digest"] and not text["summary"] and origin and origin.get("summary") else None,
        "latest": latest["title"], "latestReport": {"id": latest["id"]},
        "whyHot": {"participants48h": int(why["p48"] or 0), "newParticipants6h": int(why["p6"] or 0), "recentReports24h": int(why["r24"] or 0),
                   "observationComplete": not partial, "rank": entry["rank"] if entry else None},  # fmt: skip
        "developments": [{**d, "representative": _report_view(d["representative"])} for d in developments],
        "officialReports": [_report_view(r) for r in [r for r in reports if r["role"] != "mention" and r["first_party"]][:12]],
        "timeline": [_report_view(r) for r in reports[:100]],
        "heat": [{"hour": iso(h["hour"]), "heat": h["heat"], "participants": h["participants"]} for h in heat],
        "related": [{"publicId": r["public_id"], "title": r["title"], "relation": r["relation"]} for r in related],
        "topics": topics,
    }  # fmt: skip


# ── hot list ────────────────────────────────────────────────────────────────────────────


def _sparklines(conn, story_ids: list[int], at: datetime) -> dict[int, list]:
    """Hourly heat of the ranked stories over the day before the ranking, one slot per hour."""
    end = int(at.timestamp() * 1000 // 3600000) * 3600000
    start = end - SPARK_HOURS * 3600000
    rows = (
        conn.execute(
            "SELECT story_id, hour, heat FROM story_heat_hourly WHERE story_id = ANY(%s::bigint[]) AND complete AND hour >= %s AND hour <= %s",
            (story_ids, datetime.fromtimestamp(start / 1000, UTC), datetime.fromtimestamp(end / 1000, UTC)),
        ).fetchall()
        if story_ids
        else []
    )
    out: dict[int, list] = {i: [None] * (SPARK_HOURS + 1) for i in story_ids}
    for r in rows:
        slot = js_round((r["hour"].timestamp() * 1000 - start) / 3600000)
        line = out.get(int(r["story_id"]))
        if line is not None and 0 <= slot <= SPARK_HOURS:
            line[slot] = float(r["heat"])
    return out


def _hot_covers(conn, ranking: dict) -> dict[int, dict]:
    """A picture per story from its public full-text reports, the representative first, wide enough for a card."""
    now = datetime.now(UTC)
    entries = ranking["entries"]
    ids = [e["storyId"] for e in entries]
    reps = [e["representativeItemId"] for e in entries if e.get("representativeItemId")]
    rows = conn.execute(
        f"""SELECT DISTINCT ON (p.story_id) p.story_id, img.m
            FROM publications p JOIN articles a ON a.id = p.article_id
            CROSS JOIN LATERAL (
              SELECT m FROM jsonb_array_elements(coalesce(a.media, '[]'::jsonb)) m
              WHERE m->>'kind' = 'image' AND coalesce((m->>'width')::numeric, 800) >= 480 LIMIT 1
            ) img
            WHERE p.story_id = ANY(%(ids)s::bigint[]) AND {listed_condition()} AND p.body_mode <> 'summary'
            ORDER BY p.story_id, (p.article_id::text = ANY(%(reps)s::text[])) DESC, p.first_party DESC, p.selected DESC, coalesce(p.score, 0) DESC, p.article_id""",
        {**scope_params(now), "ids": ids, "reps": reps},
    ).fetchall()
    return {
        int(c["story_id"]): {"url": c["m"]["url"], "width": c["m"].get("width") if isinstance(c["m"].get("width"), (int, float)) else None,
                             "height": c["m"].get("height") if isinstance(c["m"].get("height"), (int, float)) else None}
        for c in rows
    }  # fmt: skip


def load_hot(pool) -> dict:
    with pool.connection() as conn:
        ranking = latest_hot_ranking(conn)
        if not ranking:
            return {"computedAt": None, "windowHours": 48, "entries": []}
        at = datetime.fromisoformat(ranking["computedAt"].replace("Z", "+00:00"))
        sparks = _sparklines(conn, [e["storyId"] for e in ranking["entries"]], at)
        covers = _hot_covers(conn, ranking)
    extras = _hot_extras(pool, ranking)
    entries = []
    for e in ranking["entries"]:
        picture = covers.get(e["storyId"])
        cover_url = proxied_image(picture["url"], "full") if picture else None
        text = extras["text"](e)
        # The card clips its text to a few lines; complete public words belong to the event page.
        summary = text["summary"]
        if summary and len(summary) > 480:
            summary = summary[:480] + "…"
        cover = None
        if picture and cover_url:
            cover = {"url": cover_url}
            src_set = proxied_image_set(picture["url"], "hero")
            if src_set:
                cover["srcSet"] = src_set
            cover |= {"width": picture["width"], "height": picture["height"]}
        entries.append({
            "rank": e["rank"], "story": {"publicId": e["storyPublicId"], "title": e["title"]}, "heat": e["heat"], "trend": e["trend"], "trendPct": e["trendPct"],
            "badges": e["badges"], "participantCount": e["participantCount"], "sourceCount": e["sourceCount"],
            "sourceNames": list(dict.fromkeys(public_source_name(n) for n in e["sourceNames"])), "participants": extras["participants"](e),
            "spark": sparks.get(e["storyId"], []), "summary": summary, "latest": text["latest"], "cover": cover,
        })  # fmt: skip
    return {"computedAt": ranking["computedAt"], "windowHours": 48, "entries": entries}


# ── followups and groups ────────────────────────────────────────────────────────────────

FOLLOWUPS_SHOWN = 8
GROUP_MAX_REPORTS = 100


def load_story_followups(pool, story_public_id: str, now: datetime | None = None) -> dict | None:
    """ "事件後續" on an article page: the news facts of its event that made 精選, newest first."""
    now = now or datetime.now(UTC)
    if not _UUID.match(story_public_id):
        return None
    with pool.connection() as conn:
        members = conn.execute(
            f"""SELECT p.fact_id, f.public_id AS fact_public_id, p.article_id AS id, p.title, s.name AS source_name,
                  p.body_mode, p.score, p.timeline_at, p.sort_at, {REPRESENTATIVE_COLUMNS}
                FROM stories st JOIN publications p ON p.story_id = st.id JOIN sources s ON s.id = p.source_id JOIN facts f ON f.id = p.fact_id
                WHERE st.public_id = %(public_id)s AND st.merged_into IS NULL AND {selected_condition()} AND {own_fact_evidence_condition()}""",
            {**scope_params(now), "public_id": story_public_id},
        ).fetchall()
    by_fact: dict[int, list[dict]] = {}
    for m in members:
        by_fact.setdefault(m["fact_id"], []).append(m)
    if not by_fact:
        return None
    facts = [
        {"factId": str(r[0]["fact_public_id"]), "first": min(x["sort_at"].timestamp() * 1000 for x in r), "representative": pick_representative(r)}
        for r in by_fact.values()
    ]
    facts.sort(key=lambda f: (-f["first"], f["factId"]))
    return {
        "items": [
            {"factId": f["factId"], "representative": {"id": f["representative"]["id"], "title": f["representative"]["title"],
                                                       "source": {"name": public_source_name(f["representative"]["source_name"])},
                                                       "timelineAt": iso(f["representative"]["timeline_at"])}}
            for f in facts[:FOLLOWUPS_SHOWN]
        ],
        "more": len(facts) > FOLLOWUPS_SHOWN,
    }  # fmt: skip


def load_group_reports(pool, fact_public_id: str, channel: str, category: str | None, tag: str | None, now: datetime | None = None) -> dict | None:
    """The fact's public reports under the filters, newest first; None when it has none."""
    now = now or datetime.now(UTC)
    fsql, fparams = filter_sql(channel, category, tag)
    with pool.connection() as conn:
        members = conn.execute(
            f"""SELECT p.article_id AS id, p.title, p.timeline_at, p.url, s.name AS source_name
                FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id JOIN sources s ON s.id = p.source_id
                WHERE f.public_id = %(fact)s AND {evidence_condition()} AND {listed_condition()} {fsql}
                ORDER BY p.timeline_at DESC, p.article_id ASC LIMIT %(cap)s""",
            {**scope_params(now), **fparams, "fact": fact_public_id, "cap": GROUP_MAX_REPORTS},
        ).fetchall()
    if not members:
        return None
    return {"factId": fact_public_id, "reports": [{"id": m["id"], "title": m["title"], "source": {"name": public_source_name(m["source_name"])},
                                                   "timelineAt": iso(m["timeline_at"]), "originalUrl": m["url"]} for m in members]}  # fmt: skip
