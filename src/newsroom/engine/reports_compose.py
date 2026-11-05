"""Daily, weekly and monthly reports. Windows are Beijing calendar based and written into the report; missed schedule points are caught up; regeneration creates a
revision. What every issue carries is decided by rule: a daily from reports_edition without a model; a weekly or monthly is compiled from its dailies and a model only
writes its overview and introductions, from the brief in the industry pack. Port of reports/compose.ts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import regex
from psycopg.types.json import Jsonb

from newsroom.engine import models as M
from newsroom.engine import pack
from newsroom.engine import vocabulary as V
from newsroom.engine.analyze import _conn
from newsroom.engine.lib.text import js_slice
from newsroom.engine.llm import SchemaError, chat_json, js_length
from newsroom.engine.pack import load
from newsroom.engine.prompts import prompt_text, prompt_version
from newsroom.engine.receipts import complete_receipt
from newsroom.engine.reports_edition import arrange_daily, candidates, daily_edition, period_entries, section_of, section_order
from newsroom.engine.shutdown import SHUTDOWN
from newsroom.engine.time import add_days, beijing_at, beijing_date, beijing_time, iso_week_label, iso_week_range, month_range

PERIOD_EVENTS = {"weekly": 20, "monthly": 30}  # a weekly's or monthly's size: the events it carries, chosen and ordered by rule
INTRO_EVENTS = 3  # a section is introduced once it carries this many events; one or two are read faster than introduced
OVERVIEW_CHARS = {"weekly": 240, "monthly": 340}  # the longest overview and introduction an issue prints; the brief asks for less, writers overshoot
INTRO_CHARS = 90
BRIEF = {
    "weekly": {"kindName": "週報", "span": "一週", "sentences": "三句話", "chars": "160"},
    "monthly": {"kindName": "月報", "span": "個月", "sentences": "三到四句話", "chars": "240"},
}


def report_version() -> str:
    return prompt_version("report-period", "report-period-sections", "report-period-no-sections")


def _site() -> dict:
    return pack.site()


def daily_metrics(main: list[dict]) -> dict:
    """The masthead's figures, counted in events: sources over every report its entries cite; releases (`modelsReleased`, its public name) are the entries of the
    pack's headline launch kind that a maker announced itself, new that day. An industry without such a kind has no release figure."""
    return {
        "totalEvents": len(main),
        "sourcesCount": len({s for e in main for s in [e["entry"]["sourceId"], *[r["sourceId"] for r in e["entry"].get("related") or []]]}),
        **({"modelsReleased": sum(1 for e in main if V.is_release(e["category"], e["tags"]) and not e["previous"] and e["authority"] < 3)} if load("taxonomy").get("RELEASE") else {}),
        "firstPartyEvents": sum(1 for e in main if e["entry"]["firstParty"]),
    }  # fmt: skip


def _saved_report(pool, kind: str, key: str) -> dict | None:
    """How many events an issue already published carries; nothing when it does not exist yet."""
    with pool.connection() as conn:
        return conn.execute(
            """SELECT coalesce(CASE WHEN kind = 'daily' THEN (content->'metrics'->>'totalEvents')::int ELSE jsonb_array_length(content->'storyOrder') END, 0) AS entries
               FROM reports WHERE kind = %s AND key = %s""",
            (kind, key),
        ).fetchone()


def _save_report(pool, kind: str, key: str, start, end, content: dict, reason: str | None, model: str | None, receipt_ids: list[int]) -> bool:
    """Without a reason (the scheduled run) only a missing issue is written: one already published stays as it is. With a reason (an explicit regeneration) the issue
    is replaced and the edition it replaces is kept as a revision. Says whether the issue was written."""
    with pool.connection() as conn, conn.transaction():

        def insert() -> bool:
            return (
                conn.execute(
                    """INSERT INTO reports (kind, key, window_start, window_end, content, generated_at, model, origin)
                       VALUES (%s, %s, %s, %s, %s, now(), %s, 'model') ON CONFLICT (kind, key) DO NOTHING""",
                    (kind, key, start, end, Jsonb(content), model),
                ).rowcount
                > 0
            )

        if reason is None:
            written = insert()
        else:
            existing = conn.execute(
                "SELECT id, revision, content, generated_at FROM reports WHERE kind = %s AND key = %s FOR UPDATE", (kind, key)
            ).fetchone()
            if existing:
                conn.execute(
                    "INSERT INTO report_revisions (report_id, revision, content, generated_at, reason) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (existing["id"], existing["revision"], Jsonb(existing["content"]), existing["generated_at"], reason),
                )
                conn.execute(
                    """UPDATE reports SET content = %s, window_start = %s, window_end = %s, generated_at = now(), model = %s, revision = revision + 1, origin = 'model',
                         updated_at = now() WHERE id = %s""",
                    (Jsonb(content), start, end, model, existing["id"]),
                )
                written = True
            else:
                written = insert()
        # (emit("reportsChanged") has no listener here: the engine runs without site modules)
        for rid in receipt_ids:
            complete_receipt(conn, rid)
        return written


def _window_point(at: datetime) -> str:
    """'10 月 4 日 08:00': one end of a quiet issue's window, as its lead paragraph names it."""
    day = beijing_date(at)
    return f"{int(day[5:7])} 月 {int(day[8:10])} 日 {beijing_time(at)}"


def compose_daily(pool, date: str, reason: str | None = None) -> dict:
    """Daily report for Beijing date D covers the 24 hours up to the site's edition time on D. Its most important entry leads, in its own words, and the next three
    are today's highlights. A window the editors judged with nothing new in it still has its issue: no entries, and a lead that says so."""
    previous = _saved_report(pool, "daily", date)
    if previous and reason is None:
        return {"key": date, "entries": previous["entries"]}
    end = beijing_at(date, _site()["EDITION_TIMES"]["daily"])
    start = end - timedelta(days=1)
    edition = daily_edition(pool, date, start, end)
    # Nothing judged in the window is a failure upstream, not a quiet day: the run fails and is caught up later.
    if not edition["entries"] and edition["stats"]["judgedReports"] == 0:
        raise RuntimeError(f"daily {date}: nothing judged in its window")
    issue = arrange_daily(edition["entries"])
    lead = issue["main"][0] if issue["main"] else None
    rest = issue["main"][1:]
    quiet = _site()["REPORTS"]["quiet"]
    content = {
        "date": date,
        "lead": {"title": lead["entry"]["title"], "leadParagraph": lead["entry"]["summary"]} if lead
        else {"title": quiet["title"], "leadParagraph": quiet["paragraph"].replace("{start}", _window_point(start)).replace("{end}", _window_point(end))},
        "leadItemId": lead["entry"]["itemId"] if lead else None,
        "highlights": [e["entry"]["itemId"] for e in rest[:3]],
        "sections": [s for s in ({"label": label, "items": [e["entry"] for e in issue["main"] if section_of(e["category"]) == label]} for label in section_order()) if s["items"]],
        "flashes": [e["entry"] for e in issue["flashes"]],
        "metrics": daily_metrics(issue["main"]),
        "windowStart": _iso(start),
        "windowEnd": _iso(end),
        "generator": {"version": report_version(), **edition["stats"], **issue["stats"]},
    }  # fmt: skip
    _save_report(pool, "daily", date, start, end, content, reason, None, [])
    return {"key": date, "entries": len(issue["main"])}


def _iso(d: datetime) -> str:
    from newsroom.engine.publication.v1 import iso

    return iso(d)


def period_schema(data) -> dict:
    if not isinstance(data, dict):
        raise SchemaError("expected an object")
    overview = data.get("overview")
    overview = overview if isinstance(overview, str) and js_length(overview) <= 1500 else ""
    sections = data.get("sections")
    if not (isinstance(sections, dict) and all(isinstance(v, str) and js_length(v) <= 600 for v in sections.values())):
        sections = {}
    return {"overview": overview, "sections": sections}


def period_prompt(kind: str, start_date: str, end_date_inclusive: str, groups: list[dict]) -> dict:
    """The writer's brief for a week or month: its events as chosen, ordered and grouped by rule. It writes the overview and the introductions of the sections large
    enough for one, and decides nothing about what the issue carries."""
    listing = "\n".join("\n".join([f"【{g['label']}】", *[f"- {e['title']}｜{js_slice(e['summary'], 0, 140)}" for e in g["items"]]]) for g in groups)
    introduced = [g["label"] for g in groups if len(g["items"]) >= INTRO_EVENTS]
    return {
        "system": prompt_text("report-period", {
            **BRIEF[kind],
            "sections": prompt_text("report-period-sections", {"columns": "".join(f"「{label}」" for label in introduced)}) if introduced else prompt_text("report-period-no-sections"),
            "sectionsExample": f'{{"{introduced[0]}": "..."}}' if introduced else "{}",
        }),
        "user": f"本期：{start_date} 至 {end_date_inclusive}\n{listing}",
    }  # fmt: skip


def _company_names() -> list[list[str]]:
    return [[n.lower() for n in V.entity_names(e)] for e in V.entities().values()]


def _plain() -> set[str]:
    """Words a writer may use that name nothing in particular: the pack's plain terms and the site's name."""
    return {*load("taxonomy")["PLAIN_TERMS"], _site()["SITE"]["name"].lower()}


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.+-]*")
_FIGURE = re.compile(r"[0-9]+(?:\.[0-9]+)?%?")
_HAN = regex.compile(r"\p{Script=Han}")


def grounded(text: str, corpus: str) -> bool:
    """Whether written text names only what the listed items name: every capitalised or numbered Latin token, every figure of three or more digits or with a decimal
    point or percent and every company of the vocabulary appears in the items' own words; a company may be named in either language (谷歌 for Google)."""
    known = corpus.lower()
    plain = _plain()
    all_names = _company_names()
    companies = [names for names in all_names if any(n in known for n in names)]

    def named(word: str) -> bool:
        return word in plain or word in known or any(word in names for names in companies)

    words = [w for w in (re.sub(r"[.+-]+$", "", m) for m in _WORD.findall(text)) if re.search(r"[A-Z0-9]", w)]
    figures = [f for f in _FIGURE.findall(text) if js_length(f) >= 3 or re.search(r"[.%]", f)]
    lower = text.lower()
    mentioned = [names for names in all_names if any(_HAN.search(n) and n in lower for n in names)]
    return all(named(w.lower()) for w in words) and all(f in known for f in figures) and all(names in companies for names in mentioned)


_SENTENCE = re.compile(r"[^。！？]+(?:[。！？]+[」”’）]*|$)")


def fitted(text: str, max_chars: int) -> str | None:
    """The leading whole sentences of a text that fit in `max` characters; null when not even the first does."""
    out = ""
    for sentence in _SENTENCE.findall(text.strip()):
        if len(out + sentence) > max_chars:
            break
        out += sentence
    return out.strip() or None


def _compose_period(pool, kind: str, key: str, start_date: str, end_date: str, reason: str | None) -> dict | None:
    """A weekly or monthly, compiled from the dailies dated in the period. Its events, their order and their sections are decided by rule: the first event leads and
    the next three are the highlights. A model only writes the overview and the introductions of sections with three or more events; text naming anything the events
    do not name is not used, and overlong text keeps the leading sentences that fit. An automatic run skips a complete period of quiet dailies without publishing an
    empty issue."""
    previous = _saved_report(pool, kind, key)
    if previous and reason is None:
        return {"key": key, "entries": previous["entries"]}
    times = _site()["EDITION_TIMES"]
    # The dailies' windows run from the edition time the day before the first to that time on the last.
    start = beijing_at(add_days(start_date, -1), times["daily"])
    end = beijing_at(end_date, times["daily"])
    found = period_entries(pool, start_date, end_date)
    entries, issues, has_entries = found["entries"], found["issues"], found["hasEntries"]
    top = entries[: PERIOD_EVENTS[kind]]
    if not top:
        if reason is None and not has_entries and issues > 0:
            # Before the site's first daily there was nothing to publish; missing later dailies are still a failure.
            with pool.connection() as conn:
                first = conn.execute("SELECT min(key) AS first FROM reports WHERE kind = 'daily'").fetchone()["first"]
            first_date = first if first and first > start_date else start_date
            days = (datetime.fromisoformat(end_date) - datetime.fromisoformat(first_date)).days + 1
            if issues == days:
                return None
        raise RuntimeError(f"{kind} {key}: no daily entries in the period")
    selected = candidates(pool, start, end)
    groups = [
        g for g in ({"label": label, "items": [e for e in top if section_of(e["category"]) == label]} for label in section_order()) if g["items"]
    ]
    corpus = "\n".join([f"{start_date} {end_date}", *[f"{e['title']} {e['summary']}" for e in top]])
    model = M.model_for(_conn(pool), "report")
    written, receipt_id = {"overview": "", "sections": {}}, None
    try:
        res = chat_json(pool, {
            "model": model, "purpose": f"report_{kind}", "subject": f"report:{kind}:{key}", "prompt_version": report_version(), **period_prompt(kind, start_date, end_date, groups),
            "schema": period_schema, "temperature": 0.3, "max_tokens": 2500,
        })  # fmt: skip
        written, receipt_id = res["data"], res["receiptId"]
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        print(
            json.dumps(
                {
                    "level": "warn",
                    "msg": "period writer failed; the issue goes out with its plain overview",
                    "report": f"{kind}:{key}",
                    "error": str(error)[:300],
                }
            )
        )

    def usable(text: str | None, limit: int) -> str | None:
        fit = fitted(text or "", limit)
        return fit if fit and grounded(fit, corpus) else None

    # Without a usable one the read layer says what the issue carries, naming only what is still public.
    overview = usable(written["overview"], OVERVIEW_CHARS[kind])

    def intro(g: dict) -> str | None:
        return usable(written["sections"].get(g["label"]), INTRO_CHARS) if len(g["items"]) >= INTRO_EVENTS else None

    lead = top[0]
    name = _site()["SITE"]["name"]
    content = {
        "kind": kind,
        "title": f"{name} 週報 · {key}" if kind == "weekly" else f"{name} 月報 · {key}",
        **({"isoLabel": key} if kind == "weekly" else {"monthLabel": key}),
        "periodStart": start_date,
        "periodEnd": end_date,
        "headline": lead["title"],
        "leadItemId": lead["itemId"],
        "highlights": [e["itemId"] for e in top[1:4]],
        "overview": overview,
        "themes": [{"heading": g["label"], "summary": intro(g), "storyRefs": [{k: v for k, v in e.items() if k not in ("category", "factKey")} for e in g["items"]]} for g in groups],
        "storyOrder": [e["itemId"] for e in top],
        "metrics": {"totalStories": len(top), "selectedCount": len(selected), "reportsCovered": issues},
        "generator": {"version": report_version(), "model": model, "written": overview is not None},
    }  # fmt: skip
    _save_report(pool, kind, key, start, end, content, reason, model, [] if receipt_id is None else [receipt_id])
    return {"key": key, "entries": len(top)}


def compose_weekly(pool, label: str, reason: str | None = None):
    r = iso_week_range(label)
    if not r:
        raise ValueError(f"bad week label {label}")
    return _compose_period(pool, "weekly", label, r["start"], r["end"], reason)


def compose_monthly(pool, label: str, reason: str | None = None):
    r = month_range(label)
    if not r:
        raise ValueError(f"bad month label {label}")
    return _compose_period(pool, "monthly", label, r["start"], r["end"], reason)


def due_daily(now: datetime | None = None) -> str:
    """The newest daily due by `now`: today's from its edition time (Beijing), yesterday's before."""
    now = now or datetime.now(UTC)
    today = beijing_date(now)
    return today if beijing_time(now) >= _site()["EDITION_TIMES"]["daily"] else add_days(today, -1)


def due_weekly(now: datetime | None = None) -> str:
    """The newest weekly due by `now`: the last complete ISO week from its edition time on Monday, the one before until then."""
    now = now or datetime.now(UTC)
    today = beijing_date(now)
    dow = (datetime.fromisoformat(today).weekday()) % 7  # Monday = 0
    due = dow > 0 or beijing_time(now) >= _site()["EDITION_TIMES"]["weekly"]
    return iso_week_label(add_days(today, -dow - (7 if due else 14)))


def due_monthly(now: datetime | None = None) -> str:
    """The newest monthly due by `now`: the last complete month from its edition time on the 1st, the one before until then."""
    now = now or datetime.now(UTC)
    y, m, d = (int(x) for x in beijing_date(now).split("-"))
    due = d > 1 or beijing_time(now) >= _site()["EDITION_TIMES"]["monthly"]
    month = y * 12 + (m - 1) - (1 if due else 2)
    return f"{month // 12}-{month % 12 + 1:02d}"


def _next_week(label: str) -> str:
    return iso_week_label(add_days(iso_week_range(label)["start"], 7))


def _next_month(label: str) -> str:
    y, m = (int(x) for x in label.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def compose_due_reports(pool, now: datetime | None = None, limit: int = 8) -> dict:
    """The scheduled run (every half hour): every issue due by `now` that does not exist yet, oldest first. The newest one appears at the first run after it falls due;
    a long stop or an older gap is filled too. A kind with no issue yet only gets its latest due one. An issue that fails does not hold up the others; at most `limit`
    issues are written per run, the next run continues."""
    now = now or datetime.now(UTC)
    generated: list[str] = []
    failed: list[str] = []
    kinds = [
        ("daily", due_daily(now), lambda k: add_days(k, 1), lambda k: k, lambda k: compose_daily(pool, k)),
        ("weekly", due_weekly(now), _next_week, lambda k: iso_week_range(k)["end"], lambda k: compose_weekly(pool, k)),
        ("monthly", due_monthly(now), _next_month, lambda k: month_range(k)["end"], lambda k: compose_monthly(pool, k)),
    ]
    # Weeklies and monthlies sum up dailies: a period that ended before the site's first daily has nothing to sum up and is not due (a site started mid-month would
    # otherwise fail that month's issue on every run).
    with pool.connection() as conn:
        first_daily = conn.execute("SELECT min(key) AS first FROM reports WHERE kind = 'daily'").fetchone()["first"]
    stop = False
    for kind, due, nxt, end_of, compose in kinds:
        if stop:
            break
        with pool.connection() as conn:
            have = {r["key"] for r in conn.execute("SELECT key FROM reports WHERE kind = %s", (kind,)).fetchall()}
        key = sorted(have)[0] if have else due
        while key <= due:
            this, key = key, nxt(key)
            if this in have:
                continue
            if kind != "daily" and (not first_daily or end_of(this) < first_daily):
                continue
            if SHUTDOWN.is_set() or len(generated) >= limit:
                stop = True
                break
            try:
                if compose(this) is not None:
                    generated.append(f"{kind}:{this}")
            except Exception as error:
                # An issue interrupted by the stop is not a failure: it is written after the restart.
                if SHUTDOWN.is_set():
                    stop = True
                    break
                failed.append(f"{kind}:{this}")
                print(json.dumps({"level": "error", "msg": "report failed", "report": f"{kind}:{this}", "error": str(error)[:500]}))
    if failed:
        raise RuntimeError(f"reports: {', '.join(failed)} failed" + (f"; {', '.join(generated)} written" if generated else ""))
    return {"generated": generated, "failed": failed}
