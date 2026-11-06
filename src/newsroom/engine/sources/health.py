"""Source-health evidence for the daily follow-ups and weekly report. A successful HTTP request is not proof of output; discovery channels and
editorial publishers are counted separately. Port of sources/health.ts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from newsroom.engine.budget import budget_message
from newsroom.engine.materials import SCHEDULED_AHEAD
from newsroom.engine.sources.xmlparse import js_number_text

RHYTHM_WINDOW_DAYS = 90  # how far back a source's own output is read to learn how often it produces something
RHYTHM_MIN_DAYS = 4  # dated days a source needs before its median gap means anything (three gaps)
RHYTHM_MULTIPLE = 4  # how many times its own median gap a source may stay quiet before it looks broken rather than slow

SOURCE_USE_NAMES = {"editorial": "編輯內容", "hot_signal": "熱度訊號"}


# A run error that is the network or the origin's answer, not our reading of the page.
TRANSIENT = (
    r"^([A-Za-z]*Error: )?(fetch failed|The operation was aborted due to timeout|Client network socket disconnected|other side closed"
    r"|Connect Timeout Error|DNS lookup failed|((jina|socialdata) )?HTTP [0-9]{3})"
)


def fails_often(failures: int, tries: int) -> bool:
    """Failing often enough to be worth fixing: at least five failures in the seven days and at least a tenth of the tries."""
    return failures >= 5 and failures / tries >= 0.1


def _text_number(v) -> str:
    return js_number_text(repr(float(v)))


SOURCE_PROBLEMS: list[dict[str, Any]] = [
    {"problem": "failing", "title": "連續抓取失敗", "action": "轉給 AI 檢查抓取錯誤", "observed_at": lambda s: s["last_failed_at"],
     "describe": lambda s: f"連續失敗 {s['fail_count']} 次{'，' + s['last_error'] if s['last_error'] else ''}"},
    {"problem": "unstable", "title": "反覆讀不出條目", "action": "即使最近成功也需檢查，避免繼續漏收", "observed_at": lambda s: s["last_unusable_at"],
     "describe": lambda s: f"近 7 天 {s['unusable']}/{s['runs']} 次抓到了頁面卻沒解析出條目"},
    {"problem": "undated", "title": "文章缺發布時間", "action": "缺時間可能讓新聞按歷史文章處理", "observed_at": lambda s: s["last_undated_at"],
     "describe": lambda s: f"近 7 天缺發布時間 {s['undated']} 篇"},
    {"problem": "revisions", "title": "文章反覆修訂", "action": "需核對正文是否混入變化內容", "observed_at": lambda s: s["last_repeated_at"],
     "describe": lambda s: f"近 7 天反覆修訂 {s['repeated']} 篇"},
    {"problem": "details", "title": "詳情補全失敗", "action": "檢視抓取記錄中的詳情地址與錯誤", "observed_at": lambda s: s["last_detail_failure_at"],
     "describe": lambda s: f"近 7 天失敗 {s['detail_failures']}/{s['detail_attempts']} 頁"},
]  # fmt: skip


def rhythm_multiple(s: dict) -> float | None:
    """How many times its own median gap a source has been quiet, or None while its own history is too thin to say."""
    if s["evidence_days"] < RHYTHM_MIN_DAYS or not s["median_gap_days"] or s["quiet_days"] is None:
        return None
    return s["quiet_days"] / s["median_gap_days"]


def _to_fixed_1(x: float) -> str:
    return str(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def rhythm_text(s: dict) -> str:
    """One quiet source in the weekly report: its own numbers, so a reader can see why it is listed."""
    times = rhythm_multiple(s)
    if times is None:
        run = f"近 7 天 {s['runs']} 次抓取、0 條新內容"
        if not s["ever_output"]:
            return f"{run}；從未收到過內容"
        if s["evidence_days"] > 0:
            return f"{run}；近 {RHYTHM_WINDOW_DAYS} 天裡只有 {s['evidence_days']} 天發過內容，還看不出固定頻率"
        return f"{run}；近 {RHYTHM_WINDOW_DAYS} 天沒有發過內容"
    return f"近 {s['quiet_days']} 天沒有新內容，平時約 {_text_number(s['median_gap_days'])} 天一條（{js_number_text(_to_fixed_1(times))} 倍）"


HEALTH_SQL = """
WITH runs AS (
  SELECT source_id, count(*) FILTER (WHERE status IN ('ok','failed') AND coalesce(error, '') NOT LIKE %(spent)s)::int AS runs,
    count(*) FILTER (WHERE status = 'failed' AND coalesce(error, '') NOT LIKE %(spent)s)::int AS failed,
    coalesce(sum((detail->>'detailAttempts')::int), 0)::int AS detail_attempts,
    coalesce(sum((detail->>'detailFailures')::int), 0)::int AS detail_failures,
    max(coalesce(finished_at, started_at)) FILTER (WHERE status = 'failed' AND coalesce(error, '') NOT LIKE %(spent)s) AS last_failed_at,
    count(*) FILTER (WHERE status = 'failed' AND coalesce(error, '') NOT LIKE %(spent)s AND NOT coalesce(error, '') ~ %(transient)s)::int AS unusable,
    max(coalesce(finished_at, started_at)) FILTER (WHERE status = 'failed' AND coalesce(error, '') NOT LIKE %(spent)s AND NOT coalesce(error, '') ~ %(transient)s) AS last_unusable_at,
    max(coalesce(finished_at, started_at)) FILTER (WHERE (detail->>'detailFailures')::int > 0) AS last_detail_failure_at
  FROM fetch_runs WHERE started_at >= %(since)s GROUP BY source_id
), seen AS (
  SELECT article_id, source_id FROM article_discoveries WHERE discovered_at >= %(since)s
  UNION SELECT id, source_id FROM articles WHERE discovered_at >= %(since)s
), discoveries AS (
  SELECT source_id, count(*)::int AS n FROM seen GROUP BY source_id
), items AS (
  SELECT source_id, count(*)::int AS n, count(*) FILTER (WHERE published_at IS NULL)::int AS undated,
    max(discovered_at) FILTER (WHERE published_at IS NULL) AS last_undated_at
  FROM articles WHERE discovered_at >= %(since)s GROUP BY source_id
), revisions AS (
  SELECT article_id, max(created_at) AS last_revision_at FROM article_revisions WHERE created_at >= %(since)s AND revision > 1
  GROUP BY article_id HAVING count(*) >= 5
), repeated AS (
  SELECT a.source_id, count(*)::int AS n, max(r.last_revision_at) AS last_repeated_at FROM revisions r JOIN articles a ON a.id = r.article_id GROUP BY a.source_id
)
SELECT s.id,s.name,s.participation_mode,s.created_at,s.health,s.fail_count,s.last_ok_at,left(s.last_error,200) AS last_error,
  coalesce(r.runs,0) AS runs, coalesce(r.failed,0) AS failed, coalesce(r.unusable,0) AS unusable, r.last_unusable_at, coalesce(r.detail_attempts,0) AS detail_attempts, coalesce(r.detail_failures,0) AS detail_failures,
  coalesce(d.n,0) AS discoveries, coalesce(a.n,0) AS items, coalesce(a.undated,0) AS undated, coalesce(e.n,0) AS repeated,
  r.last_failed_at, r.last_detail_failure_at, a.last_undated_at, e.last_repeated_at,
  (EXISTS (SELECT 1 FROM articles history WHERE history.source_id = s.id)
    OR EXISTS (SELECT 1 FROM article_discoveries history WHERE history.source_id = s.id)) AS ever_output
FROM sources s LEFT JOIN runs r ON r.source_id = s.id LEFT JOIN discoveries d ON d.source_id = s.id
  LEFT JOIN items a ON a.source_id = s.id LEFT JOIN repeated e ON e.source_id = s.id
WHERE s.enabled AND s.kind NOT IN ('external','mp_account') AND s.participation_mode IN ('editorial','hot_signal')
ORDER BY s.name,s.id
"""

RHYTHM_SQL = """
WITH rhythm AS (
  SELECT source_id, day FROM (
    SELECT source_id,
      date_trunc('day', CASE WHEN backfill_reason = 'first-import' THEN discovered_at
        ELSE coalesce(published_at, discovered_at) END)::date AS day
    FROM articles WHERE source_id = ANY(%(ids)s)
      AND discovered_at >= %(rhythm_since)s::date - %(ahead)s * interval '1 second'
    UNION ALL
    SELECT d.source_id, date_trunc('day', d.discovered_at)::date
    FROM article_discoveries d JOIN articles a ON a.id = d.article_id
    WHERE d.source_id = ANY(%(ids)s) AND d.discovered_at >= %(rhythm_since)s::date AND a.source_id <> d.source_id
  ) dated
  WHERE day BETWEEN %(rhythm_since)s::date AND %(today)s::date
), rhythm_days AS (
  SELECT DISTINCT source_id, day FROM rhythm
), rhythm_gaps AS (
  SELECT source_id, day, day - lag(day) OVER (PARTITION BY source_id ORDER BY day) AS gap FROM rhythm_days
)
SELECT source_id AS id, count(*)::int AS evidence_days,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) FILTER (WHERE gap IS NOT NULL) AS median_gap_days,
  (%(today)s::date - max(day))::int AS quiet_days
FROM rhythm_gaps GROUP BY source_id
"""


def _with_rhythm(conn, sources: list[dict], now: datetime) -> list[dict]:
    """The given sources' own rhythm, read through their own index entries rather than the whole window."""
    if not sources:
        return []
    # The statement takes the calendar day of the UTC instant, as the TypeScript one does when it casts an ISO timestamp.
    utc = now.astimezone(UTC)
    since: date = (utc - timedelta(days=RHYTHM_WINDOW_DAYS)).date()
    rows = conn.execute(
        RHYTHM_SQL, {"ids": [s["id"] for s in sources], "rhythm_since": since, "ahead": SCHEDULED_AHEAD.total_seconds(), "today": utc.date()}
    ).fetchall()
    measured = {r["id"]: r for r in rows}
    out = []
    for s in sources:
        r = measured.get(s["id"])
        out.append({**s, "evidence_days": r["evidence_days"] if r else 0, "median_gap_days": r["median_gap_days"] if r else None, "quiet_days": r["quiet_days"] if r else None})
    return out


def source_health(pool, now: datetime | None = None) -> list[dict]:
    """Seven days of outcomes, including intermittent failures that a later success clears on sources."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)
    with pool.connection() as conn:
        rows = conn.execute(HEALTH_SQL, {"spent": budget_message("%", "%"), "transient": TRANSIENT, "since": since}).fetchall()
        # Quiet is the old seven-day signal, kept as the trigger. Whether it is late is read from the source's own rhythm.
        quiet = _with_rhythm(conn, [s for s in rows if s["created_at"] < since and s["discoveries"] == 0], now)
    out = []
    for mode, name in SOURCE_USE_NAMES.items():
        sources = [s for s in rows if s["participation_mode"] == mode]
        own = [s for s in quiet if s["participation_mode"] == mode]
        late = []
        for s in own:
            times = rhythm_multiple(s)
            if times is not None and times > RHYTHM_MULTIPLE:
                late.append((s, times))
        late.sort(key=lambda x: -x[1])
        out.append({
            "mode": mode, "name": name, "sources": sources,
            "failing": [s for s in sources if s["health"] == "failing"],
            "unstable": [s for s in sources if s["health"] != "failing" and fails_often(s["unusable"], s["runs"])],
            "overdue": [s for s, _ in late],
            "unknown": [s for s in own if rhythm_multiple(s) is None],
            "undated": [s for s in sources if s["undated"] > 0] if mode == "editorial" else [],
            "revisions": [s for s in sources if s["repeated"] > 0] if mode == "editorial" else [],
            "details": [s for s in sources if fails_often(s["detail_failures"], s["detail_attempts"])],
        })  # fmt: skip
    return out


def source_health_list(rows: list[dict], describe) -> str:
    """Every listed source with its own numbers."""
    return "；".join(f"{s['name']}（{s['id']}）：{describe(s)}" for s in rows)
