"""Reading the signals of the database for the heat grading (stories of the hot ranking, and articles that stand for themselves), and the
backtest that says how many stories a person marked S are graded S or A."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from newsroom.collect.score import HeatInput, score_heat
from newsroom.collect.x_velocity import x_velocities


@dataclass
class Loaded:
    by_story: dict[int, HeatInput] = field(default_factory=dict)
    by_article: dict[str, HeatInput] = field(default_factory=dict)


def _latest_and_rate(points: list[tuple[float, datetime]]) -> tuple[float, float | None]:
    """Value now, and the rise per hour between the last two points (None with fewer than two)."""
    last = points[0]
    if len(points) < 2:
        return last[0], None
    hours = (last[1] - points[1][1]).total_seconds() / 3600
    return last[0], (last[0] - points[1][0]) / hours if hours > 0 else None


def _best(a: float | None, b: float | None) -> float | None:
    return a if b is None else b if a is None else max(a, b)


def load_heat_inputs(conn, config: dict, now: datetime, story_ids: list[int] | None = None, article_ids: list[str] | None = None) -> Loaded:
    """The inputs of the grading for stories and articles, read as of `now`."""
    out = Loaded()
    window_start = now - timedelta(hours=config["windows"]["resonanceHours"])
    members: list[tuple[str, str, str, str, datetime | None]] = []  # (owner, identity key, source id, tier, observed_at)
    if story_ids:
        for r in conn.execute(
            """SELECT ss.story_id, a.identity_key, a.source_id, s.tier, ss.observed_at FROM story_signals ss
                   JOIN articles a ON a.id = ss.article_id JOIN sources s ON s.id = a.source_id
                   WHERE ss.story_id = ANY(%s) AND ss.observed_at <= %s""",
            (list(story_ids), now),
        ).fetchall():
            members.append((f"s:{r['story_id']}", r["identity_key"], r["source_id"], r["tier"], r["observed_at"]))
    if article_ids:
        for r in conn.execute("SELECT id, identity_key, source_id FROM articles WHERE id = ANY(%s)", (list(article_ids),)).fetchall():
            members.append((f"a:{r['id']}", r["identity_key"], r["source_id"], "manual", None))
    keys = sorted({m[1] for m in members})
    series: dict[tuple[str, str], list[tuple[float, datetime]]] = {}
    if keys:
        for r in conn.execute(
            """SELECT identity_key, metric, value, observed_at FROM (
                     SELECT identity_key, metric, value, observed_at,
                            row_number() OVER (PARTITION BY identity_key, metric ORDER BY observed_at DESC) AS rn
                     FROM signal_points WHERE identity_key = ANY(%s) AND observed_at <= %s) t
                   WHERE rn <= 2 ORDER BY identity_key, metric, observed_at DESC""",
            (keys, now),
        ).fetchall():
            series.setdefault((r["identity_key"], r["metric"]), []).append((r["value"], r["observed_at"]))
    x_by_key = x_velocities(conn, keys)

    owners: dict[str, list[tuple[str, str, str, str, datetime | None]]] = {}
    for m in members:
        owners.setdefault(m[0], []).append(m)
    for owner, lst in owners.items():
        in_window = {m[2] for m in lst if m[4] and m[4] > window_start}
        inp = HeatInput(resonance_sources=1 if owner.startswith("a:") else len(in_window), identity_keys=tuple(dict.fromkeys(m[1] for m in lst)))
        tiers = [m[3] for m in lst]
        inp.best_tier = next((t for t in ("manual", "T1", "T1_5", "T2") if t in tiers), None)
        for _, key, _, _, _ in lst:
            if key in x_by_key:
                inp.x_velocity = _best(inp.x_velocity, x_by_key[key])
            if (key, "hn_points") in series:
                inp.hn_points_per_hour = _best(inp.hn_points_per_hour, _latest_and_rate(series[(key, "hn_points")])[1])
            if (key, "gh_stars") in series:
                inp.stars_per_hour = _best(inp.stars_per_hour, _latest_and_rate(series[(key, "gh_stars")])[1])
            if (key, "hf_likes") in series:
                inp.hf_likes_per_hour = _best(inp.hf_likes_per_hour, _latest_and_rate(series[(key, "hf_likes")])[1])
            if (key, "hf_upvotes") in series:
                inp.hf_upvotes = _best(inp.hf_upvotes, _latest_and_rate(series[(key, "hf_upvotes")])[0])
            if (key, "newsletter_mentions") in series:
                inp.newsletter_mentions = _best(inp.newsletter_mentions, _latest_and_rate(series[(key, "newsletter_mentions")])[0])
        (out.by_story.__setitem__(int(owner[2:]), inp) if owner.startswith("s:") else out.by_article.__setitem__(owner[2:], inp))
    return out


@dataclass
class BacktestRow:
    story: str
    label: str
    tier: str | None
    score: int | None
    found: bool


@dataclass
class BacktestResult:
    rows: list[BacktestRow]
    s_hits: int
    s_total: int
    s_rate: float | None  # 0 to 1; None without any S


def backtest_heat(conn, labels: list[dict], config: dict, now: datetime | None = None) -> BacktestResult:
    """Grades the stories a person labelled, from the data as it was at each label's time, and says how many S ones were found."""
    now = now or datetime.now(timezone.utc)
    rows: list[BacktestRow] = []
    for label in labels:
        story = conn.execute("SELECT id FROM stories WHERE public_id::text = %s", (label["story"],)).fetchone()
        if not story:
            rows.append(BacktestRow(label["story"], label["label"], None, None, False))
            continue
        at = datetime.fromisoformat(label["asOf"].replace("Z", "+00:00")) if label.get("asOf") else now
        inp = load_heat_inputs(conn, config, at, story_ids=[story["id"]]).by_story.get(story["id"]) or HeatInput()
        result = score_heat(inp, config)
        rows.append(BacktestRow(label["story"], label["label"], result.tier, result.score, True))
    s = [r for r in rows if r.label == "S" and r.found]
    hits = sum(1 for r in s if r.tier in ("S", "A"))
    return BacktestResult(rows, hits, len(s), hits / len(s) if s else None)
