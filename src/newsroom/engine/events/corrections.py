"""Manual grouping corrections, made from the admin or by an operations script: take a report out of its fact, put it into the fact an editor names, merge two stories, or
ask for a regroup. Each change is written under the article's row lock, which automatic grouping takes too before it writes, and is never undone by automatic grouping,
retries or later revisions (only an explicit regroup). Port of events/corrections.ts."""

from __future__ import annotations

from typing import Any

from newsroom.engine.audit import Conflict, audit
from newsroom.engine.events.digest import compose_story_digest
from newsroom.engine.events.group import record_signal, reset_automatic
from newsroom.engine.events.merge import merge_story_into
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.materials import GROUPING_RESET
from newsroom.engine.publication.publish import publish_article, publish_article_tx

_GROUPED = (
    "UPDATE articles SET grouped_at = now(), grouping_status = 'complete', grouping_receipt_id = NULL, grouping_error = NULL, selection_adds_value = true, "
    "selection_value_reason = NULL WHERE id = %s"
)


def _digest(pool, story_id: int) -> None:
    enqueue(pool, QUEUES["digest"], {"storyId": story_id}, singleton_key=f"story:{story_id}")


def detach_from_fact(pool, article_id: str, reason: str, actor: str) -> dict:
    """Takes an article out of its fact; it is shown on its own again and stays that way (automatic grouping, retries and later revisions do not re-attach it; an
    explicit regroup does). Its heat evidence leaves the old story, whose digest is rewritten."""
    with pool.connection() as conn, conn.transaction():
        # The grouping job writes under the same lock and reads this decision again before it does.
        conn.execute("SELECT 1 FROM articles WHERE id = %s FOR UPDATE", (article_id,))
        fact_ids = [r["fact_id"] for r in conn.execute("DELETE FROM fact_articles WHERE article_id = %s RETURNING fact_id", (article_id,)).fetchall()]
        story_rows = (
            conn.execute("SELECT DISTINCT story_id FROM facts WHERE id = ANY(%s) AND story_id IS NOT NULL", (fact_ids,)).fetchall()
            if fact_ids
            else []
        )
        signals = conn.execute("DELETE FROM story_signals WHERE article_id = %s RETURNING story_id", (article_id,)).fetchall()
        story_ids = list(dict.fromkeys(r["story_id"] for r in [*story_rows, *signals]))
        conn.execute(
            """INSERT INTO grouping_overrides (article_id, reason, actor) VALUES (%s, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET reason = EXCLUDED.reason, actor = EXCLUDED.actor, created_at = now()""",
            (article_id, reason, actor),
        )
        conn.execute(_GROUPED, (article_id,))
    publish_article(pool, article_id)
    # The fact's other reports may take a new reading-group anchor.
    if fact_ids:
        with pool.connection() as conn:
            others = conn.execute("SELECT DISTINCT article_id FROM fact_articles WHERE fact_id = ANY(%s)", (fact_ids,)).fetchall()
        for o in others:
            publish_article(pool, o["article_id"])
    for sid in story_ids:
        _digest(pool, sid)
    with pool.connection() as conn:
        audit(conn, actor, "content.detach", f"content:{article_id}", reason, {"facts": fact_ids, "stories": story_ids}, None)
    return {"detached": len(fact_ids)}


def move_to_fact(pool, article_id: str, fact_public_id: str, reason: str, actor: str) -> dict | None:
    """Puts an article into the fact an editor names, as one of its reports, and keeps it there: the membership is manual, so automatic grouping, retries and later
    revisions leave it alone. The article leaves the facts it was a report of (mentions stay); its heat evidence follows it to the fact's story, and a story left
    without any report redirects there."""
    with pool.connection() as conn, conn.transaction():
        # The grouping job writes under the same lock and reads this decision again before it does.
        article = conn.execute(
            "SELECT source_id, coalesce(published_at, discovered_at) AS at FROM articles WHERE id = %s FOR UPDATE", (article_id,)
        ).fetchone()
        if not article:
            return None
        target = conn.execute(
            "SELECT f.id, f.story_id FROM facts f JOIN stories st ON st.id = f.story_id WHERE f.public_id = %s AND st.merged_into IS NULL",
            (fact_public_id,),
        ).fetchone()
        if not target:
            raise Conflict("目標事實不存在，或它所在的事件已被合併")
        removed = conn.execute(
            "DELETE FROM fact_articles WHERE article_id = %s AND (role IN ('primary', 'report') OR fact_id = %s) RETURNING fact_id, evidence",
            (article_id, target["id"]),
        ).fetchall()
        facts = [int(r["fact_id"]) for r in removed if int(r["fact_id"]) != int(target["id"])]
        stories = (
            [
                int(r["story_id"])
                for r in conn.execute(
                    "SELECT DISTINCT story_id FROM facts WHERE id = ANY(%s) AND story_id IS NOT NULL AND story_id <> %s", (facts, target["story_id"])
                ).fetchall()
            ]
            if facts
            else []
        )
        conn.execute("DELETE FROM grouping_overrides WHERE article_id = %s", (article_id,))
        evidence = next((r["evidence"] for r in removed if r["evidence"]), None)
        conn.execute(
            "INSERT INTO fact_articles (fact_id, article_id, role, evidence, manual, created_at) VALUES (%s, %s, 'report', %s, true, %s)",
            (target["id"], article_id, evidence, article["at"]),
        )
        conn.execute("DELETE FROM story_signals WHERE article_id = %s AND story_id <> %s", (article_id, target["story_id"]))
        source = conn.execute("SELECT id, signal_group_id FROM sources WHERE id = %s", (article["source_id"],)).fetchone()
        record_signal(conn, target["story_id"], article_id, source, "editorial", article["at"])
        conn.execute(_GROUPED, (article_id,))
        moved: dict[str, Any] = {"fact": int(target["id"]), "story": int(target["story_id"]), "facts": facts, "stories": stories}
    publish_article(pool, article_id)
    # The old facts' other reports may take a new reading-group anchor.
    if moved["facts"]:
        with pool.connection() as conn:
            others = conn.execute("SELECT DISTINCT article_id FROM fact_articles WHERE fact_id = ANY(%s)", (moved["facts"],)).fetchall()
        for o in others:
            publish_article(pool, o["article_id"])
    for sid in moved["stories"]:
        with pool.connection() as conn:
            left = conn.execute(
                "SELECT 1 FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id WHERE f.story_id = %s AND fa.role IN ('primary', 'report') LIMIT 1",
                (sid,),
            ).fetchone()
        if not left and merge_story_into(pool, sid, moved["story"], f"報道已全部移走，舊地址跳到報道所在事件（最後一篇 {article_id}）", actor):
            continue
        _digest(pool, sid)
    _digest(pool, moved["story"])
    with pool.connection() as conn:
        audit(
            conn,
            actor,
            "content.move",
            f"content:{article_id}",
            reason,
            {"facts": moved["facts"], "stories": moved["stories"]},
            {"fact": moved["fact"], "story": moved["story"]},
        )
    return {"fact": moved["fact"], "story": moved["story"], "left": len(moved["facts"])}


def merge_stories(pool, from_id: int, into_id: int, reason: str, actor: str) -> dict:
    """Merges one story into another: facts move, the old public id keeps working as an alias."""
    if from_id == into_id:
        raise ValueError("cannot merge a story into itself")
    done = merge_story_into(pool, from_id, into_id, reason, actor)
    if done:
        return done
    with pool.connection() as conn:
        found = conn.execute("SELECT id FROM stories WHERE id IN (%s, %s)", (from_id, into_id)).fetchall()
    if len(found) < 2:
        raise ValueError("story not found")
    raise Conflict("兩個事件都必須是未合併的事件")


def rewrite_story_digest(pool, story_id: int, reason: str, actor: str) -> dict:
    """Writes one story's digest again from its current reports with the current prompt (after the digest prompt changed). Unchanged prompt and reports reuse the paid
    answer; the model call happens here."""
    with pool.connection() as conn:
        before = conn.execute("SELECT digest, version FROM stories WHERE id = %s AND merged_into IS NULL", (story_id,)).fetchone()
    if not before:
        raise ValueError("story not found")
    result = compose_story_digest(pool, story_id, rewrite=True)
    with pool.connection() as conn:
        audit(conn, actor, "story.rewrite-digest", f"story:{story_id}", reason, before, result)
    return result


def request_regroup(db, article_id: str, request_id: str) -> str | None:
    """An explicit regroup: it replaces an earlier "keep standalone" decision and drops the automatic membership and heat evidence now (manual memberships stay and
    still win), so until its grouping job decides again the report stands on its own and is evidence for no other. The same request id queues one job. `db` is a
    connection inside the caller's transaction, or a pool."""
    if not hasattr(db, "execute"):
        with db.connection() as conn, conn.transaction():
            return request_regroup(conn, article_id, request_id)
    # The grouping job writes under the same lock and reads the manual state again before it does.
    db.execute("SELECT 1 FROM articles WHERE id = %s FOR UPDATE", (article_id,))
    # (the previous stories only fed the site modules' articleChanged notification, which has no listener here)
    db.execute("DELETE FROM grouping_overrides WHERE article_id = %s", (article_id,))
    reset_automatic(db, article_id)
    db.execute(f"UPDATE articles SET {GROUPING_RESET} WHERE id = %s", (article_id,))
    publish_article_tx(db, article_id)
    return enqueue(db, QUEUES["group"], {"articleId": article_id, "regroup": True}, singleton_key=f"manual:group:{article_id}:{request_id}")
