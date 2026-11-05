"""X accounts renamed under us. A search by the old handle finds nothing and still succeeds, so a renamed account only looks quiet. Every account quiet for a day is looked
up by a stored post, whose author is the same account under its current handle (the newest post that still exists, of the last three: authors delete posts); a new handle
replaces the old one in the source, and the account is read again from its newest stored post, so nothing posted since is missed. Port of sources/x-renames.ts."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from newsroom.engine.audit import audit
from newsroom.engine.socialdata import get_tweet, private_account_refusal
from newsroom.engine.sources.account import X_HANDLE, x_handle
from newsroom.engine.sources.collect import collect_source

DAY_MS = 86400_000


def follow_x_renames(pool, now: float | None = None, pause_ms: float | None = None) -> dict:
    """`pause_ms` between lookups leaves room in the provider's per-minute budget for the searches."""
    from newsroom.engine.admin.sources import request_republish

    now = now if now is not None else time.time() * 1000
    window = datetime.fromtimestamp(now / 1000, UTC).strftime("%Y-%m-%d")
    with pool.connection() as conn:
        quiet = conn.execute(
            """SELECT s.id, s.name, s.config->>'query' AS query, a.tweet_ids
               FROM sources s CROSS JOIN LATERAL (
                 SELECT array_agg(tweet_id ORDER BY discovered_at DESC, published_at DESC NULLS LAST) AS tweet_ids, max(discovered_at) AS discovered_at FROM (
                   SELECT x_post->>'tweetId' AS tweet_id, discovered_at, published_at FROM articles WHERE source_id = s.id AND x_post ? 'tweetId'
                   ORDER BY discovered_at DESC, published_at DESC NULLS LAST LIMIT 3) newest) a
               WHERE s.enabled AND s.kind = 'x_search' AND s.config->>'query' ~* %s AND a.discovered_at < %s
               ORDER BY s.id""",
            (X_HANDLE.pattern, datetime.fromtimestamp((now - DAY_MS) / 1000, UTC)),
        ).fetchall()
    renamed: list[dict] = []
    private_accounts: list[str] = []
    lookups = 0
    for s in quiet:
        frm = x_handle({"query": s["query"]})
        to = None
        for tweet_id in s["tweet_ids"]:
            if lookups > 0 and pause_ms != 0:
                time.sleep((pause_ms if pause_ms is not None else 4000) / 1000)
            lookups += 1
            try:
                tweet = get_tweet(pool, tweet_id, "x_rename_check", f"source:{s['id']}", window)
                to = (tweet or {}).get("user", {}).get("screen_name")
            except Exception as error:
                # The refusal stays on its receipt; the unverified account is reported and the others go on.
                if not private_account_refusal(error):
                    raise
                private_accounts.append(s["id"])
                break
            if to:
                break
        if not to or to.lower() == frm.lower():
            continue
        # Only the handle is rewritten: a new display name or company alias needs judgement and is left to a person.
        pattern = re.compile(rf"(from:|@){re.escape(frm)}(?![A-Za-z0-9_])", re.I)
        query = pattern.sub(lambda m, to=to: f"{m.group(1)}{to}", s["query"])
        name = pattern.sub(lambda m, to=to: f"{m.group(1)}{to}", s["name"])
        with pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE sources SET config = jsonb_set(config, '{query}', to_jsonb(%s::text)), name = %s, updated_at = now() WHERE id = %s",
                (query, name, s["id"]),
            )
            audit(
                conn,
                "system",
                "source.update",
                f"source:{s['id']}",
                f"X 賬號 @{frm} 已改名 @{to}（已存帖子作者的現名）",
                {"name": s["name"], "config": {"query": s["query"]}},
                {"name": name, "config": {"query": query}},
            )
            request_republish(conn, s["id"], "system")
        read = collect_source(pool, s["id"], x_since=s["tweet_ids"][0])
        renamed.append({"sourceId": s["id"], "from": frm, "to": to, "found": read["found"]})
    return {"checked": len(quiet), "renamed": renamed, "privateAccounts": private_accounts}
