"""Source administration: list, detail, preview (fetch without storing), edit, create with duplicate checks, pause/resume and manual collection. Every
change is audited. Port of admin/sources.ts."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from newsroom.core.urls import normalize_url
from newsroom.engine import pack
from newsroom.engine.admin import validation as V
from newsroom.engine.audit import Conflict, audit, audit_history
from newsroom.engine.errors import BadRequest
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.jobs.sources import apply_use_pace
from newsroom.engine.materials import GROUPING_RESET
from newsroom.engine.publication.v1 import iso
from newsroom.engine.sources.account import x_handle
from newsroom.engine.sources.collect import enrich_details, listing_entries, stored_details
from newsroom.engine.sources.config_keys import UnsupportedConfig, assert_supported_config
from newsroom.engine.sources.filters import filter_publication_window
from newsroom.engine.sources.json_list import fetch_json_list
from newsroom.engine.sources.rss import FetchError, fetch_rss
from newsroom.engine.sources.web_list import fetch_web_list

REPLACEABLE_SOURCE_KINDS = ["rss", "web_list", "json_list"]
KINDS = ["rss", "web_list", "json_list", "x_search", "mp_account", "external"]
TIERS = ["T1", "T1_5", "T2", "EXCLUDE_MP"]
MODES = ["editorial", "hot_signal", "isolated"]
USE_LABELS: dict[str, str] = {}  # the uses installed modules declare (none are installed here)
PUBLICATION_FIELDS = ["kind", "participation_mode", "site_fulltext", "syndicate_fulltext", "tier", "name", "first_party", "owner_entity_id"]
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
EDITABLE_KEYS = [
    "name", "kind", "enabled", "interval_minutes", "tier", "selection_class", "participation_mode", "signal_group_id", "first_party", "owner_entity_id",
    "site_fulltext", "syndicate_fulltext", "tags", "uses", "config",
]  # fmt: skip
CREATE_KEYS = [
    "id", "name", "kind", "config", "tier", "selection_class", "participation_mode", "interval_minutes", "first_party", "tags", "uses", "site_fulltext", "syndicate_fulltext",
]  # fmt: skip


def selection_classes() -> list[str]:
    return list(pack.load("selection").get("SELECTION", {}).get("classes", {}))


def _selection_class(v, issues: V.Issues, path: str, nullable: bool = True):
    if v is None and nullable:
        return None
    if not isinstance(v, str):
        issues.add(path, f"Invalid input: expected string, received {V.received(v)}")
        return None
    if v not in selection_classes():
        issues.add(path, "不是行業包裡的信源類別")
    return v


def _uses(v, issues: V.Issues, path: str):
    if not isinstance(v, list):
        issues.add(path, f"Invalid input: expected array, received {V.received(v)}")
        return None
    bad_items = False
    for i, item in enumerate(v):
        if not isinstance(item, str):
            issues.add(f"{path}.{i}", f"Invalid input: expected string, received {V.received(item)}")
            bad_items = True
    if len(v) > 10:
        issues.add(path, "Too big: expected array to have <=10 items")
        return None
    if bad_items:
        return None
    if not all(u in USE_LABELS for u in v):
        issues.add(path, f"信源用途只能是已裝模組宣告的：{'、'.join(USE_LABELS) or '（沒有）'}")
        return None
    return list(dict.fromkeys(v))


def parse_patch(raw) -> dict:
    issues = V.Issues()
    data = V.object_input(raw, issues)
    out: dict = {}
    if data is not None:
        for key in EDITABLE_KEYS:
            if key not in data:
                continue
            v = data[key]
            if key == "name":
                out[key] = V.string(v, issues, key, min_len=1, max_len=200)
            elif key == "kind":
                out[key] = V.enum(v, issues, key, REPLACEABLE_SOURCE_KINDS)
            elif key in ("enabled", "first_party", "site_fulltext", "syndicate_fulltext"):
                out[key] = V.boolean(v, issues, key)
            elif key == "interval_minutes":
                out[key] = V.integer(v, issues, key, 1, 1440)
            elif key == "tier":
                out[key] = V.enum(v, issues, key, TIERS)
            elif key == "selection_class":
                out[key] = _selection_class(v, issues, key)
            elif key == "participation_mode":
                out[key] = V.enum(v, issues, key, MODES)
            elif key in ("signal_group_id", "owner_entity_id"):
                out[key] = None if v is None else V.string(v, issues, key, max_len=120)
            elif key == "tags":
                out[key] = V.string_array(v, issues, key, max_items=30, max_len=60)
            elif key == "uses":
                out[key] = _uses(v, issues, key)
            else:
                out[key] = V.record(v, issues, key)
        V.strict_keys(data, EDITABLE_KEYS, issues)
    issues.raise_if_any()
    return out


def parse_create(raw) -> dict:
    issues = V.Issues()
    data = V.object_input(raw, issues)
    out: dict = {}
    if data is not None:
        g = lambda k: data.get(k, V.MISSING)  # noqa: E731
        out["id"] = V.string(g("id"), issues, "id", pattern=ID_PATTERN)
        out["name"] = V.string(g("name"), issues, "name", min_len=1, max_len=200)
        out["kind"] = V.enum(g("kind"), issues, "kind", KINDS)
        out["config"] = V.record(g("config"), issues, "config")
        out["tier"] = V.enum(g("tier"), issues, "tier", TIERS) if "tier" in data else "T2"
        out["selection_class"] = _selection_class(g("selection_class"), issues, "selection_class") if "selection_class" in data else None
        out["participation_mode"] = V.enum(g("participation_mode"), issues, "participation_mode", MODES) if "participation_mode" in data else "editorial"
        out["interval_minutes"] = V.integer(g("interval_minutes"), issues, "interval_minutes", 1, 1440) if "interval_minutes" in data else 30
        out["first_party"] = V.boolean(g("first_party"), issues, "first_party") if "first_party" in data else False
        out["tags"] = V.string_array(g("tags"), issues, "tags") if "tags" in data else []
        out["uses"] = _uses(g("uses"), issues, "uses") if "uses" in data else []
        default_full = bool(pack.site().get("SOURCE_DEFAULTS", {}).get("siteFulltext", False))
        out["site_fulltext"] = V.boolean(g("site_fulltext"), issues, "site_fulltext") if "site_fulltext" in data else default_full
        out["syndicate_fulltext"] = V.boolean(g("syndicate_fulltext"), issues, "syndicate_fulltext") if "syndicate_fulltext" in data else False
        V.strict_keys(data, CREATE_KEYS, issues)
    issues.raise_if_any()
    return out


def _supported(kind: str, config: dict) -> None:
    try:
        assert_supported_config(kind, config)
    except UnsupportedConfig as e:
        raise BadRequest(str(e)) from None


# -- reading ----------------------------------------------------------------------------------------------------------------------------------


def list_sources(pool, f: dict) -> dict:
    page = max(1, f.get("page") or 1)
    q = f"%{f['q'].strip()}%" if f.get("q") and f["q"].strip() else None
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.kind, s.tier, s.selection_class, s.participation_mode, s.uses, s.enabled, s.health, s.fail_count, s.interval_minutes,
                   s.last_ok_at, s.last_fetch_at, s.last_error, (s.tier = 'T1') AS first_party, s.next_fetch_at,
                   (SELECT count(*)::int FROM articles a WHERE a.source_id = s.id AND a.discovered_at > now() - interval '7 days') AS items_7d,
                   (SELECT count(*)::int FROM publications p WHERE p.source_id = s.id AND p.selected AND p.discovered_at > now() - interval '30 days') AS selected_30d
            FROM (
              SELECT * FROM sources
              WHERE (%(q)s::text IS NULL OR name ILIKE %(q)s OR id ILIKE %(q)s OR config::text ILIKE %(q)s)
                AND (%(kind)s::text IS NULL OR kind = %(kind)s)
                AND (%(health)s::text IS NULL OR health = %(health)s)
                AND (%(mode)s::text IS NULL OR participation_mode = %(mode)s)
                AND (%(use)s::text IS NULL OR %(use)s = ANY(uses))
                AND (%(enabled)s::text IS NULL OR enabled = (%(enabled)s = 'true'))
              ORDER BY enabled DESC, CASE health WHEN 'failing' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END, name, id
              LIMIT 100 OFFSET %(offset)s
            ) s
            ORDER BY s.enabled DESC, CASE s.health WHEN 'failing' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END, s.name, s.id""",
            {"q": q, "kind": f.get("kind"), "health": f.get("health"), "mode": f.get("mode"), "use": f.get("use"), "enabled": f.get("enabled"), "offset": (page - 1) * 100},
        ).fetchall()
        totals = conn.execute(
            """SELECT count(*)::int AS total, count(*) FILTER (WHERE enabled)::int AS enabled,
                      count(*) FILTER (WHERE health = 'failing')::int AS failing, count(*) FILTER (WHERE health = 'degraded')::int AS degraded FROM sources"""
        ).fetchone()
    return {"page": page, "rows": rows, "totals": totals, "useLabels": USE_LABELS}


def source_detail(pool, source_id: str) -> dict | None:
    from newsroom.engine.jobs.publication_keys import republish_key

    with pool.connection() as conn:
        source = conn.execute("SELECT *, (tier = 'T1') AS first_party FROM sources WHERE id = %s", (source_id,)).fetchone()
        if not source:
            return None
        runs = conn.execute(
            "SELECT id, started_at, finished_at, status, found_count, new_count, error, detail FROM fetch_runs WHERE source_id = %s ORDER BY started_at DESC LIMIT 30", (source_id,)
        ).fetchall()
        items = conn.execute(
            """SELECT a.id, a.title, a.url, a.discovered_at, a.published_at, a.processing_state, p.selected, p.visibility, p.title AS title_zh
               FROM articles a LEFT JOIN publications p ON p.article_id = a.id WHERE a.source_id = %s ORDER BY a.discovered_at DESC LIMIT 30""",
            (source_id,),
        ).fetchall()
        stats = conn.execute(
            """SELECT count(*)::int AS total, count(*) FILTER (WHERE a.discovered_at > now() - interval '7 days')::int AS last7d,
                      (SELECT count(*)::int FROM publications p WHERE p.source_id = %(id)s AND p.selected) AS selected
               FROM articles a WHERE a.source_id = %(id)s""",
            {"id": source_id},
        ).fetchone()
        history = audit_history(conn, f"source:{source_id}")
        republish = conn.execute("SELECT value FROM settings WHERE key = %s", (republish_key(source_id),)).fetchone()
    return {"source": source, "runs": runs, "items": items, "stats": stats, "history": history, "republish": republish["value"] if republish else None, "useLabels": USE_LABELS}


# -- preview ------------------------------------------------------------------------------------------------------------------------------------


def preview_stored_source(pool, source_id: str) -> dict | None:
    with pool.connection() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
    return preview_source(pool, source) if source else None


def preview_source(pool, draft: dict) -> dict:
    """A source fetched now without storing anything."""
    source = {"name": draft.get("id"), "enabled": True, "cursor": None, "tier": "T2", "participation_mode": "editorial", **draft}
    _supported(source["kind"], source["config"])
    started = time.perf_counter()
    if source["kind"] == "rss":
        found = fetch_rss(source, True)["candidates"]
    elif source["kind"] == "web_list":
        found = fetch_web_list(source)
    elif source["kind"] == "json_list":
        found = fetch_json_list(source)
    elif source["kind"] == "x_search":
        raise NotImplementedError("previewing an X search needs SocialData, which is not ported yet")
    else:
        raise RuntimeError(f"preview is not available for {source['kind']} sources")
    # The listing and its detail pages as collection reads them, within the fixed publication boundary; the limits of a first import and of later runs
    # depend on the source's runs and are not previewed.
    with pool.connection() as conn:
        candidates = listing_entries(conn, source, found)
        known = stored_details(conn, [c["identity_key"] for c in candidates])

    def admits(c: dict) -> bool:
        return len(filter_publication_window([c], source["config"].get("publishedAfter"))) > 0

    max_fetches = (source["config"].get("detail") or {}).get("maxFetches")
    reads = enrich_details(candidates, source, known, budget=int(max_fetches or 0), after=None, dated_out={}, retries={}, admits=admits)
    # A page that fails keeps its listing data, as in collection; when none can be read, the rules are what fails.
    if reads["failures"] and len(reads["failures"]) == reads["attempts"]:
        raise FetchError(f"原文頁全部讀取失敗（{len(reads['failures'])} 頁）：{'；'.join(f'{f['url']}（{f['error']}）' for f in reads['failures'])}")
    candidates = [c for c in candidates if admits(c)]
    return {
        "ms": int(round((time.perf_counter() - started) * 1000)),
        "count": len(candidates),
        "items": [
            {"title": c["title"], "url": c["url"], "publishedAt": iso(c["published_at"]) if c.get("published_at") else None, "excerpt": (c.get("excerpt") or c.get("body_text") or "")[:200]}
            for c in candidates[:20]
        ],
    }


# -- changing ----------------------------------------------------------------------------------------------------------------------------------


def source_identity(kind: str, config: dict) -> str | None:
    """The address a source collects from, used to find duplicates before creating one."""
    raw = next((config[k] for k in ("feedUrl", "url", "listUrl", "endpoint") if config.get(k) is not None), None)
    if kind == "x_search":
        handle = x_handle(config)
        return f"x:{handle.lower()}" if handle else None
    if kind == "mp_account":  # a WeChat account is one account whichever id names it
        ident = str(config["ghid"] if config.get("ghid") is not None else config.get("wxid") if config.get("wxid") is not None else "").strip().lower()
        return f"mp:{ident}" if ident else None
    if not raw:
        return None
    text = str(raw)
    return normalize_url(re.sub(r"^https://r\.jina\.ai/", "", text, count=1)) or text


def find_duplicate_source(conn, kind: str, config: dict, except_id: str | None = None) -> dict | None:
    identity = source_identity(kind, config)
    if not identity:
        return None
    for r in conn.execute("SELECT id, kind, config, name FROM sources WHERE kind = %s", (kind,)).fetchall():
        if r["id"] != except_id and source_identity(r["kind"], r["config"]) == identity:
            return r
    return None


def _assert_replacement_config(kind: str, config: dict) -> None:
    """Replacing a working collector requires the fields its replacement needs to produce articles."""
    address = config.get("feedUrl") if kind == "rss" else config.get("url")
    try:
        u = urlsplit(str(address if address is not None else ""))
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise ValueError
    except ValueError:
        raise BadRequest("完整採集配置須包含有效的 HTTP(S) 地址") from None
    paths = config.get("titlePaths")
    if kind == "json_list" and (
        not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths) or not (config.get("urlTemplate") or config.get("urlTemplateFallback"))
    ):
        raise BadRequest("JSON 列表須提供標題路徑和文章地址模板")


def request_republish(conn, source_id: str, actor: str) -> None:
    """Re-derives a source's public rows (in the worker) after a change to fields they are derived from."""
    from newsroom.engine.jobs.publication_keys import republish_key

    conn.execute(
        """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
        (republish_key(source_id), Jsonb({"status": "queued", "queuedAt": iso(datetime.now(UTC))}), actor),
    )
    enqueue(conn, QUEUES["republishSource"], {"sourceId": source_id}, singleton_key=source_id)


def update_source(pool, source_id: str, inp, actor: str) -> dict | None:
    patch = parse_patch(inp.get("patch", V.MISSING) if isinstance(inp, dict) else V.MISSING)
    after = _save_source(pool, source_id, patch, inp, actor)
    if not after or "uses" not in patch:
        return after
    apply_use_pace(pool, source_id)  # new uses may ask for a faster pace: from now, not from the next daily adaptation
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()


def _same_json(a, b) -> bool:
    return json.dumps(a, sort_keys=False, default=str) == json.dumps(b, sort_keys=False, default=str)


def _save_source(pool, source_id: str, patch: dict, inp: dict, actor: str) -> dict | None:
    with pool.connection() as conn, conn.transaction():
        # Creation and address edits share the lock: checking then inserting must not race.
        if "config" in patch:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('admin-source-identity'))")
        before = conn.execute("SELECT * FROM sources WHERE id = %s FOR UPDATE", (source_id,)).fetchone()
        if not before:
            return None
        if iso(before["updated_at"]) != inp.get("version"):
            raise Conflict("信源已被其他操作修改，請重新整理後再改")
        kind_changed = "kind" in patch and patch["kind"] != before["kind"]
        if "kind" in patch and "config" not in patch:
            raise BadRequest("調整採集方式須同時提供完整採集配置")
        if kind_changed:
            if before["kind"] not in REPLACEABLE_SOURCE_KINDS:
                raise BadRequest("僅 RSS、網頁列表和 JSON 列表之間可調整採集方式")
            _assert_replacement_config(patch["kind"], patch["config"])
            boundary = before["config"].get("publishedAfter")
            if boundary:
                new = patch["config"].get("publishedAfter")
                if new is not None and _parse_ms(new) < _parse_ms(boundary):
                    raise BadRequest("調整採集方式不能擴大既有發布時間範圍")
                if patch["config"].get("publishedAfter") is None:
                    patch["config"]["publishedAfter"] = boundary
        # Kept in the admin shape for existing clients, but no independent first-party setting remains.
        if "tier" in patch or "first_party" in patch:
            patch["first_party"] = (patch.get("tier") or before["tier"]) == "T1"
        if "config" in patch:
            next_kind = patch.get("kind") or before["kind"]
            _supported(next_kind, patch["config"])
            # A changed address must not be one another source already collects from.
            moved = kind_changed or source_identity(next_kind, patch["config"]) != source_identity(before["kind"], before["config"])
            dup = find_duplicate_source(conn, next_kind, patch["config"], source_id) if moved else None
            if dup:
                raise Conflict(f"與已有信源重複：{dup['name']}（{dup['id']}）")
        keys = list(patch)
        if not keys:
            return before
        cursor = dict(before["cursor"]) if before["cursor"] else None
        if kind_changed and cursor:
            cursor.pop("rss", None)
            cursor.pop("detailRules", None)
        enabled = patch.get("enabled")
        sets = ", ".join(f"{k} = %({k})s" for k in keys)
        params = {k: (Jsonb(patch[k]) if k == "config" else patch[k]) for k in keys}
        params.update({"kc": kind_changed, "en": enabled, "cur": Jsonb(cursor) if cursor else None, "id": source_id})
        after = conn.execute(
            f"""UPDATE sources SET {sets}, updated_at = now(),
                  cursor = CASE WHEN %(kc)s THEN %(cur)s::jsonb ELSE cursor END,
                  health = CASE WHEN %(en)s::boolean IS FALSE THEN 'paused' WHEN %(kc)s AND enabled OR %(en)s::boolean IS TRUE AND health = 'paused' THEN 'unknown' ELSE health END,
                  next_fetch_at = CASE WHEN %(en)s::boolean IS TRUE OR %(kc)s AND enabled THEN now() ELSE next_fetch_at END
                WHERE id = %(id)s RETURNING *""",
            params,
        ).fetchone()
        if before["participation_mode"] != "editorial" and patch.get("participation_mode") == "editorial":
            # A heat-only decision cannot admit the first editorial judgement to selection. Previously judged revisions remain reusable.
            conn.execute(
                f"""UPDATE articles a SET {GROUPING_RESET}
                    WHERE a.source_id = %s AND NOT EXISTS (
                      SELECT 1 FROM analyses an WHERE an.article_id = a.id AND an.input_revision = a.revision AND an.relevance IS NOT NULL)""",
                (source_id,),
            )
        audit(
            conn, actor, "source.update", f"source:{source_id}", inp.get("reason"),
            {**{k: before[k] for k in keys}, **({"cursor": before["cursor"]} if kind_changed else {})},
            {**patch, **({"cursor": cursor} if kind_changed else {})},
        )  # fmt: skip
        # What public exits show for this source's articles is derived from these fields: re-derive them all (in the worker).
        publisher_changed = "config" in patch and any(
            json.dumps(before["config"].get(k)) != json.dumps(patch["config"].get(k)) for k in ("publisherRole", "publisherUrlPrefixes", "publisherRequiresDiscovery")
        )
        if publisher_changed or any(k in PUBLICATION_FIELDS and not _same_json(before[k], patch[k]) for k in keys):
            request_republish(conn, source_id, actor)
        return after


def _parse_ms(value) -> float:
    from newsroom.engine.sources.dates import js_parse

    d = js_parse(str(value))
    return d.timestamp() * 1000 if d else float("nan")


def create_source(pool, raw, actor: str) -> dict:
    s = parse_create(raw)
    # First party is tier T1 only: an official account, staff member, original reporting or a link to the official site does not make a T1_5 or T2
    # source first party.
    s["first_party"] = s["tier"] == "T1"
    _supported(s["kind"], s["config"])
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('admin-source-identity'))")
        dup = find_duplicate_source(conn, s["kind"], s["config"])
        if dup:
            return {"created": False, "duplicate": dup}
        row = conn.execute(
            """INSERT INTO sources (id, name, kind, config, tier, selection_class, participation_mode, uses, interval_minutes, first_party, tags, site_fulltext, syndicate_fulltext, next_fetch_at)
               VALUES (%(id)s, %(name)s, %(kind)s, %(config)s, %(tier)s, %(selection_class)s, %(participation_mode)s, %(uses)s, %(interval_minutes)s, %(first_party)s, %(tags)s,
                       %(site_fulltext)s, %(syndicate_fulltext)s, now())
               ON CONFLICT (id) DO NOTHING RETURNING *""",
            {**s, "config": Jsonb(s["config"])},
        ).fetchone()
        if not row:
            raise Conflict(f"信源 ID {s['id']} 已存在")
        audit(conn, actor, "source.create", f"source:{s['id']}", None, None, s)
    if not s["uses"]:
        return {"created": True, "source": row}
    apply_use_pace(pool, s["id"])  # its uses may ask for a faster pace than the one it was created with
    with pool.connection() as conn:
        return {"created": True, "source": conn.execute("SELECT * FROM sources WHERE id = %s", (s["id"],)).fetchone()}


def fetch_now(pool, source_id: str, actor: str) -> dict | None:
    with pool.connection() as conn:
        s = conn.execute("SELECT id, kind, config FROM sources WHERE id = %s", (source_id,)).fetchone()
        if not s:
            return None
        if s["kind"] == "mp_account":
            job = enqueue(conn, QUEUES["mpCheck"], {"sourceId": source_id, "reason": "manual"}, singleton_key=f"mp:{source_id}")
        else:
            job = enqueue(conn, QUEUES["fetchSource"], {"sourceId": source_id, "force": True}, singleton_key=f"manual:{source_id}")
        audit(conn, actor, "source.fetch", f"source:{source_id}", None, None, {"jobId": job})
    return {"jobId": job}
