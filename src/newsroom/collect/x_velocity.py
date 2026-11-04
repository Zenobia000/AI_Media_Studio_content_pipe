"""How loud an X post is against its own account (COL-FR-009): its engagement divided by the median of the account's earlier posts, so a
big account's usual numbers do not count as news. The counts are the ones the search answered with when the post was collected
(articles.raw); no extra paid call is made."""

from __future__ import annotations

from statistics import median

MIN_HISTORY = 5  # posts of the account needed before its median means anything
HISTORY = 30  # earlier posts of the account the median is taken over


def engagement_of(raw) -> float | None:
    """Likes plus twice the reposts; None when the post carries neither count."""
    if not isinstance(raw, dict):
        return None
    like, repost = raw.get("favorite"), raw.get("retweet")
    like = like if isinstance(like, (int, float)) and not isinstance(like, bool) else None
    repost = repost if isinstance(repost, (int, float)) and not isinstance(repost, bool) else None
    if like is None and repost is None:
        return None
    return (like or 0) + 2 * (repost or 0)


def velocity_of(engagement: float | None, history: list[float]) -> float | None:
    """The post's engagement in multiples of the account's median; None with too little history or no counts."""
    if engagement is None or len(history) < MIN_HISTORY:
        return None
    return engagement / max(median(history), 1)


def x_velocities(conn, identity_keys: list[str]) -> dict[str, float]:
    """Velocity of each X post (identity key `x:<id>`) among the keys; posts it cannot grade are left out."""
    keys = [k for k in identity_keys if k.startswith("x:")]
    out: dict[str, float] = {}
    if not keys:
        return out
    targets = conn.execute(
        "SELECT identity_key, author, raw, discovered_at FROM articles WHERE identity_key = ANY(%s) AND author IS NOT NULL", (keys,)
    ).fetchall()
    authors = sorted({t["author"] for t in targets})
    if not authors:
        return out
    posts = conn.execute(
        """SELECT identity_key, author, raw, discovered_at FROM (
             SELECT identity_key, author, raw, discovered_at, row_number() OVER (PARTITION BY author ORDER BY discovered_at DESC) AS rn
             FROM articles WHERE author = ANY(%s) AND identity_key LIKE 'x:%%') t WHERE rn <= %s""",
        (authors, HISTORY + len(keys)),
    ).fetchall()
    for t in targets:
        history = [
            e
            for p in posts
            if p["author"] == t["author"] and p["identity_key"] != t["identity_key"] and p["discovered_at"] <= t["discovered_at"]
            for e in [engagement_of(p["raw"])][:1]
            if e is not None
        ][:HISTORY]
        v = velocity_of(engagement_of(t["raw"]), history)
        if v is not None:
            out[t["identity_key"]] = v
    return out
