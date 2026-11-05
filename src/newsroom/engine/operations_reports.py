"""The Monday source-health report for the ops chat. It goes through the same gated channel as the alerts (off unless FEISHU_INTERNAL_ENABLED). Port of operations/reports.ts."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from newsroom.engine.admin.sources import USE_LABELS
from newsroom.engine.notify_feishu import send_alert
from newsroom.engine.operations_alerts import dismissed_follow_ups
from newsroom.engine.sources.health import SOURCE_PROBLEMS, rhythm_text, source_health, source_health_list
from newsroom.engine.time import beijing_day

MODULE_ONLY = "(participation_mode = 'isolated' AND uses <> '{}')"  # sources that serve modules only (out of the news, with uses)


def _uses_text(uses: list[str]) -> str:
    return "、".join(USE_LABELS.get(u, u) for u in uses)


def pct(a: float, b: float) -> str:
    """The change from b to a: "+12%", "—" without a b."""
    if not b:
        return "—"
    v = (a - b) / b * 100
    rounded = math.floor(abs(v) + 0.5)  # toFixed(0): halves go away from zero, and a small negative change keeps its sign ("-0")
    return f"{'-' if v < 0 else '+' if a >= b else ''}{rounded}%"


def n(v: int) -> str:
    """A count with thousands separators."""
    return f"{v:,}"


def _by(actor: str) -> str:
    """Who changed a source, as the owner reads it: the admin pages, or the AI's scripts."""
    if actor.startswith("admin:"):
        return "後臺"
    return "AI" if re.match(r"^(ops-script|codex)", actor) else actor


def _brief(reason: str | None) -> str:
    if not reason:
        return "沒寫原因"
    return reason[:60] + "…" if len(reason) > 60 else reason


def _excluded(config) -> list[str]:
    c = config or {}
    return [*(c.get("denyUrlPrefixes") or []), *(c.get("denyCategories") or [])]


def source_changes(pool, since: datetime) -> list[str]:
    """This week's pauses still in force and the exclusions added, with who and why. Agents pause and exclude on their own, and nothing else shows a wrong one."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"""SELECT s.id, s.name, s.enabled, {MODULE_ONLY} AS module_only, s.uses, a.actor, a.reason, a.created_at AS at, a.before, a.after
                FROM audit_log a JOIN sources s ON a.subject = 'source:' || s.id
                WHERE a.action = 'source.update' AND a.created_at >= %s ORDER BY a.created_at, a.id""",
            (since,),
        ).fetchall()
    paused: dict[str, str] = {}
    fenced: list[str] = []
    fenced_ids: set[str] = set()
    for r in rows:
        who = f"{_by(r['actor'])}，{beijing_day(r['at'])}" + (f"，只給{_uses_text(r['uses'])}用" if r["module_only"] else "")
        before, after = r["before"] or {}, r["after"] or {}
        # The audit keeps the fields a save sent; a form saved again on a paused source repeats enabled=false.
        if after.get("enabled") is False and before.get("enabled") is not False and not r["enabled"]:
            paused[r["id"]] = f"· {r['name']}（{who}）：{_brief(r['reason'])}"
        added = [rule for rule in _excluded(after.get("config")) if rule not in _excluded(before.get("config"))]
        if added:
            fenced.append(f"· {r['name']}（{who}，排除 {'、'.join(added)}）：{_brief(r['reason'])}")
            fenced_ids.add(r["id"])
    return [
        *([f"本週停用、現在仍停著的信源 {len(paused)} 個：", *paused.values()] if paused else []),
        *([f"本週排除了部分入口的信源 {len(fenced_ids)} 個：", *fenced] if fenced else []),
    ]


def source_health_weekly(pool, now: float | None = None) -> dict:
    """Monday 09:00: how the sources did over the last seven days, quiet ones included (the daily follow-ups leave them out)."""
    now = now if now is not None else datetime.now(UTC).timestamp() * 1000
    at = datetime.fromtimestamp(now / 1000, UTC)
    since = datetime.fromtimestamp((now - 7 * 86400_000) / 1000, UTC)
    before = datetime.fromtimestamp((now - 14 * 86400_000) / 1000, UTC)
    with pool.connection() as conn:
        counts = conn.execute(
            f"""SELECT count(*) FILTER (WHERE enabled)::int AS enabled, count(*) FILTER (WHERE enabled AND health = 'failing')::int AS failing,
                       count(*) FILTER (WHERE enabled AND health = 'degraded')::int AS degraded, count(*) FILTER (WHERE created_at >= %s)::int AS added
                FROM sources WHERE NOT {MODULE_ONLY}""",
            (since,),
        ).fetchone()
        items = conn.execute(
            f"""SELECT count(*) FILTER (WHERE discovered_at >= %(since)s)::int AS week, count(*) FILTER (WHERE discovered_at >= %(before)s AND discovered_at < %(since)s)::int AS prev,
                       (SELECT count(*)::int FROM publications p WHERE p.selected AND p.visibility <> 'withdrawn' AND p.discovered_at >= %(since)s) AS selected
                FROM articles WHERE discovered_at >= %(before)s AND source_id NOT IN (SELECT id FROM sources WHERE {MODULE_ONLY})""",
            {"since": since, "before": before},
        ).fetchone()
        served = conn.execute(f"SELECT uses, health = 'failing' AS failing FROM sources WHERE enabled AND {MODULE_ONLY}").fetchall()
    groups = source_health(pool, at)
    dismissed = dismissed_follow_ups(pool)
    lines = [
        f"本週收錄 {n(items['week'])} 條（上週 {n(items['prev'])}，{pct(items['week'], items['prev'])}），其中精選 {n(items['selected'])} 條",
        f"在用信源 {counts['enabled']} 個，本週新增 {counts['added']} 個；抓取失敗 {counts['failing']} 個，不太穩定 {counts['degraded']} 個",
    ]
    if served:
        per_use: dict[str, int] = {}
        for s in served:
            for u in s["uses"]:
                per_use[u] = per_use.get(u, 0) + 1
        each = "、".join(f"{_uses_text([u])} {count}" for u, count in per_use.items())
        lines.append(f"另有隻給模組用的信源 {len(served)} 個（{each}），抓取失敗 {sum(1 for s in served if s['failing'])} 個")
    for group in groups:
        lines += [
            "",
            f"{group['name']}：{len(group['sources'])} 個在用信源；本週收錄 {n(sum(s['items'] for s in group['sources']))} 條；連續失敗 {len(group['failing'])} 個、反覆失敗 {len(group['unstable'])} 個、超出自身更新節奏 {len(group['overdue'])} 個、7 天沒有新內容 {len(group['unknown'])} 個",
        ]
        for p in SOURCE_PROBLEMS:
            if not group[p["problem"]]:
                continue

            def describe(s, p=p):
                d = dismissed.get(f"sources.{p['problem']}.{s['id']}")
                return p["describe"](s) + (f"（已判定不修：{d['reason']}）" if d else "")

            lines.append(f"{p['title']}：{source_health_list(group[p['problem']], describe)}")
        if group["overdue"]:
            lines.append(f"超出自身更新節奏（先對照原站，區分採集失效與它自己變慢）：{source_health_list(group['overdue'], rhythm_text)}")
        if group["unknown"]:
            lines.append(f"7 天沒有新內容（近期文章太少還看不出固定更新頻率，或從未收到內容）：{source_health_list(group['unknown'], rhythm_text)}")
    changes = source_changes(pool, since)
    if changes:
        lines += ["", *changes]
    overdue = sum(len(g["overdue"]) for g in groups)
    silent = overdue + sum(len(g["unknown"]) for g in groups)  # quiet sources still worth a look: past their own rhythm, or not judgeable
    follow_up = (
        counts["failing"] > 0
        or any(s["failing"] for s in served)
        or silent > 0
        or any(g["failing"] or g["unstable"] or g["undated"] or g["revisions"] or g["details"] for g in groups)
    )
    lines += ["", "需要處理的話，把這條轉給 AI。" if follow_up else "沒有需要處理的信源。"]
    send_alert("📊 信源週報", lines)
    return {"failing": counts["failing"], "silent": silent, "overdue": overdue}
