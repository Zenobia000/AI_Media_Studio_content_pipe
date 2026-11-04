"""Writes today's inbox/YYYY-MM-DD.md (IF-001) from the public hot ranking, twice an hour: the hot stories, the links added by hand, and
what the days before left (cross-day). The ranking is the one AIHOT's site stores (hot_rankings); it is read as stored, without the
site's re-check of each entry's representative report."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsroom.collect.alerts import notify_new_s
from newsroom.collect.calendar import just_ended, mentions_ended_event, starting_tomorrow, sync_events
from newsroom.collect.heat_inputs import load_heat_inputs
from newsroom.collect.inbox import (
    CARRY_MS,
    Candidate,
    FailingSource,
    Manual,
    StateRow,
    parse_annotations,
    plan_inbox,
    render_file,
    with_health,
)
from newsroom.collect.local_heat import with_local_heat
from newsroom.collect.score import HeatInput, HeatResult, describe_breakdown, load_heat_config, score_heat
from newsroom.collect.signals import describe_signals, latest_signals
from newsroom.collect.titles import with_titles_zh
from newsroom.core.workspace import clock_hm, day_of, inbox_dir, read_annotations, write_atomic

DAY = timedelta(days=1)
FAIL_LISTED = 3  # consecutive failed collections before a source is listed in the inbox
LOOKBACK_DAYS = 14  # earlier inbox files read for ticks and notes
TIER_ORDER = {"S": 0, "A": 1, "B": 2}


def latest_hot_ranking(conn) -> dict | None:
    row = conn.execute("SELECT id, computed_at, entries FROM hot_rankings WHERE published ORDER BY computed_at DESC LIMIT 1").fetchone()
    return row


def _heat_fields(h: HeatResult) -> dict:
    return {"tier": h.tier, "score": h.score, "heat": describe_breakdown(h), "heat_reason": "、".join(h.reasons)}


def write_inbox(conn, now: datetime | None = None, notify=None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    directory = inbox_dir()
    date = day_of(now)
    file = Path(directory) / f"{date}.md"
    ranking = latest_hot_ranking(conn)
    entries = ranking["entries"] if ranking else []
    manual = conn.execute(
        """SELECT m.article_id, a.title, m.notes, m.program, m.formats, m.added_at FROM manual_candidates m JOIN articles a ON a.id = m.article_id
           WHERE m.added_at > %s ORDER BY m.added_at DESC""",
        (now - timedelta(milliseconds=CARRY_MS),),
    ).fetchall()
    story_ids = [e["storyId"] for e in entries]
    article_ids = [m["article_id"] for m in manual]
    # What people are saying about each, from the signal snapshots.
    by_story_sig, by_article_sig = latest_signals(conn, story_ids, article_ids)
    # How hot each is (heat.config.json): the tier, the score and why, and what to suggest.
    config = load_heat_config()
    inputs = load_heat_inputs(conn, config, now, story_ids, article_ids)
    # The launch events in calendar/events.md: a story that names one that has just ended is S (COL-FR-017).
    events, _ = sync_events(conn)
    ended = just_ended(events, now)

    def heat_of(inp: HeatInput | None, title: str) -> HeatResult:
        base = dataclasses.replace(inp) if inp else HeatInput()
        base.event_ended = mentions_ended_event(title, ended)
        return score_heat(base, config)

    listed: list[Candidate] = []
    for m in manual:
        h = heat_of(inputs.by_article.get(m["article_id"]), m["title"])
        listed.append(
            Candidate(
                id=f"cand_{m['article_id']}",
                title=m["title"],
                participant_count=1,
                source_names=["手動"],
                first_seen=clock_hm(m["added_at"]),
                suggested_program=m["program"] or h.program,
                suggested_formats=list(m["formats"]) or h.formats,
                manual=Manual(note=m["notes"], program=m["program"] or "", formats=", ".join(m["formats"])),
                signals=describe_signals(by_article_sig.get(m["article_id"], {})),
                **_heat_fields(h),
            )
        )
    for e in entries:
        h = heat_of(inputs.by_story.get(e["storyId"]), e["title"])
        listed.append(
            Candidate(
                id=f"cand_{e['storyPublicId']}",
                title=e["title"],
                participant_count=e["participantCount"],
                source_names=e["sourceNames"],
                first_seen=clock_hm(datetime.fromisoformat(e["firstReportAt"].replace("Z", "+00:00"))),
                suggested_program=h.program,
                suggested_formats=h.formats,
                signals=describe_signals(by_story_sig.get(e["storyId"], {})),
                **_heat_fields(h),
            )
        )
    # The day before a launch event: a candidate to prepare it.
    for ev in starting_tomorrow(events, now):
        note = "　".join(x for x in (ev.url or "", f"關鍵字：{'、'.join(ev.keywords)}" if ev.keywords else "") if x)
        listed.append(
            Candidate(
                id=f"cand_{ev.id}",
                title=f"準備：{ev.title}",
                tier="A",
                heat_reason=f"明天 {clock_hm(ev.starts_at)} 發表會",
                participant_count=1,
                source_names=["發表會行事曆"],
                first_seen=clock_hm(now),
                suggested_program="ai-today",
                suggested_formats=["threads"],
                manual=Manual(note=note),
            )
        )
    fresh = with_titles_zh(conn, listed)
    # The hottest first within a tier.
    fresh.sort(key=lambda c: (TIER_ORDER[c.tier], -(c.score or 0), c.title))

    existing = file.read_text(encoding="utf-8") if file.exists() else None
    earlier = read_annotations(directory, day_of(now - timedelta(days=LOOKBACK_DAYS)), date)
    # The latest word of the person wins: a tick picks a candidate, taking the tick away puts it back to new.
    marks = {**earlier, **(parse_annotations(existing) if existing else {})}
    ticked = [i for i, a in marks.items() if a.checked]
    unticked = [i for i, a in marks.items() if not a.checked]
    if ticked:
        conn.execute("UPDATE candidate_state SET status = 'picked', updated_at = now() WHERE candidate_id = ANY(%s) AND status = 'new'", (ticked,))
    if unticked:
        conn.execute("UPDATE candidate_state SET status = 'new', updated_at = now() WHERE candidate_id = ANY(%s) AND status = 'picked'", (unticked,))

    rows = [
        StateRow(r["candidate_id"], r["status"], r["first_listed_at"], _snapshot_to_candidate(r["snapshot"]))
        for r in conn.execute("SELECT candidate_id, status, first_listed_at, snapshot FROM candidate_state").fetchall()
    ]
    planned = plan_inbox(fresh, rows, now, date, day_of)
    # Carried candidates show the Chinese title given after they were listed.
    plan_listed = with_local_heat(conn, with_titles_zh(conn, planned.listed))

    # A source that failed its last three collections is listed at the foot (COL-FR-004).
    failing = [
        FailingSource(
            s["name"], s["fail_count"], s["last_error"], f"{day_of(s['last_ok_at'])} {clock_hm(s['last_ok_at'])}" if s["last_ok_at"] else None
        )
        for s in conn.execute(
            "SELECT name, fail_count, last_error, last_ok_at FROM sources WHERE enabled AND fail_count >= %s ORDER BY fail_count DESC, name",
            (FAIL_LISTED,),
        ).fetchall()
    ]
    # With nothing to list, every source healthy and no inbox yet, there is nothing to write; an existing one is brought up to date.
    if not plan_listed and not ranking and not failing and existing is None:
        return None
    write_atomic(file, with_health(render_file(date, plan_listed, existing, earlier), failing))

    fresh_ids = {c.id for c in fresh}
    for c in (c for c in plan_listed if c.id in fresh_ids):
        snapshot = {k: v for k, v in dataclasses.asdict(c).items() if k != "carried"}
        conn.execute(
            """INSERT INTO candidate_state (candidate_id, first_listed_at, snapshot) VALUES (%s, %s, %s)
               ON CONFLICT (candidate_id) DO UPDATE SET snapshot = EXCLUDED.snapshot, updated_at = now()""",
            (c.id, now, json.dumps(snapshot)),
        )
    if planned.skip:
        conn.execute(
            "UPDATE candidate_state SET status = 'skipped', updated_at = now() WHERE candidate_id = ANY(%s) AND status = 'new'", (planned.skip,)
        )
    # A candidate that reaches S for the first time is told once (COL-FR-018).
    notify_new_s(conn, plan_listed, str(file), *([notify] if notify else []))
    return {"file": str(file), "candidates": len(plan_listed)}


def _snapshot_to_candidate(snapshot: dict) -> Candidate:
    """A candidate as the snapshot column holds it: TypeScript wrote camelCase keys, this module writes snake_case."""
    camel = {
        "participantCount": "participant_count",
        "sourceNames": "source_names",
        "firstSeen": "first_seen",
        "suggestedProgram": "suggested_program",
        "suggestedFormats": "suggested_formats",
        "heatReason": "heat_reason",
    }
    fields = {camel.get(k, k): v for k, v in snapshot.items()}
    manual = fields.get("manual")
    fields["manual"] = Manual(**manual) if isinstance(manual, dict) else None
    known = {f.name for f in dataclasses.fields(Candidate)}
    # A snapshot that lacks a field (an older row) is still listed, with plain defaults for what it does not say.
    defaults = {
        "id": "",
        "title": "",
        "tier": "B",
        "participant_count": 1,
        "source_names": [],
        "first_seen": "",
        "suggested_program": "ai-today",
        "suggested_formats": ["threads"],
    }
    return Candidate(**{**defaults, **{k: v for k, v in fields.items() if k in known and k != "carried"}})
