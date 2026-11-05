"""Merging stories: facts and heat evidence move into the surviving story, and the old public id keeps answering as an alias. Editors merge from the admin;
grouping merges when two stories turn out to be one. Port of events/merge.ts."""

from __future__ import annotations

from newsroom.engine.audit import audit
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.publication.publish import publish_article


def merge_story_into(pool, from_id: int, into_id: int, reason: str, actor: str) -> dict | None:
    """None when missing or already merged, or when grouping would change an editor-confirmed identity."""
    if from_id == into_id:
        raise ValueError("cannot merge a story into itself")
    with pool.connection() as conn, conn.transaction():
        src = conn.execute("SELECT public_id, merged_into, origin FROM stories WHERE id = %s FOR UPDATE", (from_id,)).fetchone()
        dst = conn.execute("SELECT merged_into, origin FROM stories WHERE id = %s FOR UPDATE", (into_id,)).fetchone()
        if not src or not dst or src["merged_into"] or dst["merged_into"]:
            return None
        if actor == "grouping" and (src["origin"] == "manual" or dst["origin"] == "manual"):
            return None
        conn.execute("UPDATE facts SET story_id = %s, updated_at = now() WHERE story_id = %s", (into_id, from_id))
        # A report with evidence in both stories keeps one row: (story, article) is unique.
        conn.execute(
            """INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at)
               SELECT %s, article_id, participant_key, source_id, kind, observed_at FROM story_signals WHERE story_id = %s
               ON CONFLICT (story_id, article_id) DO NOTHING""",
            (into_id, from_id),
        )
        conn.execute("DELETE FROM story_signals WHERE story_id = %s", (from_id,))
        conn.execute("UPDATE stories SET merged_into = %s, version = version + 1, updated_at = now() WHERE id = %s", (into_id, from_id))
        conn.execute(
            """UPDATE stories SET version = version + 1, updated_at = now(),
                 latest_at = greatest(latest_at, (SELECT latest_at FROM stories WHERE id = %s)),
                 first_report_at = least(first_report_at, (SELECT first_report_at FROM stories WHERE id = %s)) WHERE id = %s""",
            (from_id, from_id, into_id),
        )
        conn.execute("INSERT INTO story_aliases (public_id, story_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (src["public_id"], into_id))
        articles = conn.execute(
            "SELECT DISTINCT fa.article_id FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE f.story_id = %s", (into_id,)
        ).fetchall()
    for a in articles:
        publish_article(pool, a["article_id"])
    enqueue(pool, QUEUES["digest"], {"storyId": into_id}, singleton_key=f"story:{into_id}")
    with pool.connection() as conn:
        audit(conn, actor, "story.merge", f"story:{from_id}", reason, None, {"into": into_id})
    return {"moved": len(articles)}
