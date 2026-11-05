"""Selected-item pushes to the content groups. One card per new fact: a short same-title lease holds back concurrent duplicates until grouping settles the fact, and the
fact (or the article when it has none) is the dedupe identity per target. Old, backfilled or silenced items are never pushed. Port of notify/selected.ts and
jobs/notify.ts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import regex

from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.ids import sha256
from newsroom.engine.media.prepare import warm_share_image
from newsroom.engine.notify_deliver import deliver_content
from newsroom.engine.notify_selected_content import selected_content

LEASE = timedelta(minutes=10)
MAX_RETRIES = 6


def normalized_title(t: str) -> str:
    return regex.sub(r"[\s\p{P}\p{S}]+", "", t.lower())


def push_selected(pool, article_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    content = selected_content(pool, article_id, now)
    if content["status"] != "ready":
        return content
    r = content["article"]
    # Same-title lease: a concurrent report with the same headline waits for grouping.
    lease_key = f"selected-title:{sha256(normalized_title(r['title']))[:24]}"
    with pool.connection() as conn:
        lease = conn.execute(
            """INSERT INTO delivery_leases (lease_key, holder, expires_at) VALUES (%(key)s, %(holder)s, %(until)s)
               ON CONFLICT (lease_key) DO UPDATE SET holder = CASE WHEN delivery_leases.expires_at < %(now)s THEN EXCLUDED.holder ELSE delivery_leases.holder END,
                 expires_at = CASE WHEN delivery_leases.expires_at < %(now)s THEN EXCLUDED.expires_at ELSE delivery_leases.expires_at END
               RETURNING holder""",
            {"key": lease_key, "holder": article_id, "until": now + LEASE, "now": now},
        ).fetchone()
    if lease and lease["holder"] != article_id and not r["fact_id"]:
        return {"status": "retry", "after": now + timedelta(minutes=2), "reason": "same title in flight"}
    dedupe_key = f"selected:fact:{r['fact_id']}" if r["fact_id"] else f"selected:article:{article_id}"
    # Reports grouped into this fact after one of them was sent under an earlier fact still count as sent: a sent or uncertain sibling among the current members stops
    # the push.
    siblings = []
    if r["fact_id"]:
        with pool.connection() as conn:
            siblings = [
                x["article_id"]
                for x in conn.execute(
                    "SELECT article_id FROM fact_articles WHERE fact_id = %s AND article_id <> %s", (r["fact_id"], article_id)
                ).fetchall()
            ]
    targets = deliver_content(
        pool,
        {
            "subjectKind": "selected",
            "subjectId": article_id,
            "dedupeKey": dedupe_key,
            "contentAt": r["discovered_at"],
            "card": content["card"],
            "siblings": siblings,
        },
    )
    out = {"status": "pushed" if any(t["status"] == "sent" for t in targets) else "skipped", "targets": targets}
    if not targets:
        out["reason"] = "no new target"
    return out


def run_notify_selected(pool, data: dict) -> dict:
    """The queue's handler: the push makes chat apps unfurl the link, so its share image is made first (first attempt only)."""
    attempt = data.get("attempt") or 0
    if not attempt:
        warm_share_image(pool, data["articleId"])
    outcome = push_selected(pool, data["articleId"])
    if outcome["status"] == "retry" and attempt < MAX_RETRIES:
        enqueue(pool, QUEUES["notifySelected"], {"articleId": data["articleId"], "attempt": attempt + 1}, start_after=outcome["after"])
    return {**outcome, "after": outcome["after"].isoformat()} if "after" in outcome else outcome
