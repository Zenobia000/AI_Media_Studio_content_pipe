"""Share images (1200×630 PNG) for pages, items, reports, topics and events; only public content gets a card, anything else is a real 404
(port of apps/api/src/routes/og.ts)."""

from __future__ import annotations

import re

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from newsroom.engine import pack
from newsroom.engine.config import config
from newsroom.engine.media.og import og_etag, poster_etag, render_og, render_poster_png
from newsroom.engine.publication.og import load_item_og_card, load_item_share
from newsroom.engine.publication.reports import load_report
from newsroom.engine.publication.site_story import load_story_detail
from newsroom.engine.publication.stories import resolve_story
from newsroom.engine.publication.topics import find_topic, topic_groups, topics
from newsroom.engine.taxonomy import category_labels
from newsroom.engine.time import beijing_date, iso_week_range, month_day

# Article, event and report images carry editable content. A reader may keep a copy for five minutes; shared caches are refreshed on publication
# changes. Brand images keep their own lifetime.
CONTENT_IMAGE_CACHE = "public, max-age=300, s-maxage=3600, must-revalidate"


def pages() -> dict[str, dict]:
    """The pages' share cards: the site's texts, and the topic count of the topic list."""
    out = dict(pack.site()["CARDS"])
    names = "、".join(g["name"] for g in topic_groups())
    out["topics"] = {"kicker": "主題", "title": f"{len(topics())} {pack.subject_after('個長期追蹤的', '方向')}", "subtitle": f"{names}。"}
    return out


def report_date_label(kind: str, key: str) -> str:
    if kind == "daily":
        return month_day(key)
    if kind == "monthly":
        return f"{key[:4]}年{int(key[5:7])}月"
    rng = iso_week_range(key)
    start, end = rng["start"], rng["end"]
    tail = f"{int(end[8:10])}日" if start[5:7] == end[5:7] else month_day(end)
    return f"{month_day(start)}—{tail}"


def not_found() -> Response:
    return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


def _matches(header: str | None, tag: str) -> bool:
    return any(re.sub(r"^W/", "", t.strip()) == tag for t in (header or "").split(","))


async def send(request: Request, card: dict, max_age: int, cache_control: str | None = None) -> Response:
    tag = f'"og-{og_etag(card)}"'
    headers = {"ETag": tag, "Cache-Control": cache_control or f"public, max-age={max_age}, s-maxage={max_age * 7}, stale-while-revalidate=86400"}
    if _matches(request.headers.get("if-none-match"), tag):
        return Response(status_code=304, headers=headers)
    return Response(await run_in_threadpool(render_og, card), media_type="image/png", headers=headers)


def register_og(app: FastAPI) -> None:
    @app.get("/og/site.png", include_in_schema=False)
    async def site_card(request: Request) -> Response:
        return await send(request, pages()["site"], 86400)

    @app.get("/og/pages/{file}", include_in_schema=False)
    async def page_card(request: Request, file: str) -> Response:
        card = pages().get(re.sub(r"\.png$", "", file))
        if not card or not file.endswith(".png"):
            return not_found()
        return await send(request, card, 86400)

    @app.get("/og/items/{file}", include_in_schema=False)
    async def item_card(request: Request, file: str) -> Response:
        if not file.endswith(".png"):
            return not_found()
        card = await run_in_threadpool(load_item_og_card, request.app.state.pool, file[:-4])
        if not card:
            return not_found()
        return await send(request, card, 3600, CONTENT_IMAGE_CACHE)

    # Phone share poster for an article (1080×1440), generated on first request and cached by content.
    @app.get("/og/posters/{file}", include_in_schema=False)
    async def poster(request: Request, file: str) -> Response:
        if not file.endswith(".png"):
            return not_found()
        d = await run_in_threadpool(load_item_share, request.app.state.pool, file[:-4])
        if not d:
            return not_found()
        copy = pack.site()["ITEM_COPY"]
        p = {
            "url": f"{config().site_url}/items/{d['id']}",
            "kicker": category_labels()[d["category"]] if d["category"] else pack.with_subject("動態"),
            "title": d["title"], "summary": d["summary"], "source": d["source"]["name"], "date": beijing_date(d["timelineAt"]),
            "score": d["score"] if d["selected"] and copy["showScore"] else None,
        }  # fmt: skip
        tag = f'"poster-{poster_etag(p)}"'
        headers = {"ETag": tag, "Cache-Control": CONTENT_IMAGE_CACHE}
        if _matches(request.headers.get("if-none-match"), tag):
            return Response(status_code=304, headers=headers)
        return Response(await run_in_threadpool(render_poster_png, p), media_type="image/png", headers=headers)

    @app.get("/og/reports/{kind}/{file}", include_in_schema=False)
    async def report_card(request: Request, kind: str, file: str) -> Response:
        if kind not in ("daily", "weekly", "monthly") or not file.endswith(".png"):
            return not_found()
        r = await run_in_threadpool(load_report, request.app.state.pool, kind, file[:-4])
        if not r:
            return not_found()
        count = sum(len(s["items"]) for s in r["sections"])
        names = {"daily": pack.with_subject("日報"), "weekly": pack.with_subject("週報"), "monthly": pack.with_subject("月報")}
        lead = r["lead"]
        unit = pack.site()["REPORTS"]["shareUnit"]
        card = {
            "kicker": f"{names[kind]} · {report_date_label(kind, r['key'])}",
            "title": lead["title"] if lead and lead.get("title") is not None else r["title"],
            "subtitle": lead["leadParagraph"] if lead and lead.get("leadParagraph") is not None else r["overview"],
            # A quiet day's issue has nothing to count.
            "meta": f"{count} {unit} · 約 {r['readingMinutes']} 分鐘讀完" if count > 0 else None,
        }  # fmt: skip
        return await send(request, card, 3600, CONTENT_IMAGE_CACHE)

    @app.get("/og/topics/{file}", include_in_schema=False)
    async def topic_card(request: Request, file: str) -> Response:
        t = find_topic(file[:-4]) if file.endswith(".png") else None
        if not t:
            return not_found()
        group = next((g["name"] for g in topic_groups() if g["key"] == t["group"]), "")
        return await send(request, {"kicker": f"主題 · {group}", "title": f"{t['name']} 最新動態", "subtitle": t["definition"]}, 86400)

    @app.get("/og/stories/{file}", include_in_schema=False)
    async def story_card(request: Request, file: str) -> Response:
        if not file.endswith(".png"):
            return not_found()

        def load():
            with request.app.state.pool.connection() as conn:
                found = resolve_story(conn, file[:-4])
            return load_story_detail(request.app.state.pool, found["storyId"]) if found["kind"] == "found" else None

        s = await run_in_threadpool(load)
        if not s:
            return not_found()
        rank = s["whyHot"]["rank"]
        card = {
            "kicker": f"熱點第 {rank} · 事件" if rank else "事件",
            "title": s["title"],
            "subtitle": s["latest"] if s["latest"] is not None else s["digest"],
            "meta": f"{s['sourceCount']} 個來源 · {s['reportCount']} 篇報道",
            "accent": "hot" if rank else "teal",
        }
        return await send(request, card, 3600, CONTENT_IMAGE_CACHE)
