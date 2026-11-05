"""Operations alerts. The person reading them is the site owner, not an engineer: each message says what readers see, whether it heals by itself and what, if anything,
the owner must do.
  now   - readers are affected and it has not healed: sent at once, repeated hourly, recovery reported.
  today - money at risk or only the owner can act: sent at once, repeated at most daily, recovery reported.
  later - other follow-ups: one 09:00 list a day for the agent (the owner's only without one). A source's follow-up is closed on its own: a fix removes it, and one
          judged not worth fixing is closed with a reason.
A finding with agent_only is still now or today, but it only wakes the agent: nothing is sent when it opens, nothing is sent when it clears, and it stays off the 09:00
digest. With an agent taking the alerts (notify_agent), a now or today problem that is not the owner's own (Finding.owner) is handed to the agent instead. The owner hears
of it when the same problem is handed over a third time in a day, or when it is still open HANDED_MS after the handoff, and from then on as above; an incident the agent
judged not a fault (hold_alert) is not told before its hold ends. The agent is not woken for what is the owner's alone (owner "only"). While the channel to the agent fails,
the owner hears everything again. Delivery goes through send_alert (alert webhook or ops chat, internal-chat fallback; off unless FEISHU_INTERNAL_ENABLED).
Port of operations/alerts.ts (with media/upstream.ts findings, operations/grouping.ts and operations/backup.ts's backup_configured)."""

from __future__ import annotations

import math
import os
import urllib.parse
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from newsroom.engine import models as M
from newsroom.engine import pack
from newsroom.engine.admin.runs import GROUPING_WARN_AFTER_MS, waiting_selected_news
from newsroom.engine.analyze import _conn
from newsroom.engine.audit import audit
from newsroom.engine.lib.text import js_slice
from newsroom.engine.notify_agent import agent_configured, agent_transport_findings, flush_agent_events, handoffs, wake_agent, wake_digest
from newsroom.engine.notify_feishu import Finding, duration, format_alert, format_recovery, format_return, send_alert
from newsroom.engine.operations_backup import backup_configured
from newsroom.engine.receipts import provider_access_refused
from newsroom.engine.sources.health import SOURCE_PROBLEMS, source_health
from newsroom.engine.time import add_days, beijing_at, beijing_date, beijing_day, beijing_stamp, is_valid_date

REPEAT_MS = {"now": 3600_000, "today": 24 * 3600_000}
HANDED_MS = {"now": 3600_000, "today": 3 * 3600_000}  # how long the agent has a problem before the owner hears of it too
RETURNS = 3  # the handoff that tells the owner a problem keeps coming back, counted over a day; told at most once a day
DAY_MS = 86_400_000
FAILING_SHARE = 0.15
FAILING_SOURCES = 50
REFUSED_PREFIX = "provider.refused."
DAILY_PREFIX = "report.daily:"
ONGOING_CLOSURE_MS = 7 * 86_400_000


def _iso(d: datetime) -> str:
    d = d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _ms(d: datetime) -> float:
    return d.timestamp() * 1000


def _parse(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000


def _at(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


# Valves default off: read at call time, only an explicit "true" turns them on.
def collecting() -> bool:
    return os.environ.get("COLLECT_ENABLED") == "true"


def models_on() -> bool:
    return os.environ.get("MODEL_CALLS_ENABLED") == "true"


def _quiet_minutes() -> tuple[float, float]:
    """How long the site may go without a new article before it counts as stalled, and how long while editorial fetches keep succeeding. At most a day."""
    alerts = pack.site()["ALERTS"]
    raw = os.environ.get("ALERT_QUIET_MINUTES")
    quiet = min(float(raw) if raw else alerts["quietMinutes"], 1440)
    return quiet, min(max(alerts["quietWhileFetchingMinutes"], quiet), 1440)


# ── checks ──────────────────────────────────────────────────────────────────────


def content_collection(pool, now: float, open_: dict) -> list[Finding]:
    """Content flow, judged by outcome: whatever broke (worker, egress proxy, models, queues), readers see a site that stops changing."""
    if not collecting() and not open_.get("content.collect"):
        return []
    quiet, quiet_fetching = _quiet_minutes()
    with pool.connection() as conn:
        last = conn.execute(
            """SELECT max(a.discovered_at) AS at FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE s.participation_mode = 'editorial' AND a.discovered_at > now() - interval '1 day'"""
        ).fetchone()["at"]
        quiet_for = now - _ms(last) if last else float("inf")
        if quiet_for <= quiet * 60_000:
            return []
        # Fetches still succeeding with nothing new is the sources being quiet, not collection stopping.
        fetching = (
            conn.execute(
                """SELECT 1 FROM fetch_runs f JOIN sources s ON s.id = f.source_id
                   WHERE s.participation_mode = 'editorial' AND f.status = 'ok' AND f.started_at > %s LIMIT 1""",
                (_at(now - quiet * 60_000),),
            ).fetchone()
            if quiet_for <= quiet_fetching * 60_000
            else None
        )
        if fetching:
            return []
        # A site without an enabled source has nothing to collect.
        if not conn.execute("SELECT 1 FROM sources WHERE enabled AND participation_mode = 'editorial' LIMIT 1").fetchone():
            return []
    usual = pack.site()["ALERTS"].get("usualFlow")
    return [Finding(
        key="content.collect", level="now", title="網站停止收錄新文章",
        impact=f"讀者看不到新文章：最後一篇收錄於 {beijing_stamp(last)}" if last else "讀者看不到新文章：一天內沒有收錄任何文章",
        heals=f"沒有，{usual}" if usual else "沒有", action="把這條發給 AI 排查處理",
        detail=(
            f"editorial articles.discovered_at 超過 {_num(quiet)} 分鐘沒有新值，期間沒有一次成功的 editorial fetch_runs（熱度訊號單獨評估）；查 worker、sources.schedule、出網代理與採集失敗"
            if quiet_for <= quiet_fetching * 60_000
            else f"editorial articles.discovered_at 超過 {_num(quiet_fetching)} 分鐘沒有新值（採集仍成功也算）；查採集結果為何一直是 new_count=0：解析、去重與入庫"
        ),
        since=last,
    )]  # fmt: skip


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def sources_failing(pool, now: float, open_: dict) -> list[Finding]:
    """Many sources failing at once points at the site's way out, not at the sources: the site keeps changing with what still works, so the stall above stays quiet.
    Each enabled source counts by its latest fetch, however long ago. Fewer than 50 failing sources are each one's own follow-up."""
    if not collecting() and not open_.get("content.fetch"):
        return []
    latest = """SELECT r.status, r.error, r.started_at FROM sources s CROSS JOIN LATERAL (
        SELECT status, error, started_at FROM fetch_runs f
        WHERE f.source_id = s.id AND f.status IN ('ok', 'failed') AND f.started_at <= %(now)s
        ORDER BY started_at DESC LIMIT 1) r
      WHERE s.enabled"""
    with pool.connection() as conn:
        r = conn.execute(
            f"SELECT count(*)::int AS fetched, count(*) FILTER (WHERE status = 'failed')::int AS failing FROM ({latest}) l", {"now": _at(now)}
        ).fetchone()
        if r["failing"] < FAILING_SOURCES or r["failing"] < r["fetched"] * FAILING_SHARE:
            return []
        errors = conn.execute(
            f"""SELECT left(coalesce(error, ''), 120) AS error, count(*)::int AS n, min(started_at) AS first FROM ({latest}) l
                WHERE status = 'failed' GROUP BY 1 ORDER BY 2 DESC""",
            {"now": _at(now)},
        ).fetchall()
    common = "；".join(f"{e['error'] or '（無）'} ×{e['n']}" for e in errors[:3])
    return [Finding(
        key="content.fetch", level="now", title="大量信源抓取失敗，這些信源的新文章進不來",
        impact=f"最近抓取過的 {r['fetched']} 個信源裡，{r['failing']} 個最近一次抓取失敗，讀者暫時看不到它們的新文章",
        heals="伺服器連外網的線路恢復後會按排程重新抓取，這段時間的新文章會晚到；線路不恢復就一直進不來", action="轉給 AI 檢查出網代理和抓取錯誤",
        detail=f"失敗信源佔 {_js_round(r['failing'] / r['fetched'] * 100)}%（門檻 {_num(FAILING_SHARE * 100)}% 且至少 {FAILING_SOURCES} 個）；常見錯誤：{common}",
        since=min(e["first"] for e in errors),
    )]  # fmt: skip


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _watching_processing(open_: dict) -> bool:
    """Processing is checked while collection and models run, and while either of its incidents is open."""
    return (collecting() and models_on()) or bool(open_.get("content.grouping")) or bool(open_.get("content.process"))


def selected_grouping(pool, now: float, open_: dict) -> list[Finding]:
    if not _watching_processing(open_):
        return []
    with pool.connection() as conn:
        items = waiting_selected_news(conn)
    waiting = [i for i in items if open_.get("content.grouping") or now - _ms(i["since"]) >= GROUPING_WARN_AFTER_MS]
    if not waiting:
        return []
    manual = sum(1 for i in waiting if i["recovery"] == "manual")
    receipt = any(i["recovery"] == "receipt" for i in waiting)
    if manual:
        heals = f"{manual} 條需要人工處理後才能恢復" + ("；其餘仍在自動恢復" if manual < len(waiting) else "")
    else:
        heals = "仍在等待自動恢復；付費結果未知的請求會在 30 分鐘後自動放行一次" if receipt else "還會自動重試，但等待已經超過正常範圍"
    detail = "；".join(
        f"{i['articleId']}{f' 回執 #{i['receiptId']}' if i['receiptId'] else ''}{f'：{js_slice(i['error'], 0, 120)}' if i['error'] else ''}"
        for i in waiting[:8]
    )
    return [Finding(
        key="content.grouping", level="now", title="有精選新聞卡在去重確認，暫未發布",
        impact=f"{len(waiting)} 條已達到精選條件的新聞仍未完成去重確認，還沒進入精選，讀者可能晚看到這些更新", heals=heals,
        action="轉給 AI 處理；核對計費，或在已有恢復授權內如實記錄計費未知後放行" if manual else "轉給 AI 檢查等待原因，避免繼續積壓", detail=detail, since=waiting[0]["since"],
    )]  # fmt: skip


def content_processing(pool, now: float, open_: dict) -> list[Finding]:
    if not _watching_processing(open_):
        return []
    # An article still new two hours after discovery is overdue, and one failed in the last three hours counts. While an announced incident is open, the windows stay
    # where its first detection put them: time passing cannot remove unfinished articles from it. The alert's duration starts at detection, not article age.
    incident = open_.get("content.process")
    detected_at = min(now, _parse(incident["since"])) if incident else now
    overdue_before = now - 2 * 3600_000
    params = {
        "waiting_after": _at(detected_at - 2 * 86400_000), "waiting_before": _at(max(overdue_before, detected_at) if incident else overdue_before), "failed_after": _at(detected_at - 3 * 3600_000),
    }  # fmt: skip
    unfinished = "(processing_state = 'new' AND discovered_at > %(waiting_after)s AND discovered_at < %(waiting_before)s) OR (processing_state = 'failed' AND discovered_at > %(failed_after)s)"
    with pool.connection() as conn:
        p = conn.execute(
            f"""SELECT count(*) FILTER (WHERE processing_state = 'new')::int AS waiting, count(*) FILTER (WHERE processing_state = 'failed')::int AS failed,
                       min(discovered_at) FILTER (WHERE processing_state = 'new') AS oldest FROM articles WHERE {unfinished}""",
            params,
        ).fetchone()
        if p["waiting"] < 10 and p["failed"] < 20 and not (incident and p["waiting"] + p["failed"] > 0):
            return []
        errors = conn.execute(
            f"""SELECT left(coalesce(processing_error, '（無）'), 120) AS error, count(*)::int AS n FROM articles
                WHERE ({unfinished}) AND processing_error IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 3""",
            params,
        ).fetchall()
    impact = (
        "；".join(
            x
            for x in (p["waiting"] > 0 and f"{p['waiting']} 篇積壓文章仍在等待處理", p["failed"] > 0 and f"{p['failed']} 篇處理失敗的文章仍未補齊")
            if x
        )
        + "，精選和熱點會缺內容"
    )
    detail = "；".join(
        x
        for x in (
            p["oldest"] and f"最老的一篇 {beijing_stamp(p['oldest'])} 收進",
            "；".join(f"{e['error']}（{e['n']}）" for e in errors) or "沒有記錄錯誤",
        )
        if x
    )
    return [Finding(
        key="content.process", level="now", title="新內容卡住了，進不了網站", impact=impact,
        heals="已失敗的文章需要恢復處理；服務恢復不代表積壓已經補齊" if p["failed"] > 0 else "排隊中的文章會繼續處理，仍需核實是否完成", action="轉給 AI 處理", detail=detail,
    )]  # fmt: skip


def refused_deliveries(pool, now: float, open_: dict) -> list[Finding]:
    """Content-group pushes the Feishu webhook refused (a removed bot, a changed address); nothing resends them."""
    incident = open_.get("deliveries.failed")
    since = _at((_parse(incident["since"]) if incident else now) - 86400_000)
    with pool.connection() as conn:
        refused = conn.execute(
            """SELECT count(*)::int AS n, max(t.note) AS target, left(max(d.response), 200) AS response FROM deliveries d JOIN notify_targets t ON t.key = d.target_key
               WHERE d.updated_at > %(since)s
                 AND (d.status = 'failed' OR (%(open)s AND (d.status = 'unknown' OR (d.status IN ('pending', 'sending') AND d.attempts > 0))))
                 AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'delivery.drop' AND a.subject = 'delivery:' || d.id AND a.created_at >= d.updated_at)""",
            {"since": since, "open": bool(incident)},
        ).fetchone()
    if not refused["n"]:
        return []
    return [Finding(
        key="deliveries.failed", level="today", title="飛書內容群有推送沒發出去", impact=f"{refused['n']} 條通知尚未確認送達{refused['target'] or '內容群'}", heals="不會自動重發",
        action="轉給 AI 處理；如果推送機器人被移出了群，需要你把它加回去", detail=refused["response"] or "",
    )]  # fmt: skip


def backup_age(pool, now: float, open_: dict | None = None) -> list[Finding]:
    if not backup_configured():
        return []
    with pool.connection() as conn:
        b = conn.execute("SELECT value FROM settings WHERE key = 'backup.last'").fetchone()
        # A failed archive updates backup.last too. Reuse the run history so repeated failures cannot reset the age; before the first success, count from the first
        # attempt rather than from now.
        runs = conn.execute(
            "SELECT max(finished_at) FILTER (WHERE status = 'ok') AS last_ok, min(started_at) AS first_attempt FROM job_runs WHERE job = 'ops.backup'"
        ).fetchone()
    value = b["value"] if b else None
    last_ok = max(_ms(runs["last_ok"]) if runs["last_ok"] else 0, _parse(value["at"]) if value and value.get("uploaded") else 0)
    since = last_ok or (_ms(runs["first_attempt"]) if runs["first_attempt"] else None) or now
    age = now - since
    if value and value.get("filesError"):
        state = f"資料庫已上傳，附件打包失敗：{value['filesError']}"
    else:
        state = "未上傳" if value and value.get("uploaded") is False else ""
    if age > 50 * 3600_000:
        return [Finding(
            key="backup.failed", level="today", title="資料庫備份連續兩天沒成功",
            impact=f"萬一伺服器出事，最近 {duration(age)} 的資料可能無法完整恢復" if last_ok else "還沒有成功的完整備份，伺服器出事時可能無法恢復資料", heals="不會", action="轉給 AI 處理",
            detail=f"{'最近一次成功' if last_ok else '首次備份嘗試'} {beijing_stamp(_at(since))}{f'；{state}' if state else ''}；看 ops.backup 的執行記錄", since=_at(since),
        )]  # fmt: skip
    if not b or not value.get("uploaded") or age > 30 * 3600_000:
        detail = f"最近一次 {beijing_stamp(_parse_at(value['at']))}{f'（{state}）' if state else ''}；看 ops.backup" if b else "還沒有成功的備份記錄"
        return [Finding(key="backup.stale", level="later", title="資料庫備份超過一天沒成功", detail=detail)]
    return []


def _parse_at(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def upstream_findings(pool, now: float, open_: dict | None = None) -> list[Finding]:
    """A follow-up: a minute window heals within the minute; only a day window leaves uncached images failing until midnight."""
    with pool.connection() as conn:
        img = conn.execute("SELECT value, updated_at FROM settings WHERE key = 'egress.imgproxy'").fetchone()
    if not img or img["value"].get("window") != "day" or now - _ms(img["updated_at"]) >= 86400_000:
        return []
    return [
        Finding(
            key="egress.imgproxy",
            level="later",
            title="圖片代理今天的流量額度用完了，未快取的圖片到零點前顯示不出來",
            detail=str(img["value"].get("reason")),
        )
    ]


def source_follow_ups(pool, now: float, open_: dict | None = None) -> list[Finding]:
    """One follow-up per source and problem, so fixing a source removes its own line. A quiet source is left to the weekly report."""
    if not collecting():
        return []
    out = []
    for group in source_health(pool, _at(now)):
        for p in SOURCE_PROBLEMS:
            for s in group[p["problem"]]:
                out.append(Finding(
                    key=f"sources.{p['problem']}.{s['id']}", level="later", title=f"{group['name']}信源「{s['name']}」{p['title']}",
                    detail=f"{s['id']}：{p['describe'](s)}；{p['action']}", observed_at=p["observed_at"](s) or None,
                ))  # fmt: skip
    return out


def unknown_outcomes(pool, now: float, open_: dict | None = None) -> list[Finding]:
    """Paid requests and content-group pushes whose outcome is not known yet."""
    out = []
    with pool.connection() as conn:
        r = conn.execute(
            """SELECT (SELECT count(*)::int FROM receipts WHERE (status = 'unknown' AND request->>'fallbackReceiptId' IS NULL)) AS receipts,
                      (SELECT string_agg(DISTINCT service || '/' || purpose, '、') FROM receipts WHERE (status = 'unknown' AND request->>'fallbackReceiptId' IS NULL)) AS services,
                      (SELECT count(*)::int FROM deliveries WHERE status = 'unknown') AS deliveries"""
        ).fetchone()
    if r["receipts"] > 0:
        out.append(
            Finding(
                key="receipts.unknown",
                level="later",
                title=f"{r['receipts']} 個付費請求的結果尚未確認",
                detail=f"{r['services']}；仍擋住內容發布的請求需核對計費或按已有授權放行",
            )
        )
    if r["deliveries"] > 0:
        out.append(
            Finding(
                key="deliveries.unknown",
                level="later",
                title=f"{r['deliveries']} 條飛書內容群推送不確定是否送達",
                detail="核對飛書內容群裡有沒有發出，再標記或補發",
            )
        )
    return out


def waiting_queues(pool, now: float, open_: dict | None = None) -> list[Finding]:
    """Runnable jobs (deferred ones excluded) that have waited more than two hours."""
    with pool.connection() as conn:
        queues = conn.execute(
            "SELECT name, count(*)::int AS n, min(start_after) AS oldest FROM jobs WHERE state IN ('created', 'retry') AND start_after <= now() AND name NOT LIKE 'cron.%' GROUP BY 1"
        ).fetchall()
    return [
        Finding(
            key=f"queue.{q['name']}",
            level="later",
            title=f"後臺任務排隊超過 2 小時：{q['name']}",
            detail=f"{q['n']} 個等待，最早的等了 {duration(now - _ms(q['oldest']))}",
        )
        for q in queues
        if now - _ms(q["oldest"]) > 2 * 3600_000
    ]


def transport_findings(pool, now: float, open_: dict | None = None) -> list[Finding]:
    return agent_transport_findings(pool, now)


# ── providers ───────────────────────────────────────────────────────────────────

PROVIDERS = {
    "llm": {"name": "預設模型服務", "where": "模型服務商的控制台"}, "zhipu": {"name": "智譜", "where": "智譜開放平臺"}, "dashscope": {"name": "阿里雲百鍊", "where": "阿里雲百鍊控制台"},
    "deepseek": {"name": "DeepSeek", "where": "DeepSeek 開放平臺"}, "mimo": {"name": "小米 MiMo", "where": "小米 MiMo 開放平臺"},
    "socialdata": {"name": "SocialData", "stops": "X（推特）上的新內容收不到", "where": "SocialData 後臺"},
    "jina": {"name": "Jina", "stops": "部分文章取不到正文", "where": "Jina 後臺"},
    "dajiala": {"name": "極致了（Dajiala）", "stops": "公眾號新文章收不到", "where": "極致了後臺"},
}  # fmt: skip


def provider_name(service: str) -> str:
    return PROVIDERS.get(service, {}).get("name", service)


def provider_console(service: str) -> str:
    return PROVIDERS.get(service, {}).get("where", f"{service} 後臺")


def provider_stops(pool, service: str, models: set[str] | None = None) -> str:
    p = PROVIDERS.get(service)
    if not p:
        return "相關功能停了"
    if p.get("stops"):
        return p["stops"]
    steps = M.steps_on_service(_conn(pool), service, models)
    return f"{'、'.join(steps)}可能受影響" if steps else "當前沒有處理步驟使用這家模型服務"


def provider_findings(pool, now: float, open_: dict) -> list[Finding]:
    """Paid services that refuse us (no balance, a dead key), and daily budgets used up."""
    out: list[Finding] = []
    ongoing = [k[len(REFUSED_PREFIX) :].split(":")[0] for k in open_ if k.startswith(REFUSED_PREFIX)]
    with pool.connection() as conn:
        failures = conn.execute(
            """SELECT service, model, error, count(*)::int AS n, max(started_at) AS latest FROM receipt_attempts a
               WHERE status = 'failed' AND (started_at > %s OR service = ANY(%s)) AND error IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM receipt_attempts good WHERE good.service = a.service AND good.model IS NOT DISTINCT FROM a.model AND good.status = 'received' AND good.started_at > a.started_at)
               GROUP BY service, model, error ORDER BY latest DESC""",
            (_at(now - 3600_000), ongoing),
        ).fetchall()
    models: dict[str, dict] = {}
    for p in failures:
        if not provider_access_refused(p["service"], p["error"]):
            continue
        key = f"{REFUSED_PREFIX}{p['service']}" + ("" if p["model"] is None else f":{urllib.parse.quote(p['model'], safe='')}")
        summary = models.setdefault(
            key, {"service": p["service"], "model": p["model"], "n": 0, "last": js_slice(p["error"], 0, 200), "latest": _ms(p["latest"])}
        )
        summary["n"] += p["n"]
    # Earlier versions combined every model under a service key without saving its scope. Freeze the unresolved models once for those existing events; their old timestamps
    # cannot establish that scope.
    for key, incident in open_.items():
        if not key.startswith(REFUSED_PREFIX) or ":" in key or incident.get("refusedModels") is not None:
            continue
        service = key[len(REFUSED_PREFIX) :]
        incident["refusedModels"] = [p["model"] for p in models.values() if p["service"] == service]
    refused: dict[str, dict] = {}
    for model_key, p in models.items():
        legacy_key = f"{REFUSED_PREFIX}{p['service']}"
        legacy = open_.get(legacy_key)
        key = legacy_key if legacy and p["model"] in (legacy.get("refusedModels") or []) else model_key
        if not open_.get(key) and p["latest"] <= now - 3600_000:
            continue
        summary = refused.setdefault(key, {"service": p["service"], "n": 0, "last": p["last"], "models": set()})
        summary["n"] += p["n"]
        if p["model"] is None:
            summary["models"] = None
        elif summary["models"] is not None:
            summary["models"].add(p["model"])
    for key, p in refused.items():
        service = p["service"]
        on_service = M.steps_on_service(_conn(pool), service, p["models"]) if p["models"] is not None else []
        unused = p["models"] is not None and not PROVIDERS.get(service, {}).get("stops") and not on_service
        out.append(Finding(
            key=key, level="later" if unused else "today", owner=None if unused else True,
            title=f"{provider_name(service)} 的舊模型限制尚未驗證" if unused else f"{provider_name(service)} 拒絕呼叫，需要核對賬號或額度",
            impact="當前處理步驟未使用這些受限模型；舊限制仍未驗證" if unused else provider_stops(pool, service, p["models"]), heals="需先恢復訪問，再確認失敗任務已接續",
            action=f"去{provider_console(service)}看餘額、套餐和消費上限；充值或恢復後讓 AI 核實呼叫並補跑剛才失敗的任務（後臺看著都正常的話，也把這條發給 AI 查原因）",
            detail=f"{'模型 ' + '、'.join(p['models']) + '；' if p['models'] else ''}最近一次成功後被拒 {p['n']} 次，尚未取得後續成功呼叫：{p['last']}",
        ))  # fmt: skip
    with pool.connection() as conn:
        capped = conn.execute(
            """SELECT b.service, b.per_day, count(a.id)::int AS used FROM budgets b
               JOIN receipt_attempts a ON a.service = b.service AND a.origin = 'live' AND a.started_at > now() - interval '1 day'
               WHERE b.per_day > 0 GROUP BY 1, 2 HAVING count(a.id) >= b.per_day"""
        ).fetchall()
    for c in capped:
        out.append(Finding(
            key=f"budget.day.{c['service']}", level="later", title=f"{provider_name(c['service'])} 過去 24 小時的呼叫額度用完了",
            impact=f"{provider_stops(pool, c['service'])}，直到額度隨時間騰出來", heals="會，額度按 24 小時滾動恢復", action="這次不用處理；如果經常出現，再決定要不要調高額度",
            detail=f"24 小時內 {c['used']} 次，上限 {c['per_day']}（budgets 表）",
        ))  # fmt: skip
    return out


# ── daily reports ───────────────────────────────────────────────────────────────


def _daily_date(key: str) -> str | None:
    return key[len(DAILY_PREFIX) :] if key.startswith(DAILY_PREFIX) and is_valid_date(key[len(DAILY_PREFIX) :]) else None


def _daily_scan_from(now: float) -> str:
    """Missing dailies are looked for, and announced ones followed, over the last seven Beijing calendar days."""
    return add_days(beijing_date(_at(now)), -6)


def daily_report_findings(pool, now: float, open_: dict) -> list[Finding]:
    if not collecting() or not models_on():
        return []
    today = beijing_date(_at(now))
    with pool.connection() as conn:
        activity = conn.execute(
            """SELECT (SELECT min(key) FROM reports WHERE kind = 'daily') AS first_report,
                      (SELECT min(started_at) FROM job_runs WHERE job = 'reports.compose') AS first_attempt"""
        ).fetchone()
    starts = sorted(
        d
        for d in (activity["first_report"], beijing_date(activity["first_attempt"]) if activity["first_attempt"] else None)
        if d and is_valid_date(d)
    )
    start = starts[0] if starts else today
    # New gaps and announced editions alike are followed for the scan window; settle_dailies closes older ones.
    frm = _daily_scan_from(now)
    dates = {d for d in (_daily_date(k) for k in open_) if d and d >= frm}
    date = frm
    while date <= today:
        if date >= start:
            dates.add(date)
        date = add_days(date, 1)
    edition = pack.site()["EDITION_TIMES"]["daily"]
    overdue = sorted(d for d in dates if now >= _ms(beijing_at(d, edition)) + 2 * 3600_000)
    if not overdue:
        return []
    with pool.connection() as conn:
        published = {r["key"] for r in conn.execute("SELECT key FROM reports WHERE kind = 'daily' AND key = ANY(%s)", (overdue,)).fetchall()}
    return [
        Finding(key=f"{DAILY_PREFIX}{d}", level="now", title=f"{d} 的日報還沒生成", impact=f"讀者看不到 {d} 的日報", heals="尚未補齊；需檢視執行記錄確認是否需要人工補做",
                action="把這條發給 AI 排查補跑", detail=f"reports daily {d} 不存在；看 reports.compose 的執行記錄")
        for d in overdue
        if d not in published
    ]  # fmt: skip


def settle_dailies(pool, open_: dict, found: set[str], now: float) -> dict:
    """Announced editions this round did not find, because generation is paused or they fell out of the scan window. Only an edition that exists has recovered; one still
    missing stays open quietly within the window, and past it is closed with one message instead of being reminded about forever."""
    missing: set[str] = set()
    sent: list[str] = []
    dates = [d for d in (_daily_date(k) for k in open_ if k not in found) if d]
    if not dates:
        return {"missing": missing, "sent": sent}
    with pool.connection() as conn:
        published = {r["key"] for r in conn.execute("SELECT key FROM reports WHERE kind = 'daily' AND key = ANY(%s)", (dates,)).fetchall()}
    for date in [d for d in dates if d not in published]:
        key = f"{DAILY_PREFIX}{date}"
        if date >= _daily_scan_from(now):
            missing.add(key)
            continue
        send_alert(
            f"{date} 的日報沒能補出，停止提醒",
            [f"讀者看不到 {date} 的日報；缺期從 {beijing_stamp(_parse_at(open_[key]['since']))} 起，已超出七天的檢查範圍"],
        )
        sent.append(f"{key}:expired")
        del open_[key]
    return {"missing": missing, "sent": sent}


# ── state and the round ─────────────────────────────────────────────────────────


def _setting(pool, key: str, default):
    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def alert_state(pool) -> dict:
    return dict(_setting(pool, "alerts.state", {}))


def returns_told(pool, now: float) -> dict:
    """When the owner was last told that a problem keeps coming back, by key, for the last day."""
    return {k: at for k, at in _setting(pool, "alerts.returns", {}).items() if now - _parse(at) < DAY_MS}


def saved_holds(pool) -> dict:
    return _setting(pool, "alerts.held", {})


def hold_alert(pool, key: str, reason: str, until: datetime, actor: str, now: float | None = None) -> dict:
    """The agent looked at an incident it was handed and judged it not a fault: the owner is not told of that incident before `until`, a day at most. Past it, or for a later
    incident, the handoff runs as before. Audited."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    if not reason or not reason.strip():
        raise ValueError("reason is required")
    if not (_ms(until) > now and _ms(until) <= now + DAY_MS):
        raise ValueError("hold until a time within the next day")
    open_ = alert_state(pool).get(key)
    if not open_ or not open_.get("handedAt") or open_.get("sentAt"):
        raise ValueError(f"not an open incident the agent has and the owner was not told of: {key}")
    before = saved_holds(pool).get(key)
    held = {"since": open_["since"], "until": _iso(until), "reason": reason.strip(), "by": actor, "at": _iso(_at(now))}
    with pool.connection() as conn, conn.transaction():
        # Merged by key in the row itself, so holds written at the same time all stand; ended ones are dropped here.
        conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES ('alerts.held', %s, %s)
               ON CONFLICT (key) DO UPDATE SET value = coalesce((SELECT jsonb_object_agg(k, v) FROM jsonb_each(settings.value) AS e(k, v)
                 WHERE (v->>'until')::timestamptz > %s), '{}'::jsonb) || EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (Jsonb({key: held}), actor, _at(now)),
        )
        audit(conn, actor, "alert.hold", f"alert:{key}", held["reason"], before, held)
    return held


CHECKS = [
    content_collection, sources_failing, selected_grouping, content_processing, daily_report_findings,  # readers affected now
    provider_findings, refused_deliveries, backup_age, upstream_findings,  # money, and things only the owner can do
    source_follow_ups, unknown_outcomes, transport_findings, waiting_queues,  # follow-ups
]  # fmt: skip


def collect_findings(pool, now: float | None = None, open_: dict | None = None) -> list[Finding]:
    """Everything wrong right now, with its level."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    announced = open_ if open_ is not None else alert_state(pool)
    out: list[Finding] = []
    for check in CHECKS:
        out.extend(check(pool, now, announced))
    return out


def _save_setting(pool, key: str, value, who: str = "alerts") -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, Jsonb(value), who),
        )


def check_alerts(pool, now: float | None = None) -> dict:
    """Every 10 minutes: new problems and recoveries of the now/today levels go out; follow-ups wait for 09:00."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    state = alert_state(pool)
    all_ = collect_findings(pool, now, state)
    handing = agent_configured() and not any(f.key == "webhook.transport" for f in all_)
    returns = returns_told(pool, now)
    holds = saved_holds(pool)
    sent: list[str] = []
    for f in all_:
        open_ = state.get(f.key)
        if f.level == "later":
            # Switching before the first check changes the impact, not whether access has recovered.
            if f.key.startswith(REFUSED_PREFIX) and not open_:
                state[f.key] = {"title": f.title, "since": _iso(_at(now))}
            continue
        if f.agent_only:
            since = open_["since"] if open_ else _iso(f.since or _at(now))
            state[f.key] = {"title": f.title, "since": since, "agentOnly": True}
            continue
        if handing and not f.owner and not (open_ or {}).get("sentAt"):
            handed_at = (open_ or {}).get("handedAt") or _iso(_at(now))
            hold = holds.get(f.key)
            held = bool(hold) and hold["since"] == (open_ or {}).get("since") and now < _parse(hold["until"])
            if held or now - _parse(handed_at) < HANDED_MS[f.level]:
                # Before this round's handoff is published, the earlier ones of the day are counted.
                earlier = 0 if open_ or returns.get(f.key) else handoffs(pool, f.key, _at(now - DAY_MS))
                if earlier >= RETURNS - 1:
                    msg = format_return(f, earlier + 1)
                    send_alert(msg["title"], msg["lines"])
                    returns[f.key] = _iso(_at(now))
                    sent.append(f"{f.key}:repeated")
                state[f.key] = {"title": f.title, "since": open_["since"] if open_ else _iso(f.since or _at(now)), "handedAt": handed_at}
                continue
        if open_ and open_.get("sentAt") and now - _parse(open_["sentAt"]) <= REPEAT_MS[f.level]:
            continue
        since = _parse_at(open_["since"]) if open_ else (f.since or _at(now))
        # Claim AI was notified only after the hub actually accepts this incident's wake; persist the occurrence timestamp first so a failed owner send or crash after
        # waking cannot mint a second incident ID on retry.
        woken = False
        if handing and f.key != "webhook.transport" and f.owner != "only" and f.action == "把這條發給 AI 排查處理":
            if not open_:
                entry = {"title": f.title, "since": _iso(since)}
                state[f.key] = entry
                with pool.connection() as conn:
                    conn.execute(
                        """INSERT INTO settings (key, value, updated_by) VALUES ('alerts.state', %s, 'alerts')
                           ON CONFLICT (key) DO UPDATE SET value = coalesce(settings.value, '{}'::jsonb) || %s, updated_at = now()""",
                        (Jsonb({f.key: entry}), Jsonb({f.key: entry})),
                    )
            f.since = since
            woken = wake_agent(pool, f, now)
        # A problem the agent has had and not closed: the owner learns it is the agent's, and for how long.
        shown = f
        if handing and not f.owner and open_ and open_.get("handedAt"):
            shown = _copy(f, heals=f"AI 接手 {duration(now - _parse(open_['handedAt']))} 還沒解決", action="問問 AI 卡在哪了；需要你動手的它會說明")
        elif woken:
            shown = _copy(f, action="已自動通知 AI 排查處理，修不好或要你拍板時會再找你；恢復後會再發一條通知")
        msg = format_alert(shown, since, now, bool(open_ and open_.get("sentAt")))
        send_alert(msg["title"], msg["lines"])
        state[f.key] = {**(open_ or {}), "title": f.title, "since": _iso(since), "sentAt": _iso(_at(now))}
        sent.append(f.key)
    found = {f.key for f in all_}
    dailies = settle_dailies(pool, state, found, now)
    sent.extend(dailies["sent"])
    for key, open_ in list(state.items()):
        if key in found or key in dailies["missing"]:
            continue
        # An agent-only incident was never told to the owner, so clearing it stays quiet too.
        if open_.get("agentOnly") or not open_.get("sentAt"):
            del state[key]
            continue
        dropped = 0
        if key == "deliveries.failed":
            with pool.connection() as conn:
                dropped = conn.execute(
                    """SELECT count(*)::int AS n FROM deliveries d
                       WHERE d.status = 'failed' AND d.updated_at > %s
                         AND EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'delivery.drop' AND a.subject = 'delivery:' || d.id AND a.created_at >= d.updated_at)""",
                    (_at(_parse(open_["since"]) - 86400_000),),
                ).fetchone()["n"]
        if dropped:
            send_alert(f"已結束跟進：{open_['title']}", [f"{dropped} 條舊通知已決定不再補發，未確認送達"])
        else:
            msg = format_recovery(open_["title"], _parse_at(open_["since"]), now)
            send_alert(msg["title"], msg["lines"])
        sent.append(f"{key}:{'closed' if dropped else 'recovered'}")
        del state[key]
    _save_setting(pool, "alerts.state", state)
    _save_setting(pool, "alerts.returns", returns)
    # Revisit every open incident: a crash between saving the owner's alert and dispatching cannot lose it. Transport receipts retain IDs across uncertain responses,
    # independently from owner reminders.
    for f in all_:
        if f.level != "later" and f.key != "webhook.transport" and f.owner != "only" and f.key in state:
            f.since = _parse_at(state[f.key]["since"])
            wake_agent(pool, f, now)
    flush_agent_events(pool)
    return {"open": list(state), "sent": sent}


def _copy(f: Finding, **changes) -> Finding:
    from dataclasses import replace

    return replace(f, **changes)


# ── dismissals and the digest ───────────────────────────────────────────────────


def _saved_dismissals(pool) -> dict:
    return _setting(pool, "alerts.dismissed", {})


def dismissed_follow_ups(pool, findings: list[Finding] | None = None, now: float | None = None) -> dict:
    """The closures still covering a current follow-up. "observed": the failures seen by its time: any new one reopens it. "ongoing": the condition itself, judged
    acceptable: it holds through new failures for a week, then the condition is listed and judged again."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    current = findings if findings is not None else source_follow_ups(pool, now)
    out = {}
    for key, closed in _saved_dismissals(pool).items():
        finding = next((f for f in current if f.key == key), None)
        if not finding:
            continue
        if closed.get("until"):
            if now < _parse(closed["until"]):
                out[key] = closed
        elif not finding.observed_at or _ms(finding.observed_at) <= _parse(closed["at"]):
            out[key] = closed
    return out


def dismiss_follow_up(pool, key: str, reason: str, actor: str, now: float | None = None, scope: str = "observed") -> dict:
    """Closes a current source follow-up as not to be fixed, with the reason (audited): the digest stops listing it."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    if not reason or not reason.strip():
        raise ValueError("reason is required")
    finding = next((f for f in source_follow_ups(pool, now) if f.key == key), None)
    if not finding:
        raise ValueError(f"not a current source follow-up: {key}")
    before = _saved_dismissals(pool).get(key)
    dismissed = {
        "title": finding.title,
        "reason": reason.strip(),
        "by": actor,
        "at": _iso(_at(now)),
        **({"until": _iso(_at(now + ONGOING_CLOSURE_MS))} if scope == "ongoing" else {}),
    }
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES ('alerts.dismissed', %s, %s)
               ON CONFLICT (key) DO UPDATE SET value = settings.value || EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (Jsonb({key: dismissed}), actor),
        )
        audit(conn, actor, "followup.dismiss", f"followup:{key}", dismissed["reason"], before, dismissed)
    return dismissed


def restore_follow_up(pool, key: str, reason: str, actor: str) -> dict | None:
    """Undoes a closure (audited): the digest lists the follow-up again while it lasts. None when it is not closed."""
    if not reason or not reason.strip():
        raise ValueError("reason is required")
    before = _saved_dismissals(pool).get(key)
    if not before:
        return None
    with pool.connection() as conn, conn.transaction():
        conn.execute("UPDATE settings SET value = value - %s::text, updated_by = %s, updated_at = now() WHERE key = 'alerts.dismissed'", (key, actor))
        audit(conn, actor, "followup.restore", f"followup:{key}", reason.strip(), before, None)
    return before


def send_digest(pool, now: float | None = None) -> dict:
    """09:00: the open follow-ups, one list; nothing when there are none. Closures whose problem is gone lapse here. Agent-only handoffs stay on the ops list and are not part
    of the owner's message."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    found = [f for f in collect_findings(pool, now) if f.level == "later"]
    dismissed = dismissed_follow_ups(pool, found, now)
    lapsed = [k for k in _saved_dismissals(pool) if k not in dismissed]
    if lapsed:
        with pool.connection() as conn:
            conn.execute("UPDATE settings SET value = value - %s::text[], updated_at = now() WHERE key = 'alerts.dismissed'", (lapsed,))
    items = [f for f in found if f.key not in dismissed and not f.agent_only]
    lines = [f"{i + 1}. {f.title}" + (f"\n   {f.detail}" if f.detail else "") for i, f in enumerate(items)]
    # Once the hub has the list, the agent works through it alone and passes on what is still the owner's. Without an agent, or when the list did not get through, the owner
    # gets it as before.
    taken = any(f.key not in dismissed for f in found) and wake_digest(pool, beijing_date(_at(now)), len(lines) or len(found))
    told_owner = len(lines) > 0 and not taken
    if told_owner:
        send_alert(f"📋 系統日報 · {beijing_day(_at(now))}", ["以下事項需要跟進，具體影響見各條說明；可把整條轉給 AI 處理。", *lines])
    flush_agent_events(pool)
    return {"items": len(lines), "toldOwner": told_owner}
