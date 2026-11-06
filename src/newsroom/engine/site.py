"""Small site-wide facts for the web shell: meta, changelog, about-page figures, contact codes and the starred-items check. Ports of
site/{meta,stats,contact}.ts and publication/availability.ts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from newsroom.engine import pack
from newsroom.engine.config import REPO_ROOT
from newsroom.engine.lib.cache import Cached, cached
from newsroom.engine.media.imgproxy import proxied_image
from newsroom.engine.publication.scope import scope_params, selected_condition
from newsroom.engine.publication.stories import public_source_name

SAMPLE = 180  # how many sources the about page's river draws, at most
_changelog: list[dict] = []


def load_changelog() -> dict:
    """Changelog is published as a data file of the site (site/changelog.json), newest first."""
    if not _changelog:
        _changelog.append(json.loads((REPO_ROOT / "site" / "changelog.json").read_text(encoding="utf-8")))
    return _changelog[0]


def site_meta() -> dict:
    return {"changelogVersion": load_changelog()["latestVersion"]}


def has_item_page(visibility: str, source_mode: str) -> bool:
    """An item page exists for every public or summary-only report of an editorial source."""
    return visibility != "withdrawn" and source_mode == "editorial"


def item_availability(conn, ids: list[str]) -> dict[str, dict]:
    """Starred items live in the browser; this tells the page which ones are still public, and the source name a public one shows today."""
    clean = list(dict.fromkeys(i for i in ids if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", i)))[:500]
    out = {i: {"status": "unavailable"} for i in clean}
    if not clean:
        return out
    rows = conn.execute(
        "SELECT p.article_id AS id, p.visibility, s.participation_mode AS source_mode, s.name AS source_name FROM publications p JOIN sources s ON s.id = p.source_id "
        "WHERE p.article_id = ANY(%s)",
        (clean,),
    ).fetchall()
    for r in rows:
        if not has_item_page(r["visibility"], r["source_mode"]):
            continue
        out[r["id"]] = {
            "status": "summary-only" if r["visibility"] == "summary-only" else "public",
            "sourceName": public_source_name(r["source_name"]),
        }
    return out


def _query_site_stats(pool, now: datetime) -> dict:
    day_ago = now - timedelta(hours=24)
    params = {**scope_params(now), "day_ago": day_ago}
    with pool.connection() as conn:
        row = conn.execute(
            f"""SELECT (SELECT count(*) FROM sources WHERE enabled)::int AS sources,
                       (SELECT count(*) FROM publications p WHERE p.visibility <> 'withdrawn')::int AS items,
                       (SELECT count(*) FROM publications p WHERE {selected_condition()})::int AS selected,
                       (SELECT count(*) FROM reports WHERE kind = 'daily')::int AS dailies,
                       (SELECT count(*) FROM publications p WHERE p.visibility <> 'withdrawn' AND p.discovered_at > %(day_ago)s)::int AS collected,
                       (SELECT count(*) FROM publications p WHERE {selected_condition()} AND p.timeline_at > %(day_ago)s)::int AS "selectedDay" """,
            params,
        ).fetchone()
        kinds = conn.execute("SELECT kind, count(*)::int AS n FROM sources WHERE enabled GROUP BY kind ORDER BY kind").fetchall()
        # A shuffle that holds for the day, so the river keeps its sources between visits.
        sample = conn.execute(
            "SELECT name, kind, participation_mode = 'hot_signal' AS heat_only FROM sources WHERE enabled ORDER BY md5(id::text || %s), id LIMIT %s",
            (now.astimezone(UTC).strftime("%Y-%m-%d"), SAMPLE),
        ).fetchall()
        latest = conn.execute(
            f"""SELECT p.article_id AS id, p.title, s.name AS source FROM publications p JOIN sources s ON s.id = p.source_id
                WHERE {selected_condition()} ORDER BY p.timeline_at DESC, p.article_id DESC LIMIT 8""",
            params,
        ).fetchall()
    collected, selected_day = row.pop("collected"), row.pop("selectedDay")
    return {
        **row,
        "sourceKinds": {k["kind"]: k["n"] for k in kinds},
        "day": {"collected": collected, "selected": selected_day},
        "sampleSources": [{"name": public_source_name(s["name"]), "kind": s["kind"], "heatOnly": s["heat_only"]} for s in sample],
        "latest": [{**i, "source": public_source_name(i["source"])} for i in latest],
    }


_stats: dict[int, Cached[dict, Any]] = {}


def load_site_stats(pool) -> dict:
    """Figures for the about page, kept for ten minutes per process; an older copy is served while the counts are read again."""
    entry = _stats.get(id(pool))
    if entry is None:

        def load(_: Any = None) -> dict:
            return _query_site_stats(pool, datetime.now(UTC))

        entry = _stats[id(pool)] = cached(load, 10 * 60_000, 60 * 60_000)
    return entry.get()


def _pack_code(slot: str) -> str | None:
    """The pack's code for a slot, served under /contact/; none when the pack ships no such file."""
    directory = REPO_ROOT / "site" / "brand" / "contact"
    if not directory.exists():
        return None
    found = next(
        (f.name for f in sorted(directory.iterdir()) if f.name.startswith(f"qr-{slot}") and re.fullmatch(r"[\w.-]+\.(png|jpe?g|webp)", f.name)), None
    )
    return f"/contact/{found}" if found else None


def load_contact(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = 'contact_qr'").fetchone()
    return {"wechatQr": _pack_code("wechat"), "feishuQr": _pack_code("feishu"), **(row["value"] if row else {})}


def load_maker_avatar(conn) -> str | None:
    """The maker's avatar at 400px through the image proxy, or None without a maker source or icon."""
    maker = pack.site()["ABOUT"].get("maker")
    source_id = (maker or {}).get("avatarSourceId")
    if not source_id:
        return None
    row = conn.execute("SELECT icon_url FROM sources WHERE id = %s", (source_id,)).fetchone()
    icon = re.sub(r"_(normal|bigger|mini)(\.\w+)$", r"_400x400\2", row["icon_url"]) if row and row["icon_url"] else None
    return proxied_image(icon, "thumb")
