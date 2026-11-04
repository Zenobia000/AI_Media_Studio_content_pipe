"""Ported from modules/inbox/tests (signals, manual, titles, alerts, x-velocity, local-heat, heat inputs). They need a throwaway database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.collect.alerts import alert_text, notify_new_s
from newsroom.collect.heat_inputs import backtest_heat, load_heat_inputs
from newsroom.collect.inbox import Candidate
from newsroom.collect.local_heat import DeniedError, QuotaError, threads_search, with_local_heat
from newsroom.collect.manual import InvalidUrlError, add_source
from newsroom.collect.materials import upsert_manual_material
from newsroom.collect.score import load_heat_config, score_heat
from newsroom.collect.signals import Point, collect_signals, describe_signals, growth_per_hour, latest_signals, store
from newsroom.collect.titles import EmptyTitleError, set_title_zh, with_titles_zh
from newsroom.collect.x_velocity import engagement_of, velocity_of, x_velocities
from newsroom.core.http import Page
from newsroom.core.urls import identity_key_for_url
from newsroom.fetch.attempts import Attempt, fetch_stats, record_attempts

UTC = timezone.utc
pytestmark = pytest.mark.usefixtures("conn")


def ok(body) -> Page:
    return Page(200, "", "application/json", json.dumps(body))


HN_FRONT = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60"
HN_AI = "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&numericFilters=points%3E20&hitsPerPage=60"


def recorded(hn, extra=None):
    pages = {
        HN_FRONT: ok({"hits": hn}),
        HN_AI: ok({"hits": []}),
        "https://huggingface.co/api/daily_papers": ok([{"paper": {"id": "2410.12345", "upvotes": 87}}]),
        "https://huggingface.co/api/models?sort=trendingScore&limit=30": ok([{"id": "acme/model-1", "likes": 321, "downloads": 9000}]),
        "https://api.github.com/repos/acme/tool": ok({"stargazers_count": 2345}),
        **(extra or {}),
    }
    return lambda url, headers=None: pages.get(url, Page(404, url, "text/plain", ""))


def offline(url, headers=None):
    raise OSError("offline")


def value(conn, key, metric):
    r = conn.execute(
        "SELECT value FROM signal_points WHERE identity_key = %s AND metric = %s ORDER BY observed_at DESC LIMIT 1", (key, metric)
    ).fetchone()
    return r["value"] if r else None


# ── signals ─────────────────────────────────────────────────────────────────────────────


def test_signals_are_recorded_against_the_address_they_are_about(conn, tag):
    conn.execute("DELETE FROM sources WHERE id LIKE 'gh-test-%'")  # earlier runs share this database
    conn.execute(
        "INSERT INTO sources (id, name, kind, config, next_fetch_at) VALUES (%s, 'acme/tool', 'rss', %s, '2100-01-01')",
        (f"gh-test-{tag}", json.dumps({"feedUrl": "https://github.com/acme/tool/releases.atom"})),
    )
    story = f"https://news.example.com/story-{tag}"
    report = collect_signals(
        conn,
        recorded([{"objectID": "1", "url": story, "points": 412, "num_comments": 120}, {"objectID": "2", "points": 50}]),
        now=datetime(2026, 11, 2, 10, 20, tzinfo=UTC),
    )
    assert report == {"hn": 2, "hf": 3, "github": 1}
    key = identity_key_for_url(story)
    assert value(conn, key, "hn_points") == 412 and value(conn, key, "hn_comments") == 120
    assert value(conn, "arxiv:2410.12345", "hf_upvotes") == 87
    assert value(conn, identity_key_for_url("https://huggingface.co/acme/model-1"), "hf_likes") == 321
    assert value(conn, "github:acme/tool", "gh_stars") == 2345


def test_same_hour_updates_a_later_hour_adds_a_point_and_growth_follows(conn, tag):
    story = f"https://news.example.com/series-{tag}"
    key = identity_key_for_url(story)

    def at(h, m=0):
        return datetime(2026, 11, 2, h, m, tzinfo=UTC)

    def hit(points):
        return [{"objectID": "9", "url": story, "points": points, "num_comments": 1}]

    collect_signals(conn, recorded(hit(100)), now=at(10, 5))
    collect_signals(conn, recorded(hit(110)), now=at(10, 45))
    assert conn.execute("SELECT count(*)::int AS n FROM signal_points WHERE identity_key = %s AND metric = 'hn_points'", (key,)).fetchone()["n"] == 1
    assert value(conn, key, "hn_points") == 110 and growth_per_hour(conn, key, "hn_points") is None
    collect_signals(conn, recorded(hit(170)), now=at(12, 10))
    assert growth_per_hour(conn, key, "hn_points") == 30


def test_a_source_that_does_not_answer_is_reported_and_the_others_are_recorded(conn):
    report = collect_signals(conn, recorded([], {HN_FRONT: Page(500, "", "text/plain", "")}), now=datetime(2026, 11, 2, 14, tzinfo=UTC))
    assert report["hn"] == "HTTP 500" and report["hf"] == 3


def test_signals_are_written_as_people_read_them():
    assert describe_signals({"hn_points": 412, "hn_comments": 120, "gh_stars": 2345}) == "HN 412 分、120 則留言、GitHub ★2345"
    assert describe_signals({}) == ""


# ── manual ──────────────────────────────────────────────────────────────────────────────


def counts(conn, url):
    return conn.execute(
        """SELECT count(DISTINCT a.id)::int AS articles, count(m.article_id)::int AS candidates FROM articles a
                           LEFT JOIN manual_candidates m ON m.article_id = a.id WHERE a.url = %s""",
        (url,),
    ).fetchone()


def test_a_new_address_gives_one_article_and_one_candidate_and_again_adds_the_note(conn, tag):
    url = f"https://example.com/manual-{tag}"
    first = add_source(conn, url, note="從工具角度切入", formats=["threads"], get=offline)
    assert first.created and not first.already_added
    again = add_source(conn, f"{url}?utm_source=x#top", note="再補一句", get=offline)
    assert again.article_id == first.article_id and again.already_added and again.inbox_date == first.inbox_date
    assert counts(conn, url) == {"articles": 1, "candidates": 1}
    row = conn.execute("SELECT notes, formats FROM manual_candidates WHERE article_id = %s", (first.article_id,)).fetchone()
    assert row["notes"] == "從工具角度切入\n再補一句" and row["formats"] == ["threads"]


def test_an_article_a_source_already_brought_becomes_a_manual_candidate_without_a_second_article(conn, tag):
    conn.execute("INSERT INTO sources (id, name, kind, next_fetch_at) VALUES (%s, 'Test feed', 'rss', '2100-01-01')", (f"test-manual-src-{tag}",))
    url = f"https://example.com/from-feed-{tag}"
    known = upsert_manual_material(conn, f"test-manual-src-{tag}", url, "From the feed", via="fetch")
    added = add_source(conn, url, note="這則要做", get=offline)
    assert added.article_id == known.article_id and not added.created
    assert counts(conn, url) == {"articles": 1, "candidates": 1}


def test_a_link_without_a_title_takes_the_pages_own_title(conn, tag):
    def html(body):
        return lambda url, headers=None: Page(200, url, "text/html; charset=utf-8", body)

    def title(url):
        return conn.execute("SELECT title FROM articles WHERE url = %s", (url,)).fetchone()["title"]

    og = f"https://example.com/og-{tag}"
    add_source(conn, og, get=html('<html><head><title>fallback</title><meta property="og:title" content="  Model  X 發表 "></head></html>'))
    assert title(og) == "Model X 發表"
    plain = f"https://example.com/plain-{tag}"
    add_source(conn, plain, get=html("<html><head><title>Plain page</title></head></html>"))
    assert title(plain) == "Plain page"
    given = f"https://example.com/given-{tag}"
    add_source(conn, given, title="我取的名字", get=html("<title>Other</title>"))
    assert title(given) == "我取的名字"
    down = f"https://example.com/down-{tag}"
    add_source(conn, down, get=offline)
    assert title(down) == down


def test_an_address_that_is_not_http_is_refused(conn):
    for bad in ("not a url", "ftp://example.com/a", ""):
        with pytest.raises(InvalidUrlError):
            add_source(conn, bad, get=offline)


def test_short_links_are_followed_and_kept_when_they_cannot_be(conn, tag):
    followed = add_source(
        conn, "https://t.co/abc", title=f"短網址 {tag}", get=lambda url, headers=None: Page(200, f"https://openai.com/index/{tag}", "text/html", "")
    )
    assert followed.url == f"https://openai.com/index/{tag}"
    kept = add_source(conn, "https://bit.ly/z", title=f"短網址 {tag}", get=offline)
    assert kept.url == "https://bit.ly/z"


# ── titles ──────────────────────────────────────────────────────────────────────────────


def cand(cid, tier="S", **over):
    base = dict(
        id=cid,
        title=f"{cid} 發布新模型",
        tier=tier,
        participant_count=3,
        source_names=["甲", "乙", "丙"],
        first_seen="08:00",
        suggested_program="ai-today",
        suggested_formats=["threads"],
        heat_reason="3 個來源共振",
    )
    return Candidate(**{**base, **over})


def test_a_title_is_kept_once_unless_forced(conn, tag):
    cid = f"cand_t{tag}"
    assert set_title_zh(conn, cid, " 中文標題 ") is True
    assert set_title_zh(conn, cid, "另一個") is False
    assert with_titles_zh(conn, [cand(cid, title="English")])[0].title == "中文標題"
    assert set_title_zh(conn, cid, "另一個", force=True) is True
    with pytest.raises(EmptyTitleError):
        set_title_zh(conn, cid, "  ")


# ── S-tier alert ────────────────────────────────────────────────────────────────────────


def listed(conn, *cs):
    for c in cs:
        conn.execute("INSERT INTO candidate_state (candidate_id, snapshot) VALUES (%s, %s)", (c.id, json.dumps({"title": c.title})))


def test_a_candidate_that_reaches_s_is_told_once_with_title_sources_and_inbox(conn, tag):
    sent = []
    c = cand(f"cand_a{tag}")
    listed(conn, c)
    told = notify_new_s(conn, [c], "inbox/2026-11-04.md", lambda t, lines: sent.append((t, lines)) or "sent")
    assert told == [c.id] and len(sent) == 1
    assert c.title in sent[0][0] and "3 個來源" not in sent[0][0]
    body = "\n".join(sent[0][1])
    assert "甲、乙、丙" in body and "inbox/2026-11-04.md" in body
    assert notify_new_s(conn, [c], "f.md", lambda t, lines: "sent") == []


def test_only_new_s_counts_and_a_channel_that_is_off_still_marks_it(conn, tag):
    a, carried, picked = cand(f"cand_x{tag}", "A"), cand(f"cand_c{tag}", carried="昨日"), cand(f"cand_p{tag}")
    listed(conn, a, carried, picked)
    conn.execute("UPDATE candidate_state SET status = 'picked' WHERE candidate_id = %s", (picked.id,))
    assert notify_new_s(conn, [a, carried, picked], "f.md", lambda t, lines: "sent") == []
    d = cand(f"cand_d{tag}")
    listed(conn, d)
    n = []
    assert notify_new_s(conn, [d], "f.md", lambda t, lines: n.append(1) or "disabled") == [d.id]
    assert notify_new_s(conn, [d], "f.md", lambda t, lines: n.append(1) or "sent") == [] and len(n) == 1


def test_a_send_that_fails_is_tried_again_next_round(conn, tag):
    c = cand(f"cand_e{tag}")
    listed(conn, c)

    def boom(t, lines):
        raise RuntimeError("down")

    assert notify_new_s(conn, [c], "f.md", boom) == []
    assert notify_new_s(conn, [c], "f.md", lambda t, lines: "sent") == [c.id]


def test_alert_text_names_the_reason_only_when_there_is_one():
    assert "為什麼是 S：3 個來源共振" in "\n".join(alert_text(cand("x"), "f.md")[1])
    assert "為什麼是 S" not in "\n".join(alert_text(cand("x", heat_reason=None), "f.md")[1])


# ── X velocity ──────────────────────────────────────────────────────────────────────────


def test_engagement_and_velocity():
    assert engagement_of({"favorite": 100, "retweet": 10, "views": 5000}) == 120
    assert engagement_of({"favorite": 7, "retweet": None}) == 7
    assert engagement_of({"views": 5}) is None and engagement_of(None) is None
    assert velocity_of(300, [100] * 5) == 3 and velocity_of(300, [100] * 3) is None and velocity_of(None, [1] * 5) is None
    assert velocity_of(10, [0] * 5) == 10


def test_a_post_far_above_its_accounts_usual_feeds_the_heat_input(conn, tag):
    conn.execute("INSERT INTO sources (id, name, kind, tier, next_fetch_at) VALUES (%s, 'X', 'rss', 'T1', '2100-01-01')", (f"x-vel-{tag}",))
    handle = f"acct_{tag}"
    base = datetime(2026, 11, 1, tzinfo=UTC)

    def post(n, favorite, retweet):
        art = upsert_manual_material(conn, f"x-vel-{tag}", f"https://x.com/{handle}/status/{n}", f"post {n}", now=base + timedelta(hours=n))
        conn.execute(
            "UPDATE articles SET author = %s, identity_key = %s, raw = %s WHERE id = %s",
            (handle, f"x:{tag}{n}", json.dumps({"favorite": favorite, "retweet": retweet, "views": None}), art.article_id),
        )
        return art.article_id

    for n in range(1, 7):
        post(n, 100, 0)
    hot_id = post(7, 250, 25)
    v = x_velocities(conn, [f"x:{tag}7", f"x:{tag}1", "https://example.com/not-x"])
    assert v[f"x:{tag}7"] == 3 and "https://example.com/not-x" not in v

    story = conn.execute("INSERT INTO stories (public_id, title) VALUES (gen_random_uuid(), %s) RETURNING id", (f"X story {tag}",)).fetchone()["id"]
    conn.execute(
        "INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (%s, %s, %s, %s, 'editorial', now())",
        (story, hot_id, f"source:x-vel-{tag}", f"x-vel-{tag}"),
    )
    config = load_heat_config()
    inp = load_heat_inputs(conn, config, datetime.now(UTC), story_ids=[story]).by_story[story]
    assert inp.x_velocity == 3
    off = score_heat(inp, config)
    assert next(b for b in off.breakdown if b.signal == "x_velocity").points == 0
    config["signals"]["x_velocity"]["enabled"] = True
    assert next(b for b in score_heat(inp, config).breakdown if b.signal == "x_velocity").points > 0


# ── local heat ──────────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 4, 8, tzinfo=UTC)


def test_s_and_a_get_a_local_answer_covered_with_enough_posts_a_gap_with_few(conn, tag):
    seen = []

    def search(q):
        seen.append(q)
        return 12 if q.startswith(f"cand_hot{tag}") else 1

    out = with_local_heat(conn, [cand(f"cand_hot{tag}"), cand(f"cand_new{tag}", "A"), cand(f"cand_b{tag}", "B")], search=search, now=NOW)
    assert "中文圈已有 12 則" in out[0].local
    assert "還沒人報導" in out[1].local and "缺口" in out[1].local
    assert out[2].local is None and len(seen) == 2


def test_checked_within_a_day_is_not_searched_again_and_the_allowance_stops_the_rest(conn, tag):
    n = []
    cid = f"cand_once{tag}"
    with_local_heat(conn, [cand(cid)], search=lambda q: n.append(1) or 5, now=NOW)
    with_local_heat(conn, [cand(cid)], search=lambda q: n.append(1) or 5, now=NOW + timedelta(hours=1))
    assert len(n) == 1
    with_local_heat(conn, [cand(cid)], search=lambda q: n.append(1) or 5, now=NOW + timedelta(hours=25))
    assert len(n) == 2
    # With one search left in the day's allowance the second and third candidates are not checked.
    n.clear()
    out = with_local_heat(
        conn,
        [cand(f"cand_1{tag}"), cand(f"cand_2{tag}"), cand(f"cand_3{tag}")],
        search=lambda q: n.append(1) or 5,
        now=NOW,
        budget=len(n) + 1 + _checked_today(conn),
    )
    assert "已有" in out[0].local and "未檢查（今天的搜尋額度用完）" in out[1].local and len(n) == 1


def _checked_today(conn):
    return conn.execute(
        "SELECT count(*)::int AS n FROM local_heat WHERE status <> 'unchecked' AND checked_at > %s", (NOW - timedelta(hours=24),)
    ).fetchone()["n"]


def test_without_access_it_says_why_a_refusal_stops_the_round_and_foreign_titles_are_not_searched(conn, tag):
    assert "未檢查（還沒設定 Threads 搜尋權限" in with_local_heat(conn, [cand(f"cand_n{tag}")], search=None, now=NOW)[0].local
    calls = []

    def denied(q):
        calls.append(q)
        raise DeniedError("Threads 搜尋被拒絕（HTTP 403），權限可能還沒通過審核")

    out = with_local_heat(conn, [cand(f"cand_d1{tag}"), cand(f"cand_d2{tag}")], search=denied, now=NOW)
    assert len(calls) == 1 and "被拒絕" in out[1].local
    english = with_local_heat(conn, [cand(f"cand_en{tag}", title="Acme releases a model")], search=lambda q: 9, now=NOW)[0]
    assert "未檢查（還沒有中文標題" in english.local
    quota = with_local_heat(conn, [cand(f"cand_q{tag}")], search=lambda q: (_ for _ in ()).throw(QuotaError()), now=NOW)[0]
    assert "額度用完" in quota.local


def test_the_threads_search_maps_its_answers():
    import httpx

    def client(status, body=None):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body or {})))

    assert threads_search("t", client(200, {"data": [{"id": "1"}, {"id": "2"}]}))("x") == 2
    with pytest.raises(QuotaError):
        threads_search("t", client(429))("x")
    with pytest.raises(DeniedError):
        threads_search("t", client(403))("x")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        threads_search("t", client(500))("x")


# ── heat inputs and backtest ────────────────────────────────────────────────────────────


def test_signals_of_the_database_become_the_inputs_and_the_backtest_counts_s_hits(conn, tag):
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    config = load_heat_config()
    for n, tier in (("a", "T1"), ("b", "T2"), ("c", "T2")):
        conn.execute(
            "INSERT INTO sources (id, name, kind, tier, next_fetch_at) VALUES (%s, %s, 'rss', %s, '2100-01-01')", (f"heat-{n}-{tag}", n, tier)
        )

    def hours_ago(h):
        return now - timedelta(hours=h)

    def story(title, reports):
        sid = conn.execute(
            "INSERT INTO stories (public_id, title) VALUES (gen_random_uuid(), %s) RETURNING id, public_id::text AS pid", (f"{title} {tag}",)
        ).fetchone()
        ids = []
        for i, (src, h) in enumerate(reports):
            art = upsert_manual_material(conn, f"heat-{src}-{tag}", f"https://news.example.com/{title}-{i}-{tag}", f"{title} {i}")
            conn.execute(
                "INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (%s, %s, %s, %s, 'editorial', %s)",
                (sid["id"], art.article_id, f"source:{src}", f"heat-{src}-{tag}", hours_ago(h)),
            )
            ids.append(art.article_id)
        return sid, ids

    sid, ids = story("sig", [("a", 2), ("b", 5), ("c", 30)])
    key = conn.execute("SELECT identity_key FROM articles WHERE id = %s", (ids[0],)).fetchone()["identity_key"]
    store(conn, [Point(key, "hn_points", 100), Point(key, "newsletter_mentions", 2)], hours_ago(3))
    store(conn, [Point(key, "hn_points", 160)], hours_ago(1))
    inp = load_heat_inputs(conn, config, now, story_ids=[sid["id"]]).by_story[sid["id"]]
    assert inp.resonance_sources == 2 and inp.best_tier == "T1" and inp.newsletter_mentions == 2
    assert inp.hn_points_per_hour == 30
    by_story, _ = latest_signals(conn, story_ids=[sid["id"]])
    assert by_story[sid["id"]]["hn_points"] == 160

    big, _ = story("big", [("a", 1), ("b", 2), ("c", 3)])
    medium, _ = story("medium", [("a", 1), ("b", 2)])
    quiet, _ = story("quiet", [("a", 1)])
    pid = {
        k: conn.execute("SELECT public_id::text AS p FROM stories WHERE id = %s", (v["id"],)).fetchone()["p"]
        for k, v in (("big", big), ("medium", medium), ("quiet", quiet))
    }
    r = backtest_heat(
        conn,
        [
            {"story": pid["big"], "label": "S"},
            {"story": pid["medium"], "label": "S"},
            {"story": pid["quiet"], "label": "S"},
            {"story": pid["quiet"], "label": "B"},
            {"story": "00000000-0000-0000-0000-000000000000", "label": "S"},
            {"story": pid["big"], "label": "S", "asOf": hours_ago(20).isoformat()},
        ],
        config,
        now,
    )
    assert [row.tier for row in r.rows[:3]] == ["S", "A", "B"]
    assert r.rows[4].found is False
    assert (r.s_hits, r.s_total) == (2, 4) and r.s_rate == 0.5


# ── attempts ────────────────────────────────────────────────────────────────────────────


def test_what_the_layers_try_is_recorded_and_the_stats_count_real_downloads_only(conn):
    now = datetime(2026, 12, 1, tzinfo=UTC)
    conn.execute("DELETE FROM fetch_attempts WHERE attempted_at > '2026-11-30'")
    record_attempts(
        conn,
        [Attempt(2, "https://a.example/x", True), Attempt(2, "https://a.example/y", False, "HTTP 404"), Attempt(3, "https://v.example/z", True)],
        at=now - timedelta(days=1),
    )
    record_attempts(conn, [Attempt(2, "https://a.example/self", False, "dead")], kind="selftest", at=now - timedelta(days=1))
    record_attempts(conn, [Attempt(2, "https://a.example/old", False)], at=now - timedelta(days=40))
    stats = {s.layer: s for s in fetch_stats(conn, 30, now) if s.layer in (2, 3)}
    assert (stats[2].attempts, stats[2].ok, stats[2].rate) >= (2, 1, 0)
    assert stats[3].name == "L3 下載器"
