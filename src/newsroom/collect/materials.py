"""Putting an address into AIHOT's articles table, for the candidates a person adds by hand.

A deliberately smaller version of AIHOT's upsertMaterial (packages/backend/src/content/materials.ts): a new address becomes an article
with its first revision and discovery; one the site already has only gains a discovery. It does not revise stored text and does not
queue the article for AIHOT's model analysis and event grouping (that queue belongs to the TypeScript site); a hand-added candidate
is listed from manual_candidates and does not need either.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone

from newsroom.core.urls import identity_key_for_url

_ALPHABET = string.ascii_lowercase + string.digits
_SPACES = re.compile(r"\s+")


def new_article_id() -> str:
    """25 characters, starting with a letter, like the site's cuid2 ids."""
    return secrets.choice(string.ascii_lowercase) + "".join(secrets.choice(_ALPHABET) for _ in range(24))


def collapse(text: str | None) -> str:
    return _SPACES.sub(" ", text or "").strip()


def content_hash(title: str, body_text: str | None = None, excerpt: str | None = None) -> str:
    return hashlib.sha256("\u0001".join([collapse(title), collapse(body_text), collapse(excerpt)]).encode("utf-8")).hexdigest()


@dataclass
class MaterialResult:
    article_id: str
    created: bool


def upsert_manual_material(conn, source_id: str, url: str, title: str, via: str = "manual", now: datetime | None = None) -> MaterialResult:
    """The identity rule is the site's (core/urls.py); the timeline is a discovery now with no publication time (a backfill, reason
    unknown-publication-time), as the site gives any address that states none."""
    identity = identity_key_for_url(url) or f"src:{source_id}:{hashlib.sha256((url + chr(1) + title).encode()).hexdigest()[:32]}"
    discovered = now or datetime.now(timezone.utc)
    title = collapse(title)[:1000] or url
    with conn.transaction():
        row = conn.execute("SELECT id FROM articles WHERE identity_key = %s FOR UPDATE", (identity,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO article_discoveries (article_id, source_id, via, discovered_at) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (row["id"], source_id, via, discovered),
            )
            return MaterialResult(row["id"], False)
        new_id = new_article_id()
        digest = content_hash(title)
        inserted = conn.execute(
            """INSERT INTO articles (id, source_id, identity_key, url, title, published_at, discovered_at, timeline_at, backfill,
                 backfill_reason, revision, content_hash, body_status, media)
               VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, true, 'unknown-publication-time', 1, %s, 'pending', '[]'::jsonb)
               ON CONFLICT (identity_key) DO NOTHING RETURNING id""",
            (new_id, source_id, identity, url, title, discovered, discovered, digest),
        ).fetchone()
        if inserted is None:  # lost a race to another writer: use the winner
            winner = conn.execute("SELECT id FROM articles WHERE identity_key = %s", (identity,)).fetchone()
            return MaterialResult(winner["id"], False)
        conn.execute(
            "INSERT INTO article_revisions (article_id, revision, content_hash, title, body_text) VALUES (%s, 1, %s, %s, NULL)",
            (new_id, digest, title),
        )
        conn.execute(
            "INSERT INTO article_discoveries (article_id, source_id, via, discovered_at) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (new_id, source_id, via, discovered),
        )
        return MaterialResult(new_id, True)
