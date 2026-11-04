"""Ported from inbox-days.test.ts, manual.test.ts and heat.test.ts: the inbox file written from the database across days."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.collect.inbox_writer import write_inbox
from newsroom.collect.manual import add_source
from newsroom.collect.titles import set_title_zh

UTC = timezone.utc
pytestmark = pytest.mark.usefixtures("conn")


def offline(url, headers=None):
    raise OSError("offline")


def at(day: int, hour: int = 2) -> datetime:
    """Taipei is UTC+8: these instants are 10:00 on the day."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


@pytest.fixture()
def inbox(conn, tmp_path, monkeypatch):
    for table in ("candidate_state", "local_heat", "candidate_titles", "manual_candidates"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM hot_rankings")
    monkeypatch.setenv("INBOX_DIR", str(tmp_path))
    monkeypatch.setenv("NEWSROOM_HOME", str(tmp_path))
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    return tmp_path


def tick(path, cid):
    path.write_text(re.sub(rf"- \[ \] (\*\*[^\n]*{cid})", r"- [x] \1", path.read_text(encoding="utf-8")), encoding="utf-8")


def test_over_three_days_a_ticked_link_and_its_note_stay_an_unticked_one_is_carried_once_then_dropped(conn, inbox, tag):
    kept = add_source(conn, f"https://example.com/kept-{tag}", note="要做", get=offline)
    dropped = add_source(conn, f"https://example.com/dropped-{tag}", note="不做", get=offline)
    conn.execute("UPDATE manual_candidates SET added_at = %s", (at(17),))

    write_inbox(conn, at(17))
    day1 = (inbox / "2026-10-17.md").read_text(encoding="utf-8")
    assert f"`cand_{kept.article_id}`" in day1
    tick(inbox / "2026-10-17.md", f"cand_{kept.article_id}")

    write_inbox(conn, at(18))
    day2 = (inbox / "2026-10-18.md").read_text(encoding="utf-8")
    assert re.search(rf"- \[x\] .*`cand_{kept.article_id}`\n.*已勾選(?:\n  - [^\n]*)*\n  - 備註：要做", day2)
    assert re.search(rf"- \[ \] .*`cand_{dropped.article_id}`\n.*昨日", day2)

    write_inbox(conn, at(19, 4))  # more than 48 hours after the first listing
    day3 = (inbox / "2026-10-19.md").read_text(encoding="utf-8")
    assert f"cand_{kept.article_id}" in day3 and f"cand_{dropped.article_id}" not in day3
    assert (
        conn.execute("SELECT status FROM candidate_state WHERE candidate_id = %s", (f"cand_{dropped.article_id}",)).fetchone()["status"] == "skipped"
    )


def test_taking_a_tick_away_puts_the_candidate_back_and_a_chinese_title_shows_on_a_carried_one(conn, inbox, tag):
    item = add_source(conn, f"https://example.com/untick-{tag}", get=offline)
    cid = f"cand_{item.article_id}"
    conn.execute("UPDATE manual_candidates SET added_at = %s WHERE article_id = %s", (at(10), item.article_id))
    write_inbox(conn, at(10))
    tick(inbox / "2026-10-10.md", cid)
    write_inbox(conn, at(10, 3))
    status = lambda: conn.execute("SELECT status FROM candidate_state WHERE candidate_id = %s", (cid,)).fetchone()["status"]  # noqa: E731
    assert status() == "picked"

    p = inbox / "2026-10-10.md"
    p.write_text(re.sub(rf"- \[x\] (\*\*[^\n]*{cid})", r"- [ ] \1", p.read_text(encoding="utf-8")), encoding="utf-8")
    set_title_zh(conn, cid, "中文標題測試")
    write_inbox(conn, at(11))
    assert status() == "new"
    assert "中文標題測試" in (inbox / "2026-10-11.md").read_text(encoding="utf-8")
    assert set_title_zh(conn, cid, "換一個") is False


def test_the_day_before_a_launch_event_there_is_a_preparation_candidate_and_after_it_ends_a_story_naming_its_keyword_is_s(conn, inbox, tag):
    (inbox / "calendar").mkdir()
    (inbox / "calendar" / "events.md").write_text(
        "| 日期 | 時間 | 活動 | 關鍵字 | 官方連結 |\n|---|---|---|---|---|\n| 2026-10-21 | 10:00 | Zeta DevDay | zetaword | https://zeta.example/ |\n",
        encoding="utf-8",
    )
    news = add_source(conn, f"https://example.com/zeta-{tag}", title="ZetaWord unveils something", get=offline)
    conn.execute("UPDATE manual_candidates SET added_at = %s WHERE article_id = %s", (at(21, 6), news.article_id))

    write_inbox(conn, at(20))
    assert "準備：Zeta DevDay" in (inbox / "2026-10-20.md").read_text(encoding="utf-8")

    write_inbox(conn, at(21, 6))
    text = (inbox / "2026-10-21.md").read_text(encoding="utf-8")
    assert re.search(
        rf"ZetaWord unveils something\*\* `cand_{news.article_id}`\n[^\n]*\n  - 熱度：[^\n]*預告過的發表會剛結束\n  - 建議：ai-today｜threads, carousel, reel",
        text,
    )


def test_the_inbox_lists_the_hottest_first_with_score_reason_and_suggested_program_and_formats(conn, inbox, tag):
    add_source(conn, f"https://github.com/acme/cool-{tag}", title=f"Cool repo {tag}", get=offline)
    hot = add_source(conn, f"https://arxiv.org/abs/2410.{abs(hash(tag)) % 90000 + 10000}", title=f"Hot paper {tag}", get=offline)
    for hours, value in ((3, 20), (1, 140)):
        conn.execute(
            """INSERT INTO signal_points (identity_key, metric, value, observed_at)
                        SELECT identity_key, 'hn_points', %s, now() - make_interval(hours => %s) FROM articles WHERE id = %s""",
            (value, hours, hot.article_id),
        )
    assert write_inbox(conn)
    text = sorted(inbox.glob("2*.md"))[-1].read_text(encoding="utf-8")
    assert text.index(f"Hot paper {tag}") < text.index(f"Cool repo {tag}")
    assert re.search(rf"Hot paper {tag}[^\n]*\n[^\n]*\n  - 熱度：\d+ 分（[^\n]*HN 增速[^\n]*）｜", text)
    assert "建議：ai-engineering｜" in text and "建議：tool-review｜" in text


def test_a_hand_added_link_reaches_the_inbox_and_keeps_the_persons_tick_on_the_next_write(conn, inbox, tag):
    added = add_source(conn, f"https://example.com/inbox-{tag}", note="今天要做", program="tool-review", get=offline)
    written = write_inbox(conn)
    assert written
    text = open(written["file"], encoding="utf-8").read()
    assert "## 手動新增" in text and f"`cand_{added.article_id}`" in text and "備註：今天要做" in text
    tick(inbox / written["file"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1], f"`cand_{added.article_id}`".strip("`"))
    write_inbox(conn)
    assert re.search(rf"- \[x\] .*`cand_{added.article_id}`", open(written["file"], encoding="utf-8").read())


def test_a_candidate_shows_what_people_are_saying_about_its_address(conn, inbox, tag):
    from newsroom.collect.signals import collect_signals
    from newsroom.core.http import Page

    url = f"https://news.example.com/inbox-{tag}"
    pages = {
        "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60": Page(
            200, "", "application/json", json.dumps({"hits": [{"objectID": "7", "url": url, "points": 412, "num_comments": 120}]})
        ),
        "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&numericFilters=points%3E20&hitsPerPage=60": Page(
            200, "", "application/json", json.dumps({"hits": []})
        ),
    }
    collect_signals(conn, lambda u, headers=None: pages.get(u, Page(404, u, "text/plain", "")), now=datetime.now(UTC) - timedelta(minutes=1))
    add_source(conn, url, title=f"有討論的題目 {tag}", get=offline)
    assert write_inbox(conn)
    text = sorted(inbox.glob("2*.md"))[-1].read_text(encoding="utf-8")
    assert "共振 1 源｜手動、HN 412 分、120 則留言｜首次出現" in text
