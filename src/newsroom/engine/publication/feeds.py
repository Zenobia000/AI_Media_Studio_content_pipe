"""RSS feeds: port of publication/feeds.ts, the summary feeds (selected, all, by category) and the report feeds. The full-text feeds
(/feed/full.xml, /feed/full/category/*) inline sanitized, image-signed bodies and wait for the HTML half of the port.

GUID = article id (isPermaLink=false), <link> = the site's page, pubDate = source publication time (none when it is unknown). Summary feeds
never carry content:encoded. Titles come from the site's name and categories."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from newsroom.engine import pack
from newsroom.engine.config import config
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.text import escape_xml
from newsroom.engine.publication.reports import (
    INDEX_LIMIT,  # noqa: F401
    daily_leads,
    daily_url,
    entries_of,
    issue_lead,
    period_overview,
    period_url,
    truthy,
    unavailable_ids,
)
from newsroom.engine.publication.scope import browsed_condition, scope_params, seated_condition
from newsroom.engine.publication.stories import public_source_name
from newsroom.engine.publication.v1 import item_url
from newsroom.engine.taxonomy import categories, feed_category_label, public_api_category_keys, to_public_api_category

EDGE_CACHE_SECONDS = 300
ISSUE_NAME = {"daily": "日報", "weekly": "週報", "monthly": "月報"}
# Issues each report feed keeps: a month of dailies, a quarter of weeklies, a year of monthlies.
ISSUES_KEPT = {"daily": 30, "weekly": 12, "monthly": 12}


def feeds() -> dict[str, dict]:
    site = pack.site()
    name = site["SITE"]["name"]
    when = site["EDITION_WHEN"]
    noun = site["REPORTS"]["entry"]["noun"]
    floor = pack.load("selection")["SELECTION"]["browseFloor"]
    left_out = [*site["FEED_COPY"]["allLeavesOut"], "未審內容", "低相關條目", f"{floor} 分以下條目", "已合併重複條目"]
    base = {"edgeCacheSeconds": EDGE_CACHE_SECONDS}
    return {
        "selected": {"path": "/feed.xml", "title": f"{name} — 精選", "homePath": "/", "pollHintMinutes": 30, **base,
                     "description": f"最新 50 條 {name} 精選摘要，保留標題、站內閱讀與原文入口；需要閱讀器內全文可改訂 /feed/full.xml。"},
        "selected-full": {"path": "/feed/full.xml", "title": f"{name} — 精選全文", "homePath": "/", "pollHintMinutes": 30, **base,
                          "description": "與精選摘要相同的最新 50 條；僅對明確允許再分發的來源內聯正文，其餘仍提供摘要和閱讀入口。"},
        "all": {"path": "/feed/all.xml", "title": f"{name} — {pack.subject_after('全部', '動態')}", "homePath": "/all", "pollHintMinutes": 30, **base,
                "description": f"最近 7 天公開動態，按真實發布時間倒序；不含{'、'.join(left_out[:-1])}和{left_out[-1]}。"},
        "daily": {"path": "/feed/daily.xml", "title": f"{name} 日報", "homePath": "/daily", "pollHintMinutes": 30, **base,
                  "description": f"{name} {when['daily']}（北京時間）發布的精編日報，保留最近 30 期。"},
        "weekly": {"path": "/feed/weekly.xml", "title": f"{name} 週報", "homePath": "/weekly", "pollHintMinutes": 180, **base,
                   "description": f"{name} {when['weekly']}（北京時間）發布的週報：從上週每天的日報裡選出的{noun}，按欄目分好，附總述；保留最近 12 期。"},
        "monthly": {"path": "/feed/monthly.xml", "title": f"{name} 月報", "homePath": "/monthly", "pollHintMinutes": 360, **base,
                    "description": f"{name} {when['monthly']}（北京時間）發布的月報：從上個月每天的日報裡選出的{noun}，按欄目分好，附總述；保留最近 12 期。"},
    }  # fmt: skip


def feed_cache_control(kind: str) -> str:
    seconds = feeds()[kind]["edgeCacheSeconds"]
    return f"public, max-age={seconds}, s-maxage={seconds}, must-revalidate"


def is_feed_category(value: str) -> bool:
    return value in public_api_category_keys()


def cdata(s: str) -> str:
    cleaned = re.sub("[\u0000-\u0008\u000b\u000c\u000e-\u001f]", "", s.replace("]]>", "]]]]><![CDATA[>"))
    return f"<![CDATA[{cleaned}]]>"


_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def rfc822(d: datetime) -> str:
    """Date.prototype.toUTCString."""
    u = d.astimezone(UTC)
    return f"{_DAYS[u.weekday()]}, {u.day:02d} {_MONTHS[u.month - 1]} {u.year:04d} {u:%H:%M:%S} GMT"


def _author() -> str:
    return f"noreply@{urlparse(config().site_url).hostname}"  # RSS <author> needs an address: a no-reply one on the site's own domain


def channel(meta: dict, items: list[str]) -> str:
    name = pack.site()["SITE"]["name"]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{escape_xml(meta["title"])}</title>
    <link>{escape_xml(site_url(meta["homePath"]))}</link>
    <description>{escape_xml(meta["description"])}</description>
    <language>zh-CN</language>
    <atom:link href="{escape_xml(site_url(meta["selfPath"]))}" rel="self" type="application/rss+xml" />
    <ttl>{meta["ttl"]}</ttl>
    <generator>{escape_xml(f"{name} ({site_url('/agent')})")}</generator>
{chr(10).join(items)}
  </channel>
</rss>
"""


def item_xml(r: dict) -> str:
    name = pack.site()["SITE"]["name"]
    link = item_url(r["id"])
    summary = r["summary"] if r["summary"] is not None else ""
    description = f'<p>{escape_xml(summary)}</p>\n<p>🔗 <a href="{escape_xml(r["url"])}">閱讀原文</a></p>\n<p>via {escape_xml(name)} · <a href="{link}">{link}</a></p>'
    public = to_public_api_category(r["category"])
    category = f"\n      <category>{escape_xml(feed_category_label(public))}</category>" if public else ""
    pub_date = f"\n      <pubDate>{rfc822(r['published_at'])}</pubDate>" if r["published_at"] else ""
    return f"""    <item>
      <title>{cdata(r["title"])}</title>
      <link>{link}</link>
      <description>{cdata(description)}</description>{category}{pub_date}
      <guid isPermaLink="false">{escape_xml(r["id"])}</guid>
      <author>{escape_xml(_author())} ({escape_xml(public_source_name(r["source_name"]))})</author>
    </item>"""


def _category_condition(category: str | None) -> tuple[str, dict]:
    if not category:
        return "", {}
    keys = [c["key"] for c in categories() if to_public_api_category(c["key"]) == category]
    return "AND p.category = ANY(%(feed_categories)s)", {"feed_categories": keys}


def item_feed(pool, kind: str, category: str | None, now: datetime | None = None) -> str:
    """Items are the newest by their original publish time (the pubDate shown): 50 per feed; a category feed holds only its last 7 days."""
    now = now or datetime.now().astimezone()
    params = scope_params(now)
    if kind == "all":
        scope = (
            f"{browsed_condition()} AND coalesce(p.published_at, p.discovered_at) > %(now)s::timestamptz - interval '7 days' "
            "AND coalesce(p.published_at, p.discovered_at) <= %(now)s"
        )
    else:
        cond, extra = _category_condition(category)
        params |= extra
        scope = f"{seated_condition()} {cond}"
        if category:
            scope += " AND coalesce(p.published_at, p.discovered_at) >= %(week_ago)s"
            params["week_ago"] = now - timedelta(days=7)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""WITH page AS MATERIALIZED (
                  SELECT p.article_id FROM publications p WHERE {scope}
                  ORDER BY coalesce(p.published_at, p.discovered_at) DESC, p.article_id DESC LIMIT 50
                )
                SELECT p.article_id AS id, p.title, p.summary, p.url, p.category, p.published_at, s.name AS source_name
                FROM page JOIN publications p ON p.article_id = page.article_id JOIN sources s ON s.id = p.source_id
                ORDER BY coalesce(p.published_at, p.discovered_at) DESC, p.article_id DESC""",
            params,
        ).fetchall()
    meta_src = feeds()[kind]
    meta = {k: meta_src[k] for k in ("title", "description", "homePath")} | {"selfPath": meta_src["path"], "ttl": meta_src["pollHintMinutes"]}
    if category:
        name = pack.site()["SITE"]["name"]
        label = feed_category_label(category)
        meta = {
            "title": f"{name} — {label}",
            "description": f"{name} 每日精選裡「{label}」這一類的摘要，按分類訂閱、不被全量精選刷屏。",
            "homePath": "/",
            "selfPath": f"/feed/category/{category}.xml",
            "ttl": meta_src["pollHintMinutes"],
        }
    return channel(meta, [item_xml(r) for r in rows])


def feed_issues(pool, kind: str, limit: int) -> list[dict]:
    """The newest issues of a kind for its RSS feed: headline, lead paragraph (a weekly's or monthly's overview) and each section's entry titles
    with their links on this site, withdrawn ones left out."""
    with pool.connection() as conn:
        if kind == "daily":
            rows = conn.execute(
                """SELECT key, generated_at, jsonb_build_object(
                     'lead', content->'lead', 'leadItemId', content->'leadItemId', 'highlights', content->'highlights',
                     'sections', (SELECT coalesce(jsonb_agg(jsonb_build_object('label', s->'label', 'items',
                       (SELECT coalesce(jsonb_agg(jsonb_build_object('itemId', i->'itemId', 'title', i->'title') ORDER BY ord), '[]'::jsonb)
                        FROM jsonb_array_elements(coalesce(s->'items', '[]'::jsonb)) WITH ORDINALITY AS e(i, ord))) ORDER BY sord), '[]'::jsonb)
                       FROM jsonb_array_elements(coalesce(content->'sections', '[]'::jsonb)) WITH ORDINALITY AS x(s, sord))) AS content
                   FROM reports WHERE kind = 'daily' ORDER BY key DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT key, generated_at, jsonb_build_object(
                     'headline', content->'headline', 'title', content->'title', 'overview', content->'overview',
                     'leadItemId', content->'leadItemId', 'highlights', content->'highlights',
                     'themes', (SELECT coalesce(jsonb_agg(jsonb_build_object('heading', t->'heading', 'storyRefs',
                       (SELECT coalesce(jsonb_agg(jsonb_build_object('itemId', i->'itemId', 'title', i->'title') ORDER BY ord), '[]'::jsonb)
                        FROM jsonb_array_elements(coalesce(t->'storyRefs', '[]'::jsonb)) WITH ORDINALITY AS e(i, ord))) ORDER BY tord), '[]'::jsonb)
                       FROM jsonb_array_elements(coalesce(content->'themes', '[]'::jsonb)) WITH ORDINALITY AS x(t, tord))) AS content
                   FROM reports WHERE kind = %s ORDER BY key DESC LIMIT %s""",
                (kind, limit),
            ).fetchall()
        shape = "daily" if kind == "daily" else "periodic"
        gone = unavailable_ids(conn, [i.get("itemId") for r in rows for i in entries_of(r["content"], shape)])
        leads = daily_leads(conn, rows, gone) if kind == "daily" else None
    out = []
    for r in rows:
        lead = leads.get(r["key"]) if leads is not None else issue_lead(r["content"], shape, gone)
        if kind == "daily":
            groups = [{"label": s.get("label"), "items": s.get("items")} for s in r["content"].get("sections") or []]
        else:
            groups = [{"label": t.get("heading"), "items": t.get("storyRefs")} for t in r["content"].get("themes") or []]
        sections = [
            {"label": str(g["label"]), "items": [{"title": str(i["title"]), "link": item_url(i["itemId"])} for i in g["items"] or [] if truthy(i.get("itemId")) and i["itemId"] not in gone]}
            for g in groups
        ]  # fmt: skip
        out.append({
            "key": r["key"], "generatedAt": r["generated_at"], "headline": lead["title"] if lead else None,
            "leadParagraph": (lead["leadParagraph"] if lead else None) if kind == "daily" else period_overview(r["content"], kind, gone),
            "sections": [s for s in sections if s["items"]],
        })  # fmt: skip
    return out


def issue_xml(kind: str, r: dict) -> str:
    name = pack.site()["SITE"]["name"]
    url = daily_url(r["key"]) if kind == "daily" else period_url(kind, r["key"])
    label = f"{name} {ISSUE_NAME[kind]}"
    title = f"{label} · {r['key']} — {r['headline']}" if r["headline"] else f"{label} · {r['key']}"
    contents = [
        f"<p><strong>{escape_xml(s['label'])}</strong></p>\n<ul>"
        + "".join(f'<li><a href="{escape_xml(i["link"])}">{escape_xml(i["title"])}</a></li>' for i in s["items"])
        + "</ul>"
        for s in r["sections"]
    ]
    lead = r["leadParagraph"] if r["leadParagraph"] is not None else (r["headline"] if r["headline"] is not None else "")
    description = "\n".join([f"<p>{escape_xml(lead)}</p>", *contents, f'<p>via {escape_xml(name)} · <a href="{url}">{url}</a></p>'])
    return f"""    <item>
      <title>{cdata(title)}</title>
      <link>{url}</link>
      <description>{cdata(description)}</description>
      <pubDate>{rfc822(r["generatedAt"])}</pubDate>
      <guid isPermaLink="false">{kind}-{escape_xml(r["key"])}</guid>
      <author>{escape_xml(_author())} ({escape_xml(name)})</author>
    </item>"""


def report_feed(pool, kind: str) -> str:
    m = feeds()[kind]
    meta = {"title": m["title"], "description": m["description"], "homePath": m["homePath"], "selfPath": m["path"], "ttl": m["pollHintMinutes"]}
    return channel(meta, [issue_xml(kind, r) for r in feed_issues(pool, kind, ISSUES_KEPT[kind])])
