"""Collection run for one source: fetch listing → filter → store material → enqueue processing. A failed fetch never advances the success cursor; the
source's health reflects consecutive failures. Port of sources/collect.ts (RSS, web list and JSON list; X searches and the schedule come later)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from newsroom.core.urls import identity_key_for_url
from newsroom.engine.budget import BudgetExceededError
from newsroom.engine.extract import page_fetchable
from newsroom.engine.jobs.queue import QUEUES, enqueue
from newsroom.engine.lib.http_fetch import HttpStatusError
from newsroom.engine.lib.ids import sha256
from newsroom.engine.materials import (
    FUTURE_TOLERANCE,
    STALE_ON_DISCOVERY,
    Material,
    fill_scheduled_publications,
    identity_key_for,
    is_historical,
    reuse_external_material_identities,
    upsert_material,
)
from newsroom.engine.presentation import PresentationMismatch
from newsroom.engine.receipts import complete_receipt
from newsroom.engine.shutdown import SHUTDOWN  # noqa: E402
from newsroom.engine.sources.config_keys import unsupported_config
from newsroom.engine.sources.dates import _iso as iso
from newsroom.engine.sources.filters import admit_listing, filter_publication_window
from newsroom.engine.sources.json_list import fetch_json_list
from newsroom.engine.sources.rss import FetchError, fetch_rss, reads_whole
from newsroom.engine.sources.web_list import fetch_detail, fetch_web_list, is_call_to_action_title, needs_title
from newsroom.engine.sources.x import fetch_x_search, plan_x_shards, read_x_search, self_thread_handle, shard_handle, shard_query, tweet_to_candidate

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
FUTURE_TOLERANCE_MS = FUTURE_TOLERANCE.total_seconds() * 1000
SOURCE_COLUMNS = "id, name, kind, config, tier, participation_mode, uses, first_party, interval_minutes, enabled, cursor, fail_count"
PRIORITY = {"live": 0, "liveSignal": -1, "history": -2}


def _num(value, default: float) -> float:
    """Number(value ?? default)."""
    return default if value is None else float(value)


def now_ms() -> float:
    return time.time() * 1000


def now_iso() -> str:
    return iso(datetime.now(UTC))


def parse_iso(text: str) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def load_source(pool, source_id: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(f"SELECT {SOURCE_COLUMNS} FROM sources WHERE id = %s", (source_id,)).fetchone()


def stored_details(conn, identities: list[str]) -> dict[str, dict]:
    """What material under these identities already has, whichever source stored it."""
    if not identities:
        return {}
    rows = conn.execute(
        """SELECT identity_key, source_id, title, published_at, publication_time, excerpt, body_status, raw->'collectionDetail'->>'rules' AS rules
           FROM articles WHERE identity_key = ANY(%s::text[])""",
        (identities,),
    ).fetchall()
    return {r["identity_key"]: r for r in rows}


def detail_rules_of(source: dict) -> str | None:
    """The detail rules in force; a page read under other rules is read again."""
    detail = source["config"].get("detail")
    return sha256(json.dumps(detail, ensure_ascii=False, separators=(",", ":"))) if detail else None


def _material_of(c: dict, source_id: str, **extra) -> Material:
    fields = {k: v for k, v in c.items() if k in Material.__dataclass_fields__ and k not in ("source_id", "via", "extra")}
    return Material(source_id=source_id, via="fetch", **{**fields, **extra})


def listing_entries(conn, source: dict, found: list[dict]) -> list[dict]:
    """A listing as every run reads it: the source's URL, category and noise rules, its order, and one entry per stored identity. Deduplication
    comes before detail reads and limits: URL aliases must neither buy duplicate reads nor crowd other articles out of the window."""
    candidates = admit_listing(found, source)
    cfg = source["config"]
    if source["kind"] == "json_list" and cfg.get("externalIdPath"):
        wrapped = [_material_of(c, source["id"]) for c in candidates]
        keys = reuse_external_material_identities(conn, source["id"], wrapped)
        candidates = [{**c, "identity_key": m.identity_key} if m.identity_key else c for c, m in zip(candidates, keys, strict=True)]
    if cfg.get("sortByPublishedAt"):
        candidates = sorted(candidates, key=lambda c: -(c["published_at"].timestamp() * 1000 if c.get("published_at") else 0))
    unreliable_dates = (cfg.get("detail") or {}).get("publishedAtAuthoritative") is True
    unique: dict[str, dict] = {}
    for c in candidates:
        key = c.get("identity_key")
        if not key and cfg.get("preserveUrlFragment") is True:
            key = identity_key_for_url(c["url"], keep_fragment=True)
        if not key:
            key = identity_key_for(_material_of(c, source["id"]))
        if key not in unique:
            unique[key] = {**c, "identity_key": key, **({"published_at": None, "publication_time": None} if unreliable_dates else {})}
    return list(unique.values())


def admission(source: dict, known: dict, first_import: bool):
    """Which entries a run stores. A first import takes the configured months of archive; later runs take new identities only when published since the
    source was added, less the stale-on-discovery window. An X search is bounded by its watermark instead."""
    months = _num((source["config"].get("_aihot") or {}).get("initialBackfillMonths"), 12)
    if first_import:
        floor = now_ms() - months * 30 * DAY_MS
    elif source["kind"] == "x_search":
        floor = None
    else:
        floor = (parse_iso(source["cursor"]["initializedAt"]) or float("nan")) - STALE_ON_DISCOVERY.total_seconds() * 1000

    def by_listing(c: dict) -> bool:
        if floor is None or (not first_import and c["identity_key"] in known):
            return True
        return not (c.get("published_at") and c["published_at"].timestamp() * 1000 < floor)

    def admits(c: dict) -> bool:
        return by_listing(c) and len(filter_publication_window([c], source["config"].get("publishedAfter"))) > 0

    return by_listing, admits


def current_date_read(read: list) -> bool:
    """Excluded detail dates expire: an undated listing cannot reveal a corrected date on the page. No date, or an explicit access refusal (null
    checkedAt), keeps the existing read-once rule."""
    return read[1] is None or read[2] is None or read[2] > now_ms() - DAY_MS


def retry_wait(failures: int) -> float:
    """How long a page waits after its latest straight failure: two quick retries, then an hour doubling to a day: never given up."""
    return 0 if failures < 3 else min(24 * HOUR_MS, HOUR_MS * 2 ** (failures - 3))


def _from_stored(c: dict, stored: dict, source: dict, rules: str | None) -> bool:
    d = source["config"].get("detail") or {}
    if d.get("titleSelector") or d.get("titleRegex"):
        c["title"] = stored["title"]
    current = stored["rules"] == rules
    if (current or d.get("publishedAtAuthoritative") is not True) and not c.get("published_at"):
        c["published_at"] = stored["published_at"]
        c["publication_time"] = stored.get("publication_time")
    return current


def _detail_need(c: dict, stored: dict | None, source: dict, listing_title: str) -> dict | None:
    d = source["config"]["detail"]
    stored_headline = stored is not None and is_call_to_action_title(listing_title) and not is_call_to_action_title(stored["title"])
    need = {
        "date": d.get("publishedAtAuthoritative") is True or not ((stored and stored["published_at"]) or c.get("published_at")) or d.get("upgradeDatePrecision") is True,
        "title": bool(d.get("titleSelector") or d.get("titleRegex")) and (d.get("titleAuthoritative") is True or (not stored_headline and needs_title(c["title"]))),
        "summary": bool(d.get("summarySelector")) and not ((stored and stored["excerpt"]) or c.get("excerpt")),
        "body": reads_whole(source) and (not stored or stored["body_status"] != "ok") and not c.get("body_text") and (not c.get("body_status") or c.get("body_status") == "pending"),
    }  # fmt: skip
    return need if need["date"] or need["title"] or need["summary"] else None


def _apply_detail(c: dict, got: dict, rules: str) -> None:
    """A detail page's findings. A confirmed body enters through normal material revisions and skips the redundant fetch job."""
    c["detail_rules"] = rules
    if got.get("title"):
        c["title"] = got["title"]
    if got.get("summary"):
        c["excerpt"] = got["summary"]
    if got.get("body"):
        c["body_html"] = got["body"]["html"]
        c["body_text"] = got["body"]["text"]
        c["body_status"] = "ok"
        if not c.get("media"):
            c["media"] = got["body"]["images"]
    # The page's date (its source's rule first); a date-only listing value gives way to its time on the same day.
    pub = got.get("published_at")
    if pub and (not c.get("published_at") or abs((pub - c["published_at"]).total_seconds() * 1000) < DAY_MS):
        c["published_at"] = pub
        c["publication_time"] = got.get("publication_time")


def enrich_details(candidates: list[dict], source: dict, known: dict, *, budget: int, after: str | None, dated_out: dict, retries: dict, admits, pool=None) -> dict:
    """Detail pages (config.detail) fill what a listing lacks, reading at most `budget` pages; the rest finish on a later run. A page read under the
    rules in force is not read again, and another source's material takes nothing from this listing."""
    rules = detail_rules_of(source)
    reads: dict[str, Any] = {"attempts": 0, "pending": 0, "failures": [], "refused": [], "listed": {}, "retries": {}, "after": None}
    provider_budget_spent = False
    start = next((i for i, c in enumerate(candidates) if c["identity_key"] == after), -1) + 1
    ordered = candidates[start:] + candidates[:start]
    for c in ordered:
        key = c["identity_key"]
        stored = known.get(key)
        if stored and stored["source_id"] != source["id"]:
            continue
        listing_title = c["title"]
        if stored and _from_stored(c, stored, source, rules):
            continue
        need = None if rules is None else _detail_need(c, stored, source, listing_title)
        if not need:
            continue
        listed = iso(c["published_at"]) if c.get("published_at") else None
        seen = dated_out.get(key)
        remembered = datetime.fromisoformat(seen[1].replace("Z", "+00:00")) if seen and seen[1] else None
        retry = retries.get(key)
        if seen and seen[0] == listed and not admits({**c, "published_at": remembered}):
            c["published_at"] = remembered
            reads["listed"][key] = seen
            if current_date_read(seen):
                continue
        if provider_budget_spent or reads["attempts"] >= budget or (retry and now_ms() < (parse_iso(retry[1]) or 0) + retry_wait(retry[0])):
            reads["pending"] += 1
            if retry:
                reads["retries"][key] = retry
            continue
        reads["attempts"] += 1
        reads["after"] = key
        try:
            got = fetch_detail(c["url"], source, need, pool)
            if got.get("published_at"):
                c["published_at"] = datetime.fromisoformat(listed.replace("Z", "+00:00")) if listed else None
            _apply_detail(c, got, cast(str, rules))
            reads["listed"][key] = [listed, iso(c["published_at"]) if c.get("published_at") else None, now_ms()]
        except NotImplementedError:
            raise
        except Exception as error:
            if SHUTDOWN.is_set():
                raise
            failed = not isinstance(error, BudgetExceededError)
            # A page the site refuses (paywall, captcha, blocked address) is read under these rules once; a missing page, a timeout, rate limiting
            # and server errors have recovered, so they are retried.
            refused = isinstance(error, HttpStatusError) and error.status < 500 and error.status not in (404, 408, 425, 429)
            if not failed:
                provider_budget_spent = True
            else:
                reads["refused" if refused else "failures"].append({"url": c["url"], "error": str(error)[:300]})
            if refused:
                c["detail_rules"] = rules
                reads["listed"][key] = [listed, iso(c["published_at"]) if c.get("published_at") else None, None]
            else:
                reads["pending"] += 1
                if failed:
                    reads["retries"][key] = [(retry[0] if retry else 0) + 1, now_iso()]
                elif retry:
                    reads["retries"][key] = retry
    return reads


def route(conn, article_id: str) -> dict | None:
    """The article's next step: its body first while none is confirmed and the source asks for full text, else analysis."""
    row = conn.execute(
        """SELECT a.body_status, s.participation_mode, s.kind, s.config, s.uses, a.url, (coalesce(a.body_text, '') = '' AND a.x_post IS NULL) AS bare,
                  a.backfill, a.published_at, a.discovered_at
           FROM articles a JOIN sources s ON s.id = a.source_id WHERE a.id = %s""",
        (article_id,),
    ).fetchone()
    if not row:
        return None
    historical = is_historical(row["published_at"], row["discovered_at"], row["backfill"])
    signal = row["participation_mode"] != "editorial"
    whole = reads_whole(row) and (not signal or not historical)
    pending = row["body_status"] == "pending"
    cfg = row["config"]
    wants_body = cfg.get("fetchPublicContent") is True or bool(cfg.get("detail")) or row["kind"] == "web_list"
    needs_page = whole and page_fetchable(row["url"], row["kind"]) and (wants_body or row["bare"])
    needs_x_article = row["kind"] == "x_search" and (whole or (row["participation_mode"] == "hot_signal" and not historical))
    return {"step": "extract" if pending and (needs_page or needs_x_article) else "analyze", "signal": signal, "historical": historical}


def grouping_priority(signal: bool, historical: bool) -> int:
    """A grouping job's place in the queue order: live work before history, discussion evidence behind reports."""
    return PRIORITY["history"] if historical else PRIORITY["liveSignal"] if signal else PRIORITY["live"]


def queue_processing(db, article_id: str, step: str | None = None, attempt_tag: str | None = None) -> str | None:
    """The one way to hand an article to processing. `attempt_tag` makes an explicit re-evaluation a new (paid) request. With a connection, the job
    commits with the caller's write."""
    if hasattr(db, "execute"):
        return _queue_processing(db, article_id, step, attempt_tag)
    with db.connection() as conn:
        return _queue_processing(conn, article_id, step, attempt_tag)


def _queue_processing(conn, article_id: str, step: str | None, attempt_tag: str | None) -> str | None:
    r = route(conn, article_id)
    if not r:
        return None
    step = step or r["step"]
    queued = conn.execute(
        "UPDATE articles SET processing_queued_at = now(), processing_attempt_tag = coalesce(%s, processing_attempt_tag) WHERE id = %s RETURNING processing_attempt_tag",
        (attempt_tag, article_id),
    ).fetchone()
    if not queued:
        return None
    tag = queued["processing_attempt_tag"]  # extraction and the sweep only carry articleId: retain the current evaluation's paid identity
    prio = PRIORITY["history"] if r["historical"] else PRIORITY["live"]
    if step == "extract":
        return enqueue(conn, QUEUES["extractBody"], {"articleId": article_id}, singleton_key=article_id, priority=prio)
    if r["signal"] and not tag:
        return enqueue(conn, QUEUES["group"], {"articleId": article_id, "signalOnly": True}, singleton_key=article_id, priority=grouping_priority(r["signal"], r["historical"]))
    return enqueue(
        conn, QUEUES["analyze"], {"articleId": article_id, "attemptTag": tag} if tag else {"articleId": article_id},
        singleton_key=f"manual:analyze:{article_id}:{tag}" if tag else article_id, priority=prio,
    )  # fmt: skip


def store(pool, source_id: str, candidates: list[dict], backfill: str | None) -> dict:
    created = revised = 0
    for c in candidates:
        try:
            res = upsert_material(pool, _material_of(c, source_id, backfill=backfill))
        except PresentationMismatch:
            # Same wording, a layout the translation cannot follow: the article's transaction retained the consistent original and translation, which is the outcome;
            # later articles are still stored.
            continue
        # A successful page with no matching field is complete too. Otherwise an undated article at the front of a listing would consume the detail
        # budget forever and strand the remaining ones.
        if c.get("detail_rules"):
            with pool.connection() as conn:
                conn.execute(
                    """UPDATE articles SET raw = jsonb_set(CASE WHEN jsonb_typeof(raw) = 'object' THEN raw ELSE '{}'::jsonb END, '{collectionDetail}', %s)
                       WHERE id = %s AND source_id = %s""",
                    (Jsonb({"rules": c["detail_rules"]}), res.article_id, source_id),
                )
        created += 1 if res.created else 0
        revised += 1 if res.revised else 0
        # Extraction first when the source wants full text and none came with the listing, else analysis.
        if res.created or res.revised or res.processing_needed:
            queue_processing(pool, res.article_id)
    return {"created": created, "revised": revised}


def record_fetch(conn, source_id: str, run_id: int, *, found: int, created: int, detail: dict | None, cursor: dict | None = None, error: str | None = None, budget: bool = False) -> None:
    """The end of a fetch run, on the run and on its source. Success marks the source healthy and due again one interval later. A failure counts toward
    "failing" (5 in a row) and waits longer after each one, up to six hours; a full budget is not the source's failure: it waits 15 minutes."""
    detail_json = Jsonb(detail) if detail else None
    if error is None:
        conn.execute(
            """UPDATE sources SET last_fetch_at = now(), last_ok_at = now(), fail_count = 0, last_error = NULL, health = 'ok', cursor = %s, updated_at = now(),
                 next_fetch_at = now() + make_interval(mins => interval_minutes) WHERE id = %s""",
            (Jsonb(cursor), source_id),
        )
        conn.execute(
            """UPDATE fetch_runs SET status = 'ok', finished_at = now(), found_count = %s, new_count = %s,
                 detail = nullif(coalesce(detail,'{}'::jsonb) || coalesce(%s::jsonb,'{}'::jsonb),'{}'::jsonb) WHERE id = %s""",
            (found, created, detail_json, run_id),
        )
        return
    conn.execute(
        """UPDATE sources SET last_fetch_at = now(),
             fail_count = CASE WHEN %(budget)s THEN fail_count ELSE fail_count + 1 END, last_error = %(error)s,
             health = CASE WHEN %(budget)s THEN health WHEN fail_count + 1 >= 5 THEN 'failing' ELSE 'degraded' END,
             next_fetch_at = now() + make_interval(mins => CASE WHEN %(budget)s THEN 15 ELSE LEAST(interval_minutes * (fail_count + 2), 360) END),
             updated_at = now() WHERE id = %(id)s""",
        {"budget": budget, "error": error, "id": source_id},
    )
    conn.execute(
        """UPDATE fetch_runs SET status = 'failed', finished_at = now(), found_count = %s, new_count = %s, error = %s,
             detail = nullif(coalesce(detail,'{}'::jsonb) || coalesce(%s::jsonb,'{}'::jsonb),'{}'::jsonb) WHERE id = %s""",
        (found, created, error, detail_json, run_id),
    )


def _result(source_id: str, status: str, found=0, created=0, revised=0, error: str | None = None) -> dict:
    out = {"sourceId": source_id, "status": status, "found": found, "created": created, "revised": revised}
    if error is not None:
        out["error"] = error
    return out


def collect_source(pool, source_id: str, force: bool = False, x_since: str | None = None) -> dict:
    """`x_since` reads an X account again from that post, without moving its watermark back."""
    source = load_source(pool, source_id)
    if not source:
        return _result(source_id, "skipped", error="missing")
    if not source["enabled"] and not force:
        return _result(source_id, "skipped", error="paused")
    if source["kind"] in ("mp_account", "external"):
        return _result(source_id, "skipped")  # WeChat accounts are reconciled by the mp job; external sources only receive reports
    with pool.connection() as conn:
        run_id = conn.execute("INSERT INTO fetch_runs (source_id) VALUES (%s) RETURNING id", (source_id,)).fetchone()["id"]
    cursor = source["cursor"] or {}
    first_import = not cursor.get("initializedAt")
    created = revised = found = 0
    paid_receipt_ids: list[int] = []
    try:
        # A config entry this kind does not implement fails the run, visibly, instead of being ignored.
        bad = unsupported_config(source["kind"], source["config"])
        if bad:
            raise FetchError(f"unsupported config: {', '.join(bad)}")
        next_cursor = dict(cursor)
        detail: dict | None = None
        d = source["config"].get("detail")
        rules = detail_rules_of(source)
        if source["kind"] == "rss":
            # Changed detail rules require the listing again even when its bytes did not change.
            expired = any(not current_date_read(read) for read in (cursor.get("detailDatedOut") or {}).values())
            rss = fetch_rss(source, force or expired or (rules is not None and cursor.get("detailRules") != rules))
            candidates = rss["candidates"]
            # The first import keeps only part of the listing: the next run reads all of it once before accepting 304s.
            if not first_import:
                next_cursor["rss"] = rss["validator"]
            else:
                next_cursor.pop("rss", None)
            if rss["notModified"]:
                detail = {"notModified": True, "httpStatus": 304}
        elif source["kind"] == "web_list":
            candidates = fetch_web_list(source, pool)
        elif source["kind"] == "json_list":
            candidates = fetch_json_list(source, pool)
        else:
            x = fetch_x_search(pool, source, x_since)
            candidates = x["candidates"]
            paid_receipt_ids = x["receiptIds"]
            if x["lastId"] and not (next_cursor.get("lastTweetId") and int(str(next_cursor["lastTweetId"])) > int(x["lastId"])):
                next_cursor["lastTweetId"] = x["lastId"]
            # A search longer than one run keeps its position for the next runs (shown in the admin).
            if x["backlog"]:
                next_cursor["xBacklog"] = x["backlog"]
            else:
                next_cursor.pop("xBacklog", None)
            detail = {"pages": x["pages"], "truncated": x["truncated"], "backlog": len(x["backlog"]), "backlogPages": x["backlogPages"], "dropped": x["dropped"]}
        found = len(candidates)
        with pool.connection() as conn:
            candidates = listing_entries(conn, source, candidates)
            # Admission of old archive entries and detail recovery both need the same stored identities.
            known = stored_details(conn, [c["identity_key"] for c in candidates]) if d or (not first_import and source["kind"] != "x_search") else {}
        by_listing, admits = admission(source, known, first_import)
        candidates = [c for c in candidates if by_listing(c)]
        # First import of a new source: bounded, and archived by source time (never "today", never pushed).
        if first_import:
            candidates = candidates[: int(_num((source["config"].get("_aihot") or {}).get("initialBackfillLimit"), 30))]
        same_rules = cursor.get("detailRules") == rules
        reads = enrich_details(
            candidates, source, known, budget=int(_num((d or {}).get("maxFetches"), 0)), admits=admits,
            after=cursor.get("detailAfter") if same_rules else None,
            dated_out=(cursor.get("detailDatedOut") or {}) if same_rules else {},
            retries=(cursor.get("detailRetries") or {}) if same_rules else {},
            pool=pool,
        )  # fmt: skip
        if d:
            detail = {**(detail or {}), "detailAttempts": reads["attempts"], "detailFailures": len(reads["failures"]), "detailPending": reads["pending"],
                      "detailErrors": [*reads["failures"], *reads["refused"]]}  # fmt: skip
            next_cursor["detailRules"] = rules
            if reads["after"]:
                next_cursor["detailAfter"] = reads["after"]
            # A validator covers the whole listing: accept 304 only after its detail work is complete.
            if source["kind"] == "rss" and reads["pending"] > 0:
                next_cursor.pop("rss", None)
        else:
            next_cursor.pop("detailRules", None)
            next_cursor.pop("detailAfter", None)

        # Detail pages can establish a date the listing did not have, including an old archive date: such an entry is not stored, and the cursor
        # remembers what its page said.
        dated_out: dict = {}
        kept = []
        for c in candidates:
            if admits(c):
                kept.append(c)
                continue
            # A future entry can become admissible without changing the feed's bytes or validator.
            if source["kind"] == "rss" and c.get("published_at") and c["published_at"].timestamp() * 1000 > now_ms() + FUTURE_TOLERANCE_MS:
                next_cursor.pop("rss", None)
            listed = reads["listed"].get(c["identity_key"])
            if listed is not None:
                dated_out[c["identity_key"]] = listed
        candidates = kept
        # Kept while the listing offers them; a 304 offers no entries and keeps the last ones.
        if found:
            next_cursor["detailDatedOut"] = dated_out
            next_cursor["detailRetries"] = reads["retries"]
        if not d or not next_cursor.get("detailDatedOut"):
            next_cursor.pop("detailDatedOut", None)
        if not d or not next_cursor.get("detailRetries"):
            next_cursor.pop("detailRetries", None)
        stored = store(pool, source_id, candidates, "first-import" if first_import else None)
        created, revised = stored["created"], stored["revised"]
        for article_id in fill_scheduled_publications(pool, source_id):
            queue_processing(pool, article_id)

        if first_import:
            next_cursor["initializedAt"] = now_iso()
        next_cursor["lastOkAt"] = now_iso()
        with pool.connection() as conn, conn.transaction():
            record_fetch(conn, source_id, run_id, found=found, created=created, detail=detail, cursor=next_cursor)
            for receipt_id in paid_receipt_ids:
                complete_receipt(conn, receipt_id)
        return _result(source_id, "ok", found, created, revised)
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        message = str(error)[:1000]
        with pool.connection() as conn:
            record_fetch(conn, source_id, run_id, found=found, created=created, detail=None, error=message, budget=isinstance(error, BudgetExceededError))
        return _result(source_id, "failed", found, created, revised, message)


# ── X accounts read together ────────────────────────────────────────────────────

X_EPOCH_MS = 1288834974657


def x_id_at(ms: float) -> int:
    """X ids begin with their millisecond timestamp (since 2010-11-04): the smallest id of a post made at `ms`."""
    return max(0, int(ms) - X_EPOCH_MS) << 22


def covered_to(m: dict) -> int:
    """Where an account's posts are known to be read up to. A quiet account's newest post can be months old, but its last successful check read everything up to then;
    bounding a shard's search by the post alone would re-read months of the other accounts' posts. Ten minutes before the check allows for posts that reach the search
    late."""
    own = int(m["cursor"]["lastTweetId"])
    checked = parse_iso(str((m["cursor"] or {}).get("lastOkAt") or ""))
    if checked is None:
        return own
    by_time = x_id_at(checked - 10 * 60_000)
    return by_time if by_time > own else own


def collect_x_shard(pool, key: str, source_ids: list[str]) -> dict:
    """One search for a shard of X accounts (plan_x_shards). Each post goes to the source whose handle wrote it, and every account keeps its own fetch run, health and
    cursor. The oldest watermark bounds the search, so no account misses a post (the others only see posts they already have again); afterwards each account's watermark
    moves to its own newest post the search saw, and the stretches still unread are kept in each account's cursor, so they survive a change of shards."""
    with pool.connection() as conn:
        rows = conn.execute(f"SELECT {SOURCE_COLUMNS} FROM sources WHERE id = ANY(%s) ORDER BY id", (source_ids,)).fetchall()
    members = [m for m in rows if m["enabled"] and shard_handle(m)]
    if not members:
        return {"key": key, "status": "skipped", "accounts": 0, "found": 0, "created": 0}
    with pool.connection() as conn:
        runs = {m["id"]: conn.execute("INSERT INTO fetch_runs (source_id) VALUES (%s) RETURNING id", (m["id"],)).fetchone()["id"] for m in members}
    found = created = 0
    try:
        since = min(covered_to(m) for m in members)
        backlog: list[dict] = []
        stretches: set[str] = set()
        for m in members:
            for b in (m["cursor"].get("xBacklog") if isinstance(m["cursor"].get("xBacklog"), list) else []):
                if f"{b['query']} {b['next']}" not in stretches:
                    backlog.append(b)
                stretches.add(f"{b['query']} {b['next']}")
        read = read_x_search(
            pool, shard_query([cast(str, shard_handle(m)) for m in members], [h for h in (self_thread_handle(m) for m in members) if h]), str(since), backlog, f"x-shard:{key}"
        )  # fmt: skip
        detail = {"shard": key, "accounts": len(members), "pages": read["pages"], "truncated": read["truncated"], "backlog": len(read["backlog"]), "backlogPages": read["backlogPages"], "dropped": read["dropped"]}
        counts: dict[str, dict] = {}
        for m in members:
            handle = cast(str, shard_handle(m)).lower()
            mine = [t for t in read["tweets"] if t["user"]["screen_name"].lower() == handle]
            stored = store(pool, m["id"], admit_listing([tweet_to_candidate(t) for t in mine], m), None)
            found += len(mine)
            created += stored["created"]
            newest = str(m["cursor"]["lastTweetId"])
            for t in mine:
                if int(t["id_str"]) > int(newest):
                    newest = t["id_str"]
            counts[m["id"]] = {"found": len(mine), "created": stored["created"], "newest": newest}
        # Only a fully stored search advances coverage. A failed write can replay every paid page; material already committed is idempotent, and no member is left half-finished.
        with pool.connection() as conn, conn.transaction():
            for m in members:
                count = dict(counts[m["id"]])
                newest = count.pop("newest")
                cursor = {**m["cursor"], "lastTweetId": newest, "lastOkAt": now_iso()}
                if read["backlog"]:
                    cursor["xBacklog"] = read["backlog"]
                else:
                    cursor.pop("xBacklog", None)
                record_fetch(conn, m["id"], runs[m["id"]], found=count["found"], created=count["created"], detail=detail, cursor=cursor)
            for receipt_id in read["receiptIds"]:
                complete_receipt(conn, receipt_id)
        return {"key": key, "status": "ok", "accounts": len(members), "found": found, "created": created}
    except Exception as error:
        if SHUTDOWN.is_set():
            raise
        message = str(error)[:1000]
        budget = isinstance(error, BudgetExceededError)
        with pool.connection() as conn:
            for m in members:
                record_fetch(conn, m["id"], runs[m["id"]], found=0, created=0, detail={"shard": key, "accounts": len(members)}, error=message, budget=budget)
        return {"key": key, "status": "failed", "accounts": len(members), "found": found, "created": created, "error": message}


__all__ = ["plan_x_shards", "self_thread_handle"]
