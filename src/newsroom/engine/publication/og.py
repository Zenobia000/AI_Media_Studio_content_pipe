"""What the share images show: only public title and summary metadata, under the same visibility rule as the item page. Port of
publication/og.ts."""

from __future__ import annotations

from newsroom.engine import pack
from newsroom.engine.publication.stories import public_source_name
from newsroom.engine.publication.v1 import iso, js_round
from newsroom.engine.site import has_item_page
from newsroom.engine.taxonomy import category_labels
from newsroom.engine.time import beijing_date


def load_item_share(pool, item_id: str) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT p.article_id AS id, p.title, p.summary, p.category, p.selected, p.score, p.timeline_at,
                 s.name AS source_name, s.participation_mode AS source_mode, p.visibility
               FROM publications p JOIN sources s ON s.id = p.source_id WHERE p.article_id = %s""",
            (item_id,),
        ).fetchone()
    if not row or not has_item_page(row["visibility"], row["source_mode"]):
        return None
    summary_only = row["visibility"] == "summary-only"
    return {
        "id": row["id"], "title": row["title"], "summary": row["summary"], "category": None if summary_only else row["category"],
        "selected": not summary_only and row["selected"],
        "score": None if summary_only or row["score"] is None else js_round(float(row["score"])),
        "timelineAt": iso(row["timeline_at"]), "source": {"name": public_source_name(row["source_name"])},
    }  # fmt: skip


def load_item_og_card(pool, item_id: str) -> dict | None:
    """The article card used by both HTTP responses and preparation before publishing a link."""
    item = load_item_share(pool, item_id)
    if not item:
        return None
    copy = pack.site()["ITEM_COPY"]
    labels = category_labels()
    return {
        "kicker": labels[item["category"]] if item["category"] else pack.with_subject("動態"),
        "title": item["title"],
        "subtitle": item["summary"],
        "meta": f"{item['source']['name']} · {beijing_date(item['timelineAt'])}",
        "badge": {"value": str(js_round(item["score"])), "label": "精選評分"} if item["selected"] and item["score"] is not None and copy["showScore"] else None,
    }  # fmt: skip
