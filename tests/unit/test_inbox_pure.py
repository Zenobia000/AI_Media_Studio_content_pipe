"""Ported from modules/inbox/tests: frontmatter, format, cross-day, events, heat (the parts that need no database)."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.collect.calendar import just_ended, mentions_ended_event, parse_events, starting_tomorrow
from newsroom.collect.inbox import (
    Candidate,
    FailingSource,
    ManagedBlockError,
    Manual,
    StateRow,
    parse_annotations,
    plan_inbox,
    render_file,
    tier_of,
    with_health,
)
from newsroom.collect.score import HeatInput, describe_breakdown, load_heat_config, program_for, score_heat
from newsroom.core.frontmatter import read_front_matter, update_front_matter

UTC = timezone.utc

# ── front-matter ────────────────────────────────────────────────────────────────────────

DOC = "---\nid: t-1\nstatus: new\ncustom: 保留\n---\n# 標題\n\n人寫的內文\n"


def test_update_changes_named_fields_and_keeps_unknown_fields_and_body():
    out = update_front_matter(DOC, {"status": "picked", "angle": "價格: 比較"})
    assert out == '---\nid: t-1\nstatus: picked\ncustom: 保留\nangle: "價格: 比較"\n---\n# 標題\n\n人寫的內文\n'
    fields, body = read_front_matter(out)
    assert fields == {"id": "t-1", "status": "picked", "custom": "保留", "angle": "價格: 比較"}
    assert body == "# 標題\n\n人寫的內文\n"


def test_nothing_to_change_gives_the_same_bytes():
    assert update_front_matter(DOC, {}) == DOC
    crlf = DOC.replace("\n", "\r\n")
    assert update_front_matter(crlf, {"status": "new"}) == crlf
    assert update_front_matter("---\na: 1\n---", {"a": "2"}) == "---\na: 2\n---"


def test_file_without_front_matter_gets_one_in_front():
    assert update_front_matter("內文\n", {"id": "x"}) == "---\nid: x\n---\n內文\n"
    assert update_front_matter("---\nbroken\n", {"id": "x"}) == "---\nid: x\n---\n---\nbroken\n"


# ── inbox format ────────────────────────────────────────────────────────────────────────


def cand(cid: str, n: int) -> Candidate:
    return Candidate(
        id=cid,
        title=f"題目 {cid}",
        tier=tier_of(n),
        participant_count=n,
        source_names=["A", "B"],
        first_seen="01:42",
        suggested_program="ai-today",
        suggested_formats=["threads"],
    )


def test_regenerating_keeps_ticks_and_notes_and_adds_new_candidates():
    first = render_file("2026-10-17", [cand("cand_aaa", 5)], None)
    edited = first.replace("- [ ] **題目 cand_aaa**", "- [x] **題目 cand_aaa**").replace("備註：", "備註：從工具角度切入")
    second = render_file("2026-10-17", [cand("cand_aaa", 5), cand("cand_bbb", 2)], edited)
    notes = parse_annotations(second)
    assert notes["cand_aaa"].checked is True
    assert notes["cand_aaa"].note == "從工具角度切入"
    assert notes["cand_bbb"].checked is False


def test_a_damaged_managed_block_is_not_overwritten():
    with pytest.raises(ManagedBlockError):
        render_file("2026-10-17", [cand("cand_aaa", 5)], "# 2026-10-17 候選\n")


def test_hand_added_candidates_come_first_with_the_persons_note_filled_in():
    manual = cand("cand_man", 1)
    manual.manual = Manual(note="從工具角度切入\n再補一句", program="tool-review", formats="threads, reel")
    text = render_file("2026-10-17", [cand("cand_aaa", 5), manual], None)
    assert text.index("## 手動新增") < text.index("## S 級")
    assert "節目：tool-review" in text
    assert "格式：threads, reel" in text
    assert "備註：從工具角度切入；再補一句" in text


def test_health_block_is_replaced_where_it_is_or_added_at_the_foot():
    text = render_file("2026-10-17", [cand("cand_aaa", 5)], None)
    once = with_health(text, [FailingSource("Feed", 3, "HTTP  500\n boom", None)])
    assert "從未成功" in once and "HTTP 500 boom" in once
    twice = with_health(once, [])
    assert twice.count("收集狀況") == 1 and "都正常" in twice


# ── cross-day ───────────────────────────────────────────────────────────────────────────


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def utc_day(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def row(cid: str, status: str, first: datetime) -> StateRow:
    return StateRow(cid, status, first, cand(cid, 3))


def ids(listed):
    return [c.id + (f":{c.carried}" if c.carried else "") for c in listed]


def test_three_days_carry_skip_and_keep_ticked():
    day1 = plan_inbox([cand("a", 3), cand("b", 3)], [], at(17, 10), "2026-10-17", utc_day)
    assert ids(day1.listed) == ["a", "b"] and day1.skip == []
    rows2 = [row("a", "picked", at(17, 10)), row("b", "new", at(17, 10))]
    day2 = plan_inbox([], rows2, at(18, 10), "2026-10-18", utc_day)
    assert ids(day2.listed) == ["a:已勾選", "b:昨日"] and day2.skip == []
    day3 = plan_inbox([], rows2, at(19, 11), "2026-10-19", utc_day)
    assert ids(day3.listed) == ["a:已勾選"] and day3.skip == ["b"]
    rows4 = [row("a", "opened", at(17, 10)), row("b", "skipped", at(17, 10))]
    assert plan_inbox([cand("b", 3)], rows4, at(20, 10), "2026-10-20", utc_day).listed == []


def test_a_candidate_first_listed_today_is_not_marked_carried():
    rows = [row("a", "new", at(17, 8))]
    assert ids(plan_inbox([], rows, at(17, 20), "2026-10-17", utc_day).listed) == ["a"]
    assert ids(plan_inbox([cand("a", 3)], rows, at(17, 20), "2026-10-17", utc_day).listed) == ["a"]


# ── calendar ────────────────────────────────────────────────────────────────────────────

TABLE = """# 發表會行事曆

| 日期 | 時間 | 活動 | 關鍵字 | 官方連結 |
|---|---|---|---|---|
| 2026-11-05 | 10:00-12:30 | Acme DevDay | Acme, 新模型 | https://acme.example/devday |
| 2026-11-06 | 02:00 | Foo Launch | foo | |
| 壞日期 | 10:00 | 壞掉的 | x | |
| | | | | |
"""


def test_table_gives_events_in_taipei_time_and_says_which_rows_it_skipped():
    events, problems = parse_events(TABLE)
    assert len(events) == 2
    assert events[0].starts_at.astimezone(UTC).isoformat() == "2026-11-05T02:00:00+00:00"
    assert events[0].ends_at.astimezone(UTC).isoformat() == "2026-11-05T04:30:00+00:00"
    assert events[0].keywords == ["Acme", "新模型"]
    assert events[1].ends_at - events[1].starts_at == timedelta(hours=2)
    assert events[1].url is None
    assert len(problems) == 1
    assert parse_events(TABLE)[0][0].id == events[0].id


def test_story_naming_a_keyword_of_a_just_ended_event_is_about_it():
    events, _ = parse_events(TABLE)
    after = just_ended(events, datetime(2026, 11, 5, 6, tzinfo=UTC))
    assert len(after) == 1
    assert mentions_ended_event("ACME unveils its model", after)
    assert not mentions_ended_event("Other news", after)
    assert just_ended(events, datetime(2026, 11, 5, 3, tzinfo=UTC)) == []
    assert just_ended(events, datetime(2026, 11, 7, 6, tzinfo=UTC)) == []


def test_the_day_before_an_event_is_due_for_preparation():
    events, _ = parse_events(TABLE)
    taipei = timezone(timedelta(hours=8))
    assert [e.title for e in starting_tomorrow(events, datetime(2026, 11, 4, 9, tzinfo=taipei))] == ["Acme DevDay"]
    assert starting_tomorrow(events, datetime(2026, 11, 3, 9, tzinfo=taipei)) == []


# ── heat ────────────────────────────────────────────────────────────────────────────────

CONFIG = load_heat_config()
HOT = HeatInput(
    resonance_sources=1, hn_points_per_hour=40, stars_per_hour=10, newsletter_mentions=2, best_tier="T1", identity_keys=("url:https://example.com/a",)
)


def with_signals(**over):
    config = copy.deepcopy(CONFIG)
    for name, patch in over.items():
        config["signals"][name].update(patch)
    return config


def test_same_input_same_score_and_breakdown():
    first = score_heat(HOT, CONFIG)
    assert score_heat(copy.deepcopy(HOT), copy.deepcopy(CONFIG)) == first
    assert abs(sum(b.points for b in first.breakdown) - first.score) <= 1
    assert 0 <= first.score <= 100


def test_a_signal_switched_off_has_weight_zero_and_raises_no_error():
    inp = HeatInput(**{**HOT.__dict__, "x_velocity": 3, "local_gap": True})
    off = score_heat(inp, CONFIG)
    for name in ("x_velocity", "local_gap"):
        b = next(x for x in off.breakdown if x.signal == name)
        assert (b.enabled, b.weight, b.points) == (False, 0, 0)
    on = score_heat(inp, with_signals(x_velocity={"enabled": True}, local_gap={"enabled": True}))
    assert on.score != off.score
    no_hn = score_heat(HeatInput(**{**HOT.__dict__, "hn_points_per_hour": None}), with_signals(hn_velocity={"enabled": False}))
    assert next(b for b in no_hn.breakdown if b.signal == "hn_velocity").weight == 0
    assert score_heat(HeatInput(resonance_sources=0), CONFIG).score == 0


def test_s_a_and_b():
    s3 = score_heat(HeatInput(resonance_sources=3), CONFIG)
    assert s3.tier == "S" and "6 小時內 3 個來源共振" in ",".join(s3.reasons)
    ended = score_heat(HeatInput(resonance_sources=1, event_ended=True), CONFIG)
    assert ended.tier == "S" and "發表會" in ended.reasons[0]
    assert score_heat(HeatInput(resonance_sources=2), CONFIG).tier == "A"
    b = score_heat(HeatInput(resonance_sources=1, best_tier="T1"), CONFIG)
    assert b.tier == "B" and "沒有達到 A 級" in b.reasons[0]
    by_score = score_heat(HeatInput(resonance_sources=1, hn_points_per_hour=200, stars_per_hour=100, newsletter_mentions=5, best_tier="T1"), CONFIG)
    assert by_score.score >= CONFIG["tiers"]["A"]["minScore"] and by_score.tier != "B"


def test_each_tier_suggests_formats_and_the_program_follows_what_the_thing_is():
    assert score_heat(HeatInput(resonance_sources=3), CONFIG).formats == ["threads", "carousel", "reel"]
    assert score_heat(HeatInput(resonance_sources=2), CONFIG).formats == ["carousel", "reel"]
    assert score_heat(HeatInput(resonance_sources=1), CONFIG).formats == ["threads"]
    assert program_for(["arxiv:2410.12345"]) == "ai-engineering"
    assert program_for(["github:ollama/ollama"]) == "tool-review"
    assert program_for(["url:https://huggingface.co/acme/model"]) == "tool-review"
    assert program_for(["url:https://news.example.com/a"]) == "ai-today"
    assert program_for([]) == "ai-today"


def test_breakdown_is_written_as_people_read_it():
    import re

    text = describe_breakdown(score_heat(HOT, CONFIG))
    assert re.match(r"^\d+ 分（共振 .*HN 增速 .*）$", text)
    assert "X 互動" not in text and "在地缺口" not in text
