"""X accounts via SocialData search, newest first. Verified publishers include their own thread continuations; other replies stay excluded. Plain accounts share
length-bounded search shards. Port of sources/x.ts."""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from newsroom.engine.lib.ids import sha256
from newsroom.engine.lib.text import js_slice
from newsroom.engine.llm import js_length
from newsroom.engine.receipts import ProviderRejectedError
from newsroom.engine.socialdata import search_tweets, tweet_media, tweet_text
from newsroom.engine.sources.account import x_handle
from newsroom.engine.sources.rss import FetchError

MAX_PAGES = 10  # pages of new posts read per run after a watermark; a longer search continues in later runs
MAX_BACKLOG_PAGES = 10  # pages per run spent on older stretches left over from earlier runs
MAX_BACKLOG = 5  # stretches kept per source; beyond this the oldest is given up and reported
SHARDABLE = re.compile(
    r"^from:[A-Za-z0-9_]{1,15} -filter:replies\Z", re.I
)  # a query that can share a search: exactly "from:handle -filter:replies", newest first
SHARDABLE_SQL = "^from:[A-Za-z0-9_]{1,15} -filter:replies$"  # the same test in SQL, for the schedulers
SHARD_QUERY_MAX = 470  # SocialData refuses queries over 512 characters; the since_id watermark takes about 30 of them
SHARD_MAX_ACCOUNTS = 24
X_ARTICLE_LINK = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/i/article/[0-9]+", re.I)


def _avatar(url: str | None) -> str | None:
    return url.replace("_normal.", "_bigger.", 1) if url else None  # larger avatar than the default 48px thumbnail


def to_x_post(t: dict) -> dict:
    q = t.get("quoted_status")
    user = t["user"]
    return {
        "tweetId": t["id_str"], "authorName": user["name"], "handle": user["screen_name"], "avatarUrl": _avatar(user.get("profile_image_url_https")), "text": tweet_text(t),
        "lang": t.get("lang"), "replyTo": t.get("in_reply_to_status_id_str"), "media": tweet_media(t),
        "quoted": {"authorName": q["user"]["name"], "handle": q["user"]["screen_name"], "text": tweet_text(q), "url": f"https://x.com/{q['user']['screen_name']}/status/{q['id_str']}", "media": tweet_media(q)}
        if q else None,
    }  # fmt: skip


def links_x_article(text: str | None) -> bool:
    """The post links an X Article (long-form text posted on X): its body is fetched before judging."""
    return bool(X_ARTICLE_LINK.search(text or ""))


def only_x_article_link(text: str | None) -> bool:
    """Nothing but the article's link: without the article there is nothing to judge (under 30 characters left)."""
    return links_x_article(text) and js_length(X_ARTICLE_LINK.sub("", text or "").strip()) < 30


_MARKS = {
    "header-one": "# ",
    "header-two": "## ",
    "header-three": "### ",
    "blockquote": "> ",
    "unordered-list-item": "- ",
    "ordered-list-item": "1. ",
}


def x_article_text(article: dict) -> dict | None:
    """An X Article as plain text with Markdown-like headings, quotes and list marks; media blocks are left out."""
    lines = []
    for b in (article.get("content_state") or {}).get("blocks") or []:
        text = (b.get("text") or "").strip()
        if not text or b.get("type") == "atomic":
            continue
        lines.append(_MARKS.get(b.get("type") or "", "") + text)
    text = "\n\n".join(lines).strip()
    return {"title": (article.get("title") or "").strip(), "text": text} if text else None


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def tweet_to_candidate(t: dict) -> dict:
    post = to_x_post(t)
    first_line = next((line for line in post["text"].split("\n") if line.strip()), post["text"])
    quoted = f"\n\n【引用 @{post['quoted']['handle']}】{post['quoted']['text']}" if post["quoted"] else ""
    return {
        "url": f"https://x.com/{t['user']['screen_name']}/status/{t['id_str']}", "identity_key": f"x:{t['id_str']}",
        "title": js_slice(first_line, 0, 137) + "..." if js_length(first_line) > 140 else first_line, "author": t["user"]["screen_name"], "language": t.get("lang"),
        "published_at": _parse_time(t["tweet_created_at"]), "body_text": (post["text"] + quoted).strip(),
        # A linked X Article is the post's real body: extraction fetches it.
        "body_status": "pending" if links_x_article(post["text"]) else "ok", "x_post": post, "media": post.get("media") or [],
        "raw": {"favorite": t.get("favorite_count"), "retweet": t.get("retweet_count"), "views": t.get("views_count")},
    }  # fmt: skip


def window_key(now_ms: float | None = None) -> str:
    """Half-hour receipt window: a retried fetch in the same window reuses the paid response."""
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    ms = math.floor(now_ms / 1_800_000) * 1_800_000
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def read_x_search(pool, base: str, last_id: str | None, backlog: list[dict], subject: str, kind: str = "Latest") -> dict:
    """New posts since the watermark. The search is bounded by since_id, so every further page holds older posts that are still new to us. A search longer than one run's
    pages (a long outage) moves the watermark to the newest post and keeps its position; later runs read on from there until it reaches the old watermark, so nothing in
    between is skipped. Without a watermark (a source's very first fetch) one page is read: its import is bounded anyway."""
    backlog = [dict(b) for b in backlog]
    window = window_key()

    def search(query: str, cursor: str | None) -> dict:
        return search_tweets(pool, query, "source_fetch", subject, window, kind, cursor)

    all_: list[dict] = []
    receipt_ids: dict[int, None] = {}
    query = f"{base} since_id:{last_id}" if last_id else base
    cursor = None
    pages = 0
    truncated = False
    while True:
        try:
            res = search(query, cursor)
        except Exception:
            # A later page failing keeps the pages already read; the search goes on from there next run.
            if not cursor:
                raise
            truncated = True
            backlog.append({"query": query, "next": cursor})
            break
        pages += 1
        receipt_ids[res["receiptId"]] = None
        all_.extend(res["tweets"])
        if not last_id or not res["nextCursor"] or not res["tweets"]:
            break
        if pages >= MAX_PAGES:
            truncated = True
            backlog.append({"query": query, "next": res["nextCursor"]})
            break
        cursor = res["nextCursor"]

    # Older stretches, oldest first. Budget, account or passing trouble leaves them for the next run; a position the provider refuses as a bad request (an expired
    # cursor) is given up.
    dropped = 0
    while len(backlog) > MAX_BACKLOG:
        backlog.pop(0)
        dropped += 1
    backlog_pages = 0
    while backlog and backlog_pages < MAX_BACKLOG_PAGES:
        stretch = backlog[0]
        try:
            res = search(stretch["query"], stretch["next"])
        except Exception as error:
            if isinstance(error, ProviderRejectedError) and error.status in (400, 422):
                backlog.pop(0)
                dropped += 1
                continue
            break
        backlog_pages += 1
        receipt_ids[res["receiptId"]] = None
        all_.extend(res["tweets"])
        if not res["nextCursor"] or not res["tweets"]:
            backlog.pop(0)
        else:
            stretch["next"] = res["nextCursor"]

    seen: set[str] = set()
    tweets = []
    for t in all_:
        if t.get("retweeted_status") or t["id_str"] in seen:
            continue
        seen.add(t["id_str"])
        tweets.append(t)
    max_id = last_id
    for t in tweets:
        if max_id is None or int(t["id_str"]) > int(max_id):
            max_id = t["id_str"]
    return {
        "tweets": tweets,
        "receiptIds": list(receipt_ids),
        "lastId": max_id,
        "backlog": backlog,
        "pages": pages,
        "truncated": truncated,
        "backlogPages": backlog_pages,
        "dropped": dropped,
    }


def self_thread_handle(s: dict) -> str | None:
    """Verified publisher identity permits its own announcement thread, not conversations with others."""
    cfg = s["config"]
    if cfg.get("publisherRole") not in ("organization", "person"):
        return None
    return x_handle(cfg) if SHARDABLE.match(str(cfg.get("query") if cfg.get("query") is not None else "")) else None


def fetch_x_search(pool, source: dict, since: str | None = None) -> dict:
    """One account's own search (its first fetch, a query of its own, or a manual run from the admin), from its watermark or from an earlier post (`since`)."""
    handle = self_thread_handle(source)
    cfg = source["config"]
    base = f"from:{handle} (-filter:replies OR filter:self_threads)" if handle else str(cfg.get("query") if cfg.get("query") is not None else "")
    if not base:
        raise FetchError("query missing")
    cursor = source.get("cursor") or {}
    read = read_x_search(
        pool, base, since or cursor.get("lastTweetId") or None, cursor["xBacklog"] if isinstance(cursor.get("xBacklog"), list) else [], f"source:{source['id']}", cfg.get("searchType") or "Latest"
    )  # fmt: skip
    tweets = read.pop("tweets")
    return {"candidates": [tweet_to_candidate(t) for t in tweets], **read}


def shard_handle(s: dict) -> str | None:
    """The account a source reads, when it can share a search: a plain query and a watermark already set."""
    cfg, cursor = s["config"], s.get("cursor") or {}
    if s["kind"] != "x_search" or (cfg.get("searchType") or "Latest") != "Latest" or not cursor.get("lastTweetId"):
        return None
    return x_handle(cfg) if SHARDABLE.match(str(cfg.get("query") if cfg.get("query") is not None else "")) else None


def shard_query(handles: list[str], self_threads: list[str] | None = None) -> str:
    self_threads = self_threads or []
    posts = f"({' OR '.join('from:' + h for h in handles)}) -filter:replies"
    own = [h for h in handles if h in self_threads]
    return f"({posts} OR ({' OR '.join('from:' + h for h in own)}) filter:self_threads)" if own else posts


def plan_x_shards(sources: list[dict]) -> list[dict]:
    """Accounts that can share a search, per participation mode, in a stable order (by source id) and packed into queries under the length limit. The same sources give
    the same shards, so a shard's accounts stay together between runs; a source added or removed shifts only the shards after it."""
    by_mode: dict[str, list[dict]] = {}
    for s in sources:
        handle = shard_handle(s)
        if handle:
            by_mode.setdefault(s["participation_mode"], []).append({"id": s["id"], "handle": handle, "selfThread": bool(self_thread_handle(s))})
    shards = []
    for mode in sorted(by_mode):
        items = sorted(by_mode[mode], key=lambda x: x["id"])
        current: list[dict] = []

        def close(mode=mode, current=None):
            if current:
                shards.append(
                    {"mode": mode, "sourceIds": [c["id"] for c in current], "key": f"{mode}:{sha256(','.join(c['id'] for c in current))[:12]}"}
                )

        for s in items:
            trial = [*current, s]
            if (
                len(current) >= SHARD_MAX_ACCOUNTS
                or js_length(shard_query([c["handle"] for c in trial], [c["handle"] for c in trial if c["selfThread"]])) > SHARD_QUERY_MAX
            ):
                close(current=current)
                current = []
            current.append(s)
        close(current=current)
    return shards
