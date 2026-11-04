"""Reports through the public read layer, v1 shapes: port of publication/reports.ts (the index, leads and the v1 dailies, weeklies and
monthlies). Only real reports are listed; a missing date is a 404, never another day. Withdrawn citations are marked, not shown.

Reports are JSON documents saved by the composer, so they are handled as dicts, and JavaScript's truthiness and `??` are spelled out where
the TypeScript relies on them."""

from __future__ import annotations

import json
from datetime import datetime

import regex

from newsroom.engine import pack
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.cache import cached
from newsroom.engine.publication.scope import listed_condition, scope_params
from newsroom.engine.publication.stories import public_source_name  # noqa: F401  (the citation read side uses it)
from newsroom.engine.publication.v1 import iso, item_url
from newsroom.engine.time import is_valid_date, iso_week_range, month_range

INDEX_LIMIT = 400


def truthy(v) -> bool:
    """JavaScript truthiness."""
    return v not in (None, False, 0, "", 0.0) and v == v


def nullish(v, default):
    return default if v is None else v


def daily_url(date: str) -> str:
    return site_url(f"/daily/{date}")


def period_url(kind: str, key: str) -> str:
    return site_url(f"/{kind}/{key}")


def attribution(url: str) -> dict:
    return {"name": pack.site()["SITE"]["name"], "url": url}


# ── availability of cited items ─────────────────────────────────────────────────────────


def availability(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = conn.execute(
        f"""SELECT p.article_id AS id, ({listed_condition()}) AS available, p.summary, (s.tier = 'T1') AS first_party, p.source_id, s.name AS source_name,
              s.icon_url, st.public_id::text AS story_id, coalesce(p.published_at, p.discovered_at) AS at
            FROM publications p LEFT JOIN sources s ON s.id = p.source_id LEFT JOIN stories st ON st.id = p.story_id
            WHERE p.article_id = ANY(%(ids)s)""",
        {**scope_params(datetime.now().astimezone()), "ids": ids},
    ).fetchall()
    return {r["id"]: {"available": r["available"], "summary": r["summary"], "firstParty": r["first_party"], "sourceId": r["source_id"], "sourceName": r["source_name"],
                      "sourceIcon": r["icon_url"], "publishedAt": r["at"], "storyId": r["story_id"]} for r in rows}  # fmt: skip


def unavailable_ids(conn, ids: list[str]) -> set[str]:
    """Ids among `ids` that are no longer public. Ids absent from this database stay cited as published."""
    unique = sorted({i for i in ids if i})
    if not unique:
        return set()
    rows = conn.execute(
        f"SELECT p.article_id AS id FROM publications p WHERE p.article_id = ANY(%(ids)s) AND NOT ({listed_condition()})",
        {**scope_params(datetime.now().astimezone()), "ids": unique},
    ).fetchall()
    return {r["id"] for r in rows}


def report_index_rows(conn, kind: str, limit: int) -> list[dict]:
    """Directory/feed metadata only: citation summaries and full report prose stay in the detail read. Issue numbers count the whole series of
    the kind before the limit applies."""
    return conn.execute(
        """SELECT key, generated_at, (row_number() OVER (ORDER BY key))::int AS issue_number, jsonb_build_object(
             'lead', content->'lead', 'headline', content->'headline', 'title', content->'title',
             'leadItemId', content->'leadItemId', 'highlights', content->'highlights',
             CASE WHEN kind = 'daily' THEN 'sections' ELSE 'themes' END,
             jsonb_build_array(jsonb_build_object(CASE WHEN kind = 'daily' THEN 'items' ELSE 'storyRefs' END,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('itemId', item->'itemId', 'title', item->'title') ORDER BY ord), '[]'::jsonb)
                FROM jsonb_array_elements(jsonb_path_query_array(content,
                  CASE WHEN kind = 'daily' THEN '$.sections[*].items[*]'::jsonpath ELSE '$.themes[*].storyRefs[*]'::jsonpath END
                )) WITH ORDINALITY AS cited(item, ord))))) AS content
           FROM reports WHERE kind = %s ORDER BY key DESC LIMIT %s""",
        (kind, limit),
    ).fetchall()


# ── leads ───────────────────────────────────────────────────────────────────────────────


def entries_of(content: dict, kind: str) -> list[dict]:
    """The entries an issue cites in reading order: a daily's sections, a weekly's or monthly's themes."""
    if kind == "daily":
        return [i for s in nullish(content.get("sections"), []) for i in nullish(s.get("items"), [])]
    return [r for t in nullish(content.get("themes"), []) for r in nullish(t.get("storyRefs"), [])]


def periodic_headline(content: dict) -> str | None:
    """A weekly or monthly's own headline; the composer's "<site name> 週報 · 2026-W38" names the issue, not its news."""
    text = str(nullish(content.get("headline"), content.get("title") if content.get("title") is not None else ""))
    name = pack.site()["SITE"]["name"]
    issue_name = text.startswith(f"{name} ") and bool(regex.match(r"^[周月]報 · ", text[len(name) + 1 :]))
    return text if text and not issue_name else None


def _bigrams(text: str) -> set[str]:
    chars = list(regex.sub(r"[\s\p{P}]", "", text.lower()))
    return {chars[i] + ch for i, ch in enumerate(chars[1:])}


def lead_item_of(lead_title: str | None, highlights: list[dict], all_: list[dict]) -> dict | None:
    """The item a daily's front page leads with: the item whose title shares most of the lead's character pairs, if most of them are shared."""
    if not lead_title:
        return next((c for c in highlights if c.get("available")), None) or next((c for c in all_ if c.get("available")), None)
    want = _bigrams(lead_title)
    if not want:
        return None
    best = None
    for c in all_:
        share = len(want & _bigrams(str(c.get("title") or ""))) / len(want)
        if best is None or share > best[1]:
            best = (c, share)
    return best[0] if best and best[1] >= 0.5 else None


def written_lead_id(content: dict, kind: str, entries: list[dict] | None = None) -> str | None:
    """A written lead's citation, using the same title match as a daily's front-page picture."""
    entries = entries_of(content, kind) if entries is None else entries
    title = (content.get("lead") or {}).get("title") if kind == "daily" else periodic_headline(content)
    if not truthy(title):
        return None
    found = lead_item_of(title, [], entries)
    return nullish(found.get("itemId"), None) if found else None


def lead_candidates(content: dict, kind: str) -> list[dict]:
    """The entries an issue may lead with, in order."""
    entries = entries_of(content, kind)
    lead_id = content.get("leadItemId") if content.get("leadItemId") is not None else written_lead_id(content, kind, entries)  # `??`, not `||`
    if not truthy(lead_id):
        has_written = (content.get("lead") or {}).get("title") if kind == "daily" else periodic_headline(content)
        return [] if truthy(has_written) else entries
    by_id = {str(e["itemId"]): e for e in entries if truthy(e.get("itemId"))}
    order = dict.fromkeys(str(x) for x in [lead_id, *nullish(content.get("highlights"), []), *(e.get("itemId") for e in entries)] if truthy(x))
    return [by_id[i] for i in order if i in by_id]


def issue_lead(content: dict, kind: str, gone: set[str]) -> dict | None:
    """The lead an issue shows, everywhere it is shown (page, indexes, feeds, v1, MCP)."""
    candidates = lead_candidates(content, kind)
    if not candidates:
        title = (content.get("lead") or {}).get("title") if kind == "daily" else periodic_headline(content)
        if not truthy(title):
            return None
        paragraph = nullish((content.get("lead") or {}).get("leadParagraph"), None) if kind == "daily" else None
        return {"itemId": None, "title": str(title), "leadParagraph": paragraph}
    first = next((e for e in candidates if not truthy(e.get("itemId")) or str(e["itemId"]) not in gone), None)
    if first is None:
        return None
    lead = {"itemId": str(first["itemId"]) if truthy(first.get("itemId")) else None, "title": str(nullish(first.get("title"), ""))}
    written_id = None if truthy(content.get("leadItemId")) else written_lead_id(content, kind)
    if written_id and first.get("itemId") == written_id:
        title = (content.get("lead") or {}).get("title") if kind == "daily" else periodic_headline(content)
        paragraph = nullish((content.get("lead") or {}).get("leadParagraph"), None) if kind == "daily" else None
        return {**lead, "title": str(title), "leadParagraph": paragraph}
    if kind == "periodic":
        return {**lead, "leadParagraph": None}
    own = first.get("itemId") == content.get("leadItemId") and isinstance((content.get("lead") or {}).get("leadParagraph"), str)
    if own:
        paragraph = content["lead"]["leadParagraph"]
    elif (truthy(content.get("leadItemId")) or written_id) and isinstance(first.get("summary"), str):
        paragraph = first["summary"]
    else:
        paragraph = None
    return {**lead, "leadParagraph": paragraph}


def daily_leads(conn, rows: list[dict], gone: set[str]) -> dict[str, dict | None]:
    """Projected indexes and feeds keep no citation prose. Fetch just a replacement lead's frozen paragraph after choosing it."""
    leads = {r["key"]: issue_lead(r["content"], "daily", gone) for r in rows}
    wanted = []
    for r in rows:
        lead = leads[r["key"]]
        if (
            lead
            and lead["itemId"]
            and lead["leadParagraph"] is None
            and (truthy(r["content"].get("leadItemId")) or written_lead_id(r["content"], "daily"))
        ):
            wanted.append({"key": r["key"], "item_id": lead["itemId"]})
    if not wanted:
        return leads
    paragraphs = conn.execute(
        """SELECT r.key, (SELECT i->>'summary' FROM jsonb_path_query(r.content, '$.sections[*].items[*]') i WHERE i->>'itemId' = wanted.item_id LIMIT 1) AS summary
           FROM reports r JOIN jsonb_to_recordset(%s::jsonb) AS wanted(key text, item_id text) ON wanted.key = r.key WHERE r.kind = 'daily'""",
        (json.dumps(wanted),),
    ).fetchall()
    for p in paragraphs:
        leads[p["key"]]["leadParagraph"] = p["summary"]  # type: ignore[index]
    return leads


def unavailable_headline_ids(conn, rows: list[dict], kind: str) -> set[str]:
    """Check only the first possible lead of each report; advance reports whose candidate was withdrawn."""
    reports = [c for c in (lead_candidates(r["content"], kind) for r in rows) if c]
    gone: set[str] = set()
    checked: set[str] = set()
    while True:
        candidates = []
        for items in reports:
            first = next((i for i in items if not truthy(i.get("itemId")) or i["itemId"] not in gone), None)
            if first and truthy(first.get("itemId")) and first["itemId"] not in checked:
                candidates.append(first["itemId"])
        if not candidates:
            return gone
        gone |= unavailable_ids(conn, candidates)
        checked |= set(candidates)


def period_overview(content: dict, kind: str, gone: set[str]) -> str | None:
    """A weekly's or monthly's overview: the one written for it, else what it carries, naming the first three of its lead, highlights and
    entries still public. `gone` must cover every entry."""
    changed = any(truthy(e.get("itemId")) and e["itemId"] in gone for e in entries_of(content, "periodic"))
    if not changed and isinstance(content.get("overview"), str) and content["overview"]:
        return content["overview"]
    shown = [e for e in lead_candidates(content, "periodic") if not truthy(e.get("itemId")) or str(e["itemId"]) not in gone]
    if not shown:
        return None
    entry = pack.site()["REPORTS"]["entry"]
    names = "；".join(str(e.get("title")) for e in shown[:3])
    return f"{'本週' if kind == 'weekly' else '本月'} {len(shown)} {entry['measure']}{entry['noun']}，最受關注的是：{names}。"


# ── citations ───────────────────────────────────────────────────────────────────────────


def still_public(raw: dict, avail: dict[str, dict]) -> bool:
    """Items absent from this database (older than the imported window) stay cited as they were published."""
    return not truthy(raw.get("itemId")) or avail.get(raw["itemId"], {}).get("available", True)


def citation_metadata(raw: dict, a: dict | None) -> dict:
    """Frozen citation fields, with current source metadata and a public summary only if none was saved."""
    a = a or {}
    raw_summary = raw.get("summary")
    published = a.get("publishedAt")
    if published is not None:
        published_at = iso(published)
    elif truthy(raw.get("publishedAt")):
        published_at = iso(datetime.fromisoformat(str(raw["publishedAt"]).replace("Z", "+00:00")))
    else:
        published_at = None
    name = (
        a.get("sourceName")
        if a.get("sourceName") is not None
        else (raw.get("sourceName") if raw.get("sourceName") is not None else (raw.get("source") or {}).get("name"))
    )
    url = raw.get("sourceUrl") if raw.get("sourceUrl") is not None else (raw.get("links") or {}).get("original")
    return {
        "summary": raw_summary if raw_summary is not None else a.get("summary"),
        "sourceName": str(name if name is not None else ""),
        "sourceUrl": str(url if url is not None else ""),
        "publishedAt": published_at,
    }


def section_summary(section: dict, avail: dict[str, dict]) -> str | None:
    """An introduction cannot keep quoting an unavailable entry after that entry leaves its section."""
    if any(not still_public(e, avail) for e in nullish(section.get("storyRefs"), [])):
        return None
    return section["summary"] if isinstance(section.get("summary"), str) and section["summary"] else None


# ── v1 shapes ───────────────────────────────────────────────────────────────────────────

_reports_index: dict[tuple[int, str], object] = {}


def report_index(conn_factory, kind: str):
    """The index rows and the withdrawn lead candidates, kept for a minute."""
    key = (id(conn_factory), kind)
    entry = _reports_index.get(key)
    if entry is None:

        def load(_arg=None):
            with conn_factory.connection() as conn:
                rows = report_index_rows(conn, kind, INDEX_LIMIT)
                return {"rows": rows, "gone": unavailable_headline_ids(conn, rows, "daily" if kind == "daily" else "periodic")}

        entry = _reports_index[key] = cached(load, 60_000, 60_000)
    return entry.get()  # type: ignore[attr-defined]


def v1_dailies(pool, limit: int) -> dict:
    index = report_index(pool, "daily")
    rows = index["rows"][:limit]
    with pool.connection() as conn:
        leads = daily_leads(conn, rows, index["gone"])
    items = []
    for r in rows:
        url = daily_url(r["key"])
        lead = leads.get(r["key"])
        items.append({"date": r["key"], "generatedAt": iso(r["generated_at"]), "leadTitle": lead["title"] if lead else None,
                      "leadParagraph": lead["leadParagraph"] if lead else None, "links": {"aihot": url}, "attribution": attribution(url)})  # fmt: skip
    return {"schemaVersion": 1, "count": len(items), "items": items}


def _fetch_report(conn, kind: str, key: str):
    if key == "latest":
        return conn.execute(
            "SELECT kind, key, window_start, window_end, content, generated_at FROM reports WHERE kind = %s ORDER BY key DESC LIMIT 1", (kind,)
        ).fetchone()
    return conn.execute(
        "SELECT kind, key, window_start, window_end, content, generated_at FROM reports WHERE kind = %s AND key = %s", (kind, key)
    ).fetchone()


def v1_daily(pool, date: str) -> dict | None:
    with pool.connection() as conn:
        r = _fetch_report(conn, "daily", date)
        if not r:
            return None
        c = r["content"]
        raw = [*[i for s in nullish(c.get("sections"), []) for i in nullish(s.get("items"), [])], *nullish(c.get("flashes"), [])]
        ids = sorted({i["itemId"] for i in [*raw, *[x for i in raw for x in nullish(i.get("related"), [])]] if truthy(i.get("itemId"))})
        avail = availability(conn, ids)

    def ok(i: dict) -> bool:
        return not truthy(i.get("itemId")) or avail.get(i["itemId"], {}).get("available", True)

    def metadata(i: dict) -> dict:
        return citation_metadata(i, avail.get(i.get("itemId")))

    def links(i: dict) -> dict:
        return {"aihot": item_url(i["itemId"]) if truthy(i.get("itemId")) else None, "original": metadata(i)["sourceUrl"]}

    url = daily_url(r["key"])
    gone = {i for i, a in avail.items() if not a["available"]}
    lead = issue_lead(c, "daily", gone) if truthy(c.get("lead")) or truthy(c.get("leadItemId")) else None
    return {
        "schemaVersion": 1,
        "report": {
            "date": r["key"], "generatedAt": iso(r["generated_at"]), "windowStart": iso(r["window_start"]), "windowEnd": iso(r["window_end"]),
            "links": {"aihot": url}, "attribution": attribution(url),
            "lead": {"title": lead["title"], "leadParagraph": nullish(lead["leadParagraph"], "")} if lead else None,
            "sections": [
                {"label": str(s.get("label")), "items": [
                    {"title": str(i.get("title")), "summary": str(nullish(metadata(i)["summary"], "")), "source": {"name": metadata(i)["sourceName"]}, "links": links(i),
                     "attribution": attribution(item_url(i["itemId"]) if truthy(i.get("itemId")) else url)}
                    for i in nullish(s.get("items"), []) if ok(i)]}
                for s in nullish(c.get("sections"), [])
            ],  # fmt: skip
            "flashes": [
                {"title": str(i.get("title")), "source": {"name": metadata(i)["sourceName"]}, "links": links(i),
                 "publishedAt": metadata(i)["publishedAt"] or iso(r["generated_at"]), "attribution": attribution(item_url(i["itemId"]) if truthy(i.get("itemId")) else url)}
                for i in nullish(c.get("flashes"), []) if ok(i)
            ],  # fmt: skip
        },
    }  # fmt: skip


def period_of(kind: str, key: str) -> dict:
    """A weekly's or monthly's own key and calendar days: { week, periodStart, periodEnd } or { month, … }."""
    rng = iso_week_range(key) if kind == "weekly" else month_range(key)
    return {("week" if kind == "weekly" else "month"): key, "periodStart": rng["start"] if rng else None, "periodEnd": rng["end"] if rng else None}


def is_period_key(kind: str, key: str) -> bool:
    """Whether a weekly or monthly key is a real ISO week (2026-W39) or calendar month (2026-09)."""
    return (iso_week_range(key) if kind == "weekly" else month_range(key)) is not None


def v1_periods(pool, kind: str, limit: int) -> dict:
    """The v1 index of weeklies or monthlies, newest first: key, days, headline and link."""
    index = report_index(pool, kind)
    items = []
    for r in index["rows"][:limit]:
        url = period_url(kind, r["key"])
        lead = issue_lead(r["content"], "periodic", index["gone"])
        items.append({**period_of(kind, r["key"]), "generatedAt": iso(r["generated_at"]), "headline": lead["title"] if lead else None, "links": {"aihot": url},
                      "attribution": attribution(url)})  # fmt: skip
    return {"schemaVersion": 1, "count": len(items), "items": items}


def v1_period(pool, kind: str, key: str) -> dict | None:
    """A weekly or monthly in v1: its headline, overview and sections, each with its introduction and the items it carries in order."""
    with pool.connection() as conn:
        r = _fetch_report(conn, kind, key)
        if not r:
            return None
        c = r["content"]
        raw = [x for t in nullish(c.get("themes"), []) for x in nullish(t.get("storyRefs"), [])]
        avail = availability(conn, sorted({i["itemId"] for i in raw if truthy(i.get("itemId"))}))
    gone = {i for i, a in avail.items() if not a["available"]}
    url = period_url(kind, r["key"])

    def ok(i: dict) -> bool:
        return not truthy(i.get("itemId")) or avail.get(i["itemId"], {}).get("available", True)

    sections = []
    for t in nullish(c.get("themes"), []):
        items = []
        for i in nullish(t.get("storyRefs"), []):
            if not ok(i):
                continue
            m = citation_metadata(i, avail.get(i.get("itemId")))
            items.append({"title": str(i.get("title")), "summary": str(nullish(m["summary"], "")), "source": {"name": m["sourceName"]},
                          "links": {"aihot": item_url(i["itemId"]) if truthy(i.get("itemId")) else None, "original": m["sourceUrl"]}, "publishedAt": m["publishedAt"],
                          "attribution": attribution(item_url(i["itemId"]) if truthy(i.get("itemId")) else url)})  # fmt: skip
        if items:
            sections.append({"label": str(t.get("heading")), "summary": section_summary(t, avail), "items": items})
    lead = issue_lead(c, "periodic", gone)
    return {
        "schemaVersion": 1,
        "report": {
            **period_of(kind, r["key"]),
            "generatedAt": iso(r["generated_at"]),
            "windowStart": iso(r["window_start"]),
            "windowEnd": iso(r["window_end"]),
            "links": {"aihot": url},
            "attribution": attribution(url),
            "headline": lead["title"] if lead else None,
            "overview": period_overview(c, kind, gone),
            "sections": sections,
        },  # fmt: skip
    }


__all__ = ["is_valid_date"]  # the route validates dates with the time module
