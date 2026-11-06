"""The single entrance for new material from every channel (collectors, external reports, imports). It owns identity, revisions and the timeline
rule, so no entrance can bypass them. Port of content/materials.ts and content/provenance.ts.

`collect/materials.py` is the smaller version the manual candidates use; this one is the engine's, with revisions, the timeline rule and
publisher attribution. A presentation change that must carry a model translation along (content/presentation.ts rebaseTranslation) is not
ported: it raises, so a body whose layout changed under a translated article is never saved half-done."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from newsroom.core.urls import identity_key_for_url
from newsroom.engine.audit import audit
from newsroom.engine.lib.ids import new_article_id, stable_json
from newsroom.engine.lib.text import collapse_whitespace, decode_entities
from newsroom.engine.publication.publish import publish_article_tx
from newsroom.engine.publication.v1 import iso
from newsroom.engine.sanitize import resolve_url

STALE_ON_DISCOVERY = timedelta(hours=48)  # archived by source time, stays out of "today" and is never pushed
FUTURE_TOLERANCE = timedelta(hours=1)  # source times more than an hour in the future are not trusted
SCHEDULED_AHEAD = timedelta(hours=24)  # a scheduled post can be listed up to a day before the time its page states
UNKNOWN_TIME = "unknown-publication-time"
STALE = "stale-on-discovery"


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()  # JavaScript's `undefined`, where it differs from null


@dataclass
class Material:
    source_id: str
    url: str
    title: str
    via: str  # "fetch" | "ingest" | "import" | a module's name
    identity_key: str | None = None
    author: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    publication_time: Any = UNSET  # original precision: YYYY-MM-DD, an exact ISO timestamp, or unknown
    source_updated_at: datetime | None = None
    excerpt: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    body_status: str | None = None  # pending | ok | unconfirmed | none
    media: list | None = None
    x_post: dict | None = None
    raw: Any = UNSET
    discovered_at: datetime | None = None
    backfill: str | None = None  # explicit backfill: the reason
    id: str | None = None  # keep an existing id when importing history
    extra: dict = field(default_factory=dict)


@dataclass
class MaterialResult:
    article_id: str
    created: bool
    revised: bool
    backfill: bool
    processing_needed: bool = False  # provenance moved an unanalysed signal into editorial processing, without a material revision


@dataclass
class Timeline:
    published_at: datetime | None
    timeline_at: datetime
    backfill: bool
    backfill_reason: str | None


def decide_timeline(claimed: datetime | None, discovered_at: datetime, explicit: str | None = None, ahead: timedelta = FUTURE_TOLERANCE) -> Timeline:
    """The one timeline rule shared by every entrance."""
    published = claimed
    if published and published > discovered_at + ahead:
        published = None
    reason: str | None = None
    if explicit:
        reason = explicit
    elif not published:
        reason = UNKNOWN_TIME
    elif discovered_at - published > STALE_ON_DISCOVERY:
        reason = STALE
    backfill = reason is not None
    return Timeline(published, published if backfill and published else discovered_at, backfill, reason)


def explicit_backfill(reason: str | None) -> str | None:
    """The backfill a material was given on arrival (a first import, a reported backfill), apart from what its date made it."""
    return None if reason in (UNKNOWN_TIME, STALE) else reason


def is_historical(published_at: datetime | None, discovered_at: datetime, backfill: bool) -> bool:
    """History rather than news: a backfill whose source time is unknown or was already past the stale threshold when found."""
    return backfill and (not published_at or discovered_at - published_at > STALE_ON_DISCOVERY)


NEWS_CONDITION = f"(NOT a.backfill OR a.discovered_at - a.published_at <= make_interval(secs => {STALE_ON_DISCOVERY.total_seconds():g}))"
TEXT_SETTLED = "(a.body_status <> 'pending' OR a.processing_state <> 'new')"
GROUPING_RESET = """grouping_status = 'pending', grouped_at = NULL, grouping_receipt_id = NULL, grouping_error = NULL,
  selection_adds_value = NULL, selection_value_reason = NULL"""


def content_hash(title: str, body_text: str | None = None, excerpt: str | None = None) -> str:
    """Identity of semantic input; presentation changes preserve this revision and its paid decisions."""
    parts = [collapse_whitespace(title), collapse_whitespace(body_text or ""), collapse_whitespace(excerpt or "")]
    return hashlib.sha256("\u0001".join(parts).encode()).hexdigest()


def _same_barring_loss(a: str | None, b: str | None) -> bool:
    """Whether two renderings of a text differ only where a character was lost in transit: a U+FFFD on either side stands for any one character."""

    def chars(s: str | None) -> list[str]:
        return list(re.sub("�+", "�", collapse_whitespace(decode_entities(s or ""))))

    x, y = chars(a), chars(b)
    return len(x) == len(y) and all(c == d or c == "�" or d == "�" for c, d in zip(x, y, strict=True))


def identity_key_for(m: Material) -> str:
    if m.identity_key:
        return m.identity_key
    if m.x_post and m.x_post.get("tweetId"):
        return f"x:{m.x_post['tweetId']}"
    from_url = identity_key_for_url(m.url)
    if from_url:
        return from_url
    return f"src:{m.source_id}:{hashlib.sha256((m.url + chr(1) + m.title).encode()).hexdigest()[:32]}"


def reuse_external_material_identities(conn, source_id: str, candidates: list[Material]) -> list[Material]:
    """A publisher's stable entry ID survives a URL rename: reuse the earliest material for that ID within its source."""

    def external_id(raw) -> str | None:
        value = raw.get("externalId") if isinstance(raw, dict) else None
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(int(value)) if float(value).is_integer() else str(value)
        return None

    ids = list(dict.fromkeys(i for i in (external_id(c.raw) for c in candidates) if i is not None))
    if not ids:
        return candidates
    rows = conn.execute(
        """SELECT DISTINCT ON (raw->>'externalId') raw->>'externalId' AS external_id, identity_key
           FROM articles WHERE source_id = %s AND raw->>'externalId' = ANY(%s::text[])
           ORDER BY raw->>'externalId', discovered_at, id""",
        (source_id, ids),
    ).fetchall()
    known = {r["external_id"]: r["identity_key"] for r in rows}
    out = []
    for c in candidates:
        key = known.get(external_id(c.raw) or "")
        if key:
            c = Material(**{**c.__dict__, "identity_key": key})
        out.append(c)
    return out


# ── provenance ──────────────────────────────────────────────────────────────────────────


def _ownership_url(raw: str):
    try:
        u = urlsplit(resolve_url(raw))
    except ValueError:
        return None
    return u if u.scheme in ("http", "https") and not u.username and not u.password else None


def _origin(u) -> str:
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}"


def publisher_owns_url(source: dict, raw_url: str) -> bool:
    """Explicit ownership scopes also work for shared hosts (one repository or publication). Identity normalization folds www/http and strips
    tracking parameters, which is too broad for ownership: match the configured origin and path boundary."""
    url = _ownership_url(raw_url)
    if not url:
        return False
    cfg = source["config"]
    if cfg.get("publisherRequiresDiscovery") is True and not isinstance(cfg.get("publisherUrlPrefixes"), list):
        return False
    if isinstance(cfg.get("publisherUrlPrefixes"), list):
        scopes = [v for v in cfg["publisherUrlPrefixes"] if isinstance(v, str)]
    elif source["kind"] == "web_list" and isinstance(cfg.get("url"), str):
        scopes = [cfg["url"]]  # a site's configured listing owns only its own path, not all links it points to
    else:
        return False
    for raw in scopes:
        scope = _ownership_url(raw)
        if not scope or _origin(scope) != _origin(url) or scope.query or scope.fragment:
            continue
        prefix = scope.path.rstrip("/")
        if not prefix or url.path == prefix or url.path.startswith(prefix + "/"):
            return True
    return False


def reconcile_material_source(conn, article_id: str, observed: dict | None = None) -> bool:
    """The caller holds the article row lock. A provenance-only repair preserves the material revision."""
    article = conn.execute(
        """SELECT a.source_id, a.url, a.author, s.participation_mode,
             EXISTS (SELECT 1 FROM analyses an WHERE an.article_id = a.id AND an.input_revision = a.revision AND an.relevance IS NOT NULL) AS analyzed
           FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s FOR UPDATE OF a""",
        (article_id,),
    ).fetchone()
    if not article:
        return False
    candidates = conn.execute(
        """SELECT s.id, s.kind, s.config, s.participation_mode FROM sources s
           WHERE s.tier = 'T1'
             AND (s.config->'publisherRequiresDiscovery' IS DISTINCT FROM 'true'::jsonb
               OR EXISTS (SELECT 1 FROM article_discoveries d WHERE d.article_id = %(id)s AND d.source_id = s.id))
             AND (jsonb_typeof(s.config->'publisherUrlPrefixes') = 'array'
             OR (s.kind = 'web_list' AND NOT (s.config ? 'publisherUrlPrefixes')
               AND EXISTS (SELECT 1 FROM article_discoveries d WHERE d.article_id = %(id)s AND d.source_id = s.id)))""",
        {"id": article_id},
    ).fetchall()
    owned = [s for s in candidates if publisher_owns_url(s, article["url"])]
    if len(owned) != 1:
        return False
    publisher = owned[0]
    if publisher["id"] == article["source_id"]:
        return False
    # Aggregator submitters are not article authors. Keep a name only from the publisher discovery.
    author = ((observed or {}).get("author") or "").strip() or None if (observed or {}).get("sourceId") == publisher["id"] else None
    needs_processing = article["participation_mode"] != "editorial" and publisher["participation_mode"] == "editorial" and not article["analyzed"]
    extra = f", processing_state = 'new', processing_queued_at = NULL, {GROUPING_RESET}" if needs_processing else ""
    conn.execute(f"UPDATE articles SET source_id = %s, author = %s, updated_at = now(){extra} WHERE id = %s", (publisher["id"], author, article_id))
    audit(conn, "system", "article.attribution", f"article:{article_id}", "唯一 T1 原發信源與已驗證 URL 範圍一致（顯式配置或已觀察官網列表）",
          {"sourceId": article["source_id"], "author": article["author"]}, {"sourceId": publisher["id"], "author": author})  # fmt: skip
    # This changes attribution and the public seat, not the judgement or selection threshold.
    publish_article_tx(conn, article_id)
    return True


# ── storing ─────────────────────────────────────────────────────────────────────────────


def fill_publication_time(
    conn, article_id: str, claimed: datetime | None, ahead: timedelta = FUTURE_TOLERANCE, publication_time: Any = UNSET
) -> Timeline | None:
    """Fill a missing publication date from the original listing or page, against the first discovery time. Explicit imports stay backfills."""
    if not claimed:
        return None
    a = conn.execute("SELECT published_at, discovered_at, backfill_reason FROM articles WHERE id = %s", (article_id,)).fetchone()
    if not a or a["published_at"]:
        return None
    nxt = decide_timeline(claimed, a["discovered_at"], explicit_backfill(a["backfill_reason"]), ahead)
    shown = iso(claimed) if publication_time is UNSET else publication_time
    # A time still ahead is kept as the latest claim: a postponed post waits for its new time (fill_scheduled_publications).
    if not nxt.published_at:
        conn.execute(
            "UPDATE articles SET published_at_claim = %s, publication_time = %s WHERE id = %s AND published_at_claim IS DISTINCT FROM %s",
            (claimed, shown, article_id, claimed),
        )
        return None
    conn.execute(
        """UPDATE articles SET published_at = %s, published_at_claim = %s, publication_time = %s, timeline_at = %s, backfill = %s, backfill_reason = %s
           WHERE id = %s""",
        (nxt.published_at, claimed, shown, nxt.timeline_at, nxt.backfill, nxt.backfill_reason, article_id),
    )
    return nxt


def fill_scheduled_publications(pool, source_id: str) -> list[str]:
    """A source's posts listed before the time their page states (scheduled publication) were refused that time on arrival as future. Once it has
    come, and it was at most a day ahead of the listing, it is the publication time: filled as a new revision."""
    with pool.connection() as conn:
        due = conn.execute(
            """SELECT id FROM articles WHERE source_id = %s AND published_at IS NULL
                 AND published_at_claim > discovered_at + %s * interval '1 second'
                 AND published_at_claim <= discovered_at + %s * interval '1 second'
                 AND published_at_claim <= now() + %s * interval '1 second'""",
            (source_id, FUTURE_TOLERANCE.total_seconds(), SCHEDULED_AHEAD.total_seconds(), FUTURE_TOLERANCE.total_seconds()),
        ).fetchall()
    filled: list[str] = []
    for row in due:
        with pool.connection() as conn, conn.transaction():
            a = conn.execute(
                "SELECT title, body_text, excerpt, published_at_claim, publication_time FROM articles WHERE id = %s AND published_at IS NULL FOR UPDATE",
                (row["id"],),
            ).fetchone()
            if not a or not fill_publication_time(conn, row["id"], a["published_at_claim"], SCHEDULED_AHEAD, a["publication_time"]):
                continue
            revise_material(conn, row["id"], "title = title", (), content_hash(a["title"], a["body_text"], a["excerpt"]), a["title"], a["body_text"])
            filled.append(row["id"])
    return filled


def upsert_material(db, m: Material) -> MaterialResult:
    """Stores material. Existing identities get a discovery record, and a new revision only when the stored content really changes. Concurrent
    reports of the same material are serialised on the row, so every change gets its own revision number."""
    if hasattr(db, "execute"):
        with db.transaction():
            return _upsert(db, m)
    with db.connection() as conn, conn.transaction():
        return _upsert(conn, m)


def _lock_material(conn, identity_key: str) -> dict | None:
    return conn.execute(
        """SELECT a.id, a.source_id, a.revision, a.content_hash, a.backfill, a.published_at, a.publication_time, a.title, a.body_text, a.excerpt, s.participation_mode
           FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.identity_key = %s FOR UPDATE OF a""",
        (identity_key,),
    ).fetchone()


def _upsert(conn, m: Material) -> MaterialResult:
    identity_key = identity_key_for(m)
    discovered_at = m.discovered_at or datetime.now(UTC)
    title = collapse_whitespace(m.title)[:1000] or m.url
    t = decide_timeline(m.published_at, discovered_at, m.backfill)
    existing = _lock_material(conn, identity_key)
    if not existing:
        new_id = m.id or new_article_id()
        digest = content_hash(title, m.body_text, m.excerpt)
        shown = None
        if m.published_at:
            shown = iso(m.published_at) if m.publication_time is UNSET else m.publication_time
        inserted = conn.execute(
            """INSERT INTO articles (id, source_id, identity_key, url, title, author, language, published_at, published_at_claim, publication_time,
                 discovered_at, source_updated_at, timeline_at, backfill, backfill_reason, revision, content_hash, excerpt,
                 body_text, body_html, body_status, media, x_post, raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (identity_key) DO NOTHING RETURNING id""",
            (new_id, m.source_id, identity_key, m.url, title, m.author, m.language, t.published_at, m.published_at, shown, discovered_at, m.source_updated_at,
             t.timeline_at, t.backfill, t.backfill_reason, digest, m.excerpt, m.body_text, m.body_html, m.body_status or ("ok" if m.body_text else "pending"),
             Jsonb(m.media or []), Jsonb(m.x_post) if m.x_post else None, None if m.raw is UNSET else Jsonb(m.raw)),
        ).fetchone()  # fmt: skip
        if inserted:
            conn.execute(
                "INSERT INTO article_revisions (article_id, revision, content_hash, title, body_text) VALUES (%s, 1, %s, %s, %s)",
                (new_id, digest, title, m.body_text),
            )
            conn.execute(
                "INSERT INTO article_discoveries (article_id, source_id, via, discovered_at) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (new_id, m.source_id, m.via, discovered_at),
            )
            reconcile_material_source(conn, new_id, {"sourceId": m.source_id, "author": m.author})
            return MaterialResult(new_id, True, False, t.backfill)
        existing = cast(dict, _lock_material(conn, identity_key))
    conn.execute(
        "INSERT INTO article_discoveries (article_id, source_id, via, discovered_at) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (existing["id"], m.source_id, m.via, discovered_at),
    )
    unchanged = MaterialResult(existing["id"], False, False, existing["backfill"])
    # Configuration may have gained a verified publisher since this same discovery channel last saw the URL.
    if reconcile_material_source(conn, existing["id"], {"sourceId": m.source_id, "author": m.author}):
        if existing["participation_mode"] != "editorial":
            pending = conn.execute(
                """SELECT 1 FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s AND s.participation_mode = 'editorial'
                     AND NOT EXISTS (SELECT 1 FROM analyses an WHERE an.article_id = a.id AND an.input_revision = a.revision AND an.relevance IS NOT NULL)""",
                (existing["id"],),
            ).fetchone()
            if pending:
                unchanged.processing_needed = True
        return unchanged
    # Another source listing the same material (an aggregator, a translated mirror, a hot signal) is a discovery only.
    if existing["source_id"] != m.source_id:
        return unchanged
    # Recover source precision only when the new reading describes the stored instant.
    if (
        m.publication_time and m.publication_time is not UNSET and m.published_at and existing["published_at"] == m.published_at
        and (existing["publication_time"] is None or (len(existing["publication_time"]) == 10 and len(m.publication_time) > 10))
    ):  # fmt: skip
        conn.execute("UPDATE articles SET publication_time = %s WHERE id = %s", (m.publication_time, existing["id"]))
    time = None if existing["published_at"] else fill_publication_time(conn, existing["id"], m.published_at, FUTURE_TOLERANCE, m.publication_time)
    # What the row will hold after this report: a listing without body keeps the stored (extracted) body.
    body_text = m.body_text if m.body_text is not None else existing["body_text"]
    excerpt = m.excerpt if m.excerpt is not None else existing["excerpt"]
    nxt = content_hash(title, body_text, excerpt)
    if not time and existing["content_hash"] == nxt:
        update_presentation(conn, existing["id"], m.body_html, m.media)
        return unchanged
    if not time and existing["content_hash"] is None:
        # Imported history carries no hash of this form: the first report here records the baseline instead of a revision.
        conn.execute("UPDATE articles SET content_hash = %s, excerpt = coalesce(excerpt, %s) WHERE id = %s", (nxt, m.excerpt, existing["id"]))
        conn.execute(
            "INSERT INTO article_revisions (article_id, revision, content_hash, title, body_text) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (existing["id"], existing["revision"], nxt, title, body_text),
        )
        return unchanged
    # A version this article already had is no new material (listings that alternate between two renderings, pages that rotate promotions).
    seen = conn.execute("SELECT 1 FROM article_revisions WHERE article_id = %s AND content_hash = %s LIMIT 1", (existing["id"], nxt)).fetchone()
    if not time and seen:
        return unchanged
    # Nor is the stored version with other characters lost in transit, or with them restored.
    if (
        not time
        and _same_barring_loss(existing["title"], title)
        and _same_barring_loss(existing["body_text"], body_text)
        and _same_barring_loss(existing["excerpt"], excerpt)
    ):
        return unchanged
    media = Jsonb(m.media) if m.media is not None else None
    revise_material(
        conn,
        existing["id"],
        """title = %s, author = coalesce(%s, author), language = coalesce(%s, language), source_updated_at = %s, excerpt = coalesce(%s, excerpt),
           body_text = coalesce(%s, body_text), body_html = coalesce(%s, body_html),
           body_status = CASE WHEN %s::text IS NULL THEN body_status ELSE %s END,
           media = CASE WHEN %s::jsonb IS NULL THEN media ELSE %s::jsonb END, x_post = coalesce(%s, x_post)""",
        (title, m.author, m.language, m.source_updated_at, m.excerpt, m.body_text, m.body_html, m.body_text, m.body_status or "ok", media, media, Jsonb(m.x_post) if m.x_post else None),
        nxt, title, body_text,
    )  # fmt: skip
    return MaterialResult(existing["id"], False, True, time.backfill if time else existing["backfill"])


def update_presentation(conn, article_id: str, html: str | None, media: list | None) -> bool:
    """Caller holds the material lock. A presentation update preserves all semantic processing state."""
    a = conn.execute("SELECT revision, body_html, media FROM articles WHERE id = %s FOR UPDATE", (article_id,)).fetchone()
    if not a:
        return False
    new_html = html if html is not None else a["body_html"]
    new_media = media if media is not None else a["media"]
    if new_html == a["body_html"] and stable_json(new_media) == stable_json(a["media"]):
        return False
    if new_html != a["body_html"]:
        tr = conn.execute(
            "SELECT body_html, origin FROM translations WHERE article_id = %s AND lang = 'zh' AND revision = %s FOR UPDATE",
            (article_id, a["revision"]),
        ).fetchone()
        # A publisher's translation is independently supplied, so only model translations share the source layout.
        if tr and tr["origin"] == "model" and tr["body_html"]:
            from newsroom.engine.presentation import rebase_translation

            translated = rebase_translation(a["body_html"] or "", new_html or "", tr["body_html"])
            conn.execute("UPDATE translations SET body_html = %s WHERE article_id = %s AND lang = 'zh'", (translated, article_id))
    conn.execute("UPDATE articles SET body_html = %s, media = %s, updated_at = now() WHERE id = %s", (new_html, Jsonb(new_media), article_id))
    return True


def revise_material(conn, article_id: str, set_sql: str, set_params: tuple, hash_: str, title: str, body_text: str | None) -> None:
    """A new revision of stored material, from a report or from body extraction: `set_sql` writes the new content, and everything decided on the
    old content starts over (analysis and its retry count, grouping, the "adds value" check). The caller holds the row lock."""
    row = conn.execute(
        f"""UPDATE articles SET {set_sql}, revision = revision + 1, content_hash = %s, {GROUPING_RESET},
              processing_state = 'new', processing_attempts = 0, processing_retry_at = NULL, processing_error = NULL, processing_queued_at = NULL,
              updated_at = now()
            WHERE id = %s RETURNING revision""",
        (*set_params, hash_, article_id),
    ).fetchone()
    conn.execute(
        "INSERT INTO article_revisions (article_id, revision, content_hash, title, body_text) VALUES (%s, %s, %s, %s, %s)",
        (article_id, row["revision"], hash_, title, body_text),
    )
    # Only withdraw an existing projection; the first publication still belongs to completed analysis.
    if conn.execute("SELECT 1 FROM publications WHERE article_id = %s", (article_id,)).fetchone():
        publish_article_tx(conn, article_id)
