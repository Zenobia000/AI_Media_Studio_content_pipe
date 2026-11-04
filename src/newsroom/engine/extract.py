"""Article page reading. Port of content/extract.ts, so far the sign-in tests the listings need."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Sign-in paths read from the final address, never the text; a segment only counts when it ends at a delimiter (a real page path or an extension).
AUTH_PATH = re.compile(
    r"(?:^|/)(?:login|log-in|log_in|signin|sign-in|sign_in|register|signup|sign-up|sign_up|authorize|oauth2?|sso|auth|wp-login(?:\.php)?)(?:/|$|\.(?:php|aspx?|html?|jsp))",
    re.I,
)


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def is_sign_in_url(url: str) -> bool:
    """Whether an address is a sign-in form's own path (a listing's "Log in" link is no post either)."""
    try:
        return AUTH_PATH.search(_path(url)) is not None
    except ValueError:
        return False


def is_sign_in_redirect(asked: str, landed: str) -> bool:
    """A redirect that ended on a sign-in form: read from the final address of the fetch, never from the text."""
    try:
        return AUTH_PATH.search(_path(asked)) is None and AUTH_PATH.search(_path(landed)) is not None
    except ValueError:
        return False


def page_fetchable(url: str, source_kind: str) -> bool:
    """Ordinary article pages; player pages have no article body, and social posts arrive separately."""
    from newsroom.engine.lib.video_url import is_video_page_url

    if source_kind in ("x_search", "mp_account") or is_video_page_url(url):
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.hostname) and re.search(r"(^|\.)(x\.com|twitter\.com|mp\.weixin\.qq\.com)$", u.hostname, re.I) is None


# ── fetching and storing an article's body ─────────────────────────────────────────────────────────────────────────────────────────────────────


def extract_from_url(url: str, rules=None, paid_fallback: bool = True):
    """The article an address serves, read under the source's rules. A page that does not read as an article falls back to the paid browser renderer
    (Jina) in the original; that fallback is not ported, so such a page is None here."""
    from newsroom.engine.lib.http_fetch import guarded_fetch, language_headers
    from newsroom.engine.lib.video_url import is_video_page_url
    from newsroom.engine.page_reader import PageRules, readable

    rules = rules or PageRules()
    if is_video_page_url(url):
        return None
    try:
        res = guarded_fetch(url, headers=language_headers(rules.accept_language), timeout_ms=20_000, max_bytes=16 * 1024 * 1024)
        if is_video_page_url(res.url) or is_sign_in_redirect(url, res.url):
            return None
        if res.status == 200 and "html" in (res.headers.get("content-type") or ""):
            got = readable(res.text(), res.url, rules)
            if got:
                return got
    except Exception:
        pass  # falls through to the paid fallback
    return None


def _mark_unconfirmed(conn, article_id: str, revision: int) -> str:
    row = conn.execute(
        "UPDATE articles SET body_status = 'unconfirmed', updated_at = now() WHERE id = %s AND revision = %s AND body_status <> 'ok' RETURNING id",
        (article_id, revision),
    ).fetchone()
    return "unconfirmed" if row else "skipped"


def extract_article_body(pool, article_id: str) -> str:
    """Fetches and stores the body of one article. Unconfirmed bodies are recorded as such. Returns "ok", "unconfirmed" or "skipped"."""
    from psycopg.types.json import Jsonb

    from newsroom.engine.materials import content_hash, fill_publication_time, revise_material, update_presentation
    from newsroom.engine.page_reader import page_rules

    with pool.connection() as conn:
        a = conn.execute(
            """SELECT a.id, a.url, a.body_status, a.revision, a.x_post, s.config FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s""",
            (article_id,),
        ).fetchone()
    if not a or a["body_status"] == "ok":
        return "skipped"
    if (a["x_post"] or {}).get("tweetId"):
        raise NotImplementedError("the X Article of a post needs SocialData, which is not ported yet")
    got = extract_from_url(a["url"], page_rules(a["config"]))
    with pool.connection() as conn:
        if not got:
            return _mark_unconfirmed(conn, article_id, a["revision"])
        # The body is new content: a new revision, so an analysis of the body-less input counts as stale.
        with conn.transaction():
            row = conn.execute(
                """SELECT title, excerpt, content_hash, media FROM articles WHERE id = %s AND revision = %s AND body_status <> 'ok' FOR UPDATE""",
                (article_id, a["revision"]),
            ).fetchone()
            if not row:
                return "skipped"
            authoritative = (a["config"].get("detail") or {}).get("publishedAtAuthoritative") is True
            time = None if authoritative else fill_publication_time(conn, article_id, got.published_at, publication_time=got.publication_time)
            h = content_hash(row["title"], got.text, row["excerpt"])
            if not time and h == row["content_hash"]:
                update_presentation(conn, article_id, got.html, row["media"] if row["media"] else got.images)
                conn.execute("UPDATE articles SET body_status = 'ok', updated_at = now() WHERE id = %s", (article_id,))
                return "ok"
            revise_material(
                conn, article_id,
                """body_html = %s, body_text = %s, body_status = 'ok',
                   media = CASE WHEN jsonb_array_length(media) = 0 THEN %s::jsonb ELSE media END""",
                (got.html, got.text, Jsonb(got.images)), h, row["title"], got.text,
            )  # fmt: skip
            return "ok"
