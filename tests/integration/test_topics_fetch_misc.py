"""open-topic, fetch (source text), newsletters, page-change monitoring, init, doctor and the fetch self-test."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from newsroom.collect.changedetect import poll_watches, summarize
from newsroom.collect.inbox_writer import write_inbox
from newsroom.collect.manual import add_source
from newsroom.collect.newsletters import split_newsletters, story_links
from newsroom.collect.topics import OpenTopicError, merge_candidates, open_topic, slug_of, source_id_of
from newsroom.core.doctor import Deps, exit_code_of, run_checks
from newsroom.core.http import Page
from newsroom.core.init import init_workspace
from newsroom.core.urls import identity_key_for_url
from newsroom.core.workspace import find_workspace
from newsroom.fetch.extract import ExtractFailure, extract_source, language_of, word_count
from newsroom.fetch.html_markdown import html_to_markdown
from newsroom.fetch.selftest import compare_versions, load_targets, run_selftest
from newsroom.fetch.topic import fetch_topic, official_links

pytestmark = pytest.mark.usefixtures("conn")


def offline(url, headers=None):
    raise OSError("offline")


@pytest.fixture()
def workspace(conn, tmp_path, monkeypatch):
    for table in ("candidate_state", "local_heat", "candidate_titles", "manual_candidates"):
        conn.execute(f"DELETE FROM {table}")
    monkeypatch.setenv("NEWSROOM_HOME", str(tmp_path))
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    return tmp_path


def candidate(conn, url, title=None, **kw):
    added = add_source(conn, url, title=title or f"題目 {url[-10:]}", get=offline, **kw)
    return added, f"cand_{added.article_id}"


ARTICLE = (
    "<html lang='en'><head><title>Acme ships Model X</title><meta property='og:site_name' content='Acme Blog'>"
    "<meta name='author' content='Jane Doe'><meta property='article:published_time' content='2026-11-02T08:00:00Z'></head><body><article>"
    "<h1>Acme ships Model X</h1>"
    + "".join(
        f"<p>Paragraph {i}: Acme released Model X today with a longer context window, lower prices and better tool use for developers everywhere.</p>"
        for i in range(8)
    )
    + "<p>Read the <a href='https://github.com/acme/model-x'>repository</a> and the <a href='https://arxiv.org/abs/2410.12345v2'>paper</a>.</p></article></body></html>"
)


def pages(mapping: dict[str, tuple[int, str, str]]):
    def get(url, headers=None):
        status, ctype, text = mapping.get(url, (404, "text/plain", ""))
        return Page(status, url, ctype, text)

    return get


# ── open-topic ──────────────────────────────────────────────────────────────────────────


def test_opening_the_same_candidate_twice_leaves_one_topic_and_a_format_added_later_adds_one_content(conn, workspace, tag):
    _, cid = candidate(conn, f"https://example.com/topic-{tag}", note="從工具角度切入")
    write_inbox(conn)
    first = open_topic(conn, [cid], program="tool-review", formats=["threads"])
    assert first.created and first.contents_created == ["threads-01"]
    topic_dir = workspace / "topics" / first.topic_id
    front = (topic_dir / "topic.md").read_text(encoding="utf-8")
    assert "program: tool-review" in front and cid in front and "<!-- newsroom:begin status -->" in front
    again = open_topic(conn, [cid], formats=["threads", "reel"])
    assert not again.created and again.topic_id == first.topic_id
    assert again.contents_existing == ["threads-01"] and again.contents_created == ["reel-01"]
    assert (topic_dir / "contents" / "reel-01" / "content.md").exists()
    assert "從工具角度切入" in (topic_dir / "notes.md").read_text(encoding="utf-8")
    assert conn.execute("SELECT status FROM candidate_state WHERE candidate_id = %s", (cid,)).fetchone()["status"] == "opened"


def test_two_merged_candidates_give_one_topic_with_both_sources_and_a_note_can_merge_them_too(conn, workspace, tag):
    a, ca = candidate(conn, f"https://example.com/merge-a-{tag}")
    b, cb = candidate(conn, f"https://example.com/merge-b-{tag}")
    write_inbox(conn)
    assert merge_candidates(conn, ca, cb) is None
    t = open_topic(conn, [ca], formats=["threads"])
    assert sorted(t.candidate_ids) == sorted([ca, cb]) and len(t.source_ids) == 2

    c, cc = candidate(conn, f"https://example.com/merge-c-{tag}")
    d, cd = candidate(conn, f"https://example.com/merge-d-{tag}", note=f"合併到 {cc}")
    write_inbox(conn)
    t2 = open_topic(conn, [cd], formats=["threads"])
    assert sorted(t2.candidate_ids) == sorted([cc, cd])


def test_an_unknown_candidate_a_bad_program_or_format_and_no_candidate_at_all_are_refused(conn, workspace, tag):
    _, cid = candidate(conn, f"https://example.com/refuse-{tag}")
    write_inbox(conn)
    for kwargs, args in (({}, []), ({}, ["cand_nope"]), ({"program": "nope"}, [cid]), ({"formats": ["podcast"]}, [cid])):
        with pytest.raises(OpenTopicError):
            open_topic(conn, args, **kwargs)


def test_slugs_are_ascii_lower_case_and_short_and_the_same_address_has_the_same_source_id():
    assert slug_of("Hello, World! 你好") == "hello-world"
    assert slug_of("你好") == "topic" and len(slug_of("a" * 100)) <= 40
    assert source_id_of("https://Example.com/a/?utm_source=x") == source_id_of("https://example.com/a")
    assert source_id_of("https://example.com/a").startswith("src_") and len(source_id_of("https://example.com/a")) == 16


# ── fetch (source text) ─────────────────────────────────────────────────────────────────


def test_html_becomes_markdown_with_headings_links_lists_and_images():
    md = html_to_markdown(
        "<h2>Title</h2><p>Hello <a href='/x'>link</a> <strong>bold</strong></p><ul><li>one</li><li>two</li></ul><img src='/i.png' alt='pic'>",
        "https://e.com/p/",
    )
    assert "## Title" in md and "[link](https://e.com/x)" in md and "**bold**" in md and "- one" in md and "![pic](https://e.com/i.png)" in md
    assert html_to_markdown("", "https://e.com") == ""


def test_a_news_page_is_read_as_markdown_with_its_details_and_a_short_page_is_not_a_source():
    url = "https://news.example.com/acme-model-x"
    x = extract_source(url, pages({url: (200, "text/html; charset=utf-8", ARTICLE)}))
    assert (x.kind, x.method, x.title, x.publisher, x.author, x.language) == (
        "article",
        "readability",
        "Acme ships Model X",
        "Acme Blog",
        "Jane Doe",
        "en",
    )
    assert x.published_at == "2026-11-02T08:00:00Z" and "Paragraph 3" in x.markdown
    assert "https://github.com/acme/model-x" in x.links
    short = "https://news.example.com/short"
    with pytest.raises(ExtractFailure):
        extract_source(short, pages({short: (200, "text/html", "<html><body><p>tiny</p></body></html>")}))
    with pytest.raises(ExtractFailure, match="HTTP 404"):
        extract_source("https://news.example.com/none", pages({}))
    with pytest.raises(ExtractFailure, match="推文"):
        extract_source("https://x.com/a/status/1", pages({}))


def test_github_and_arxiv_are_read_through_their_own_interfaces():
    readme = "# Tool\n\n" + "A command line tool that does many useful things for developers. " * 6
    get = pages(
        {
            "https://api.github.com/repos/acme/tool/readme": (200, "text/plain", readme),
            "https://api.github.com/repos/acme/tool/releases/latest": (
                200,
                "application/json",
                json.dumps({"tag_name": "v1.2", "name": "One two", "body": "Fixes and features", "published_at": "2026-11-01T00:00:00Z"}),
            ),
            "https://export.arxiv.org/api/query?id_list=2410.12345": (
                200,
                "application/atom+xml",
                "<feed><entry><title>Attention Again</title><summary> We study   attention. </summary><author><name>A. Author</name></author><published>2024-10-01T00:00:00Z</published></entry></feed>",
            ),
        }
    )
    gh = extract_source("https://github.com/acme/tool", get)
    assert (
        (gh.kind, gh.method, gh.author, gh.published_at) == ("release", "github-api", "acme", "2026-11-01T00:00:00Z")
        and "## README" in gh.markdown
        and "Release v1.2：One two" in gh.markdown
    )
    ax = extract_source("https://arxiv.org/abs/2410.12345v2", get)
    assert (ax.kind, ax.title, ax.author, ax.method) == (
        "paper",
        "Attention Again",
        "A. Author",
        "arxiv-api",
    ) and "We study attention." in ax.markdown
    assert word_count("你好世界 hello world") == 6 and language_of("這是中文的一段話") == "zh" and language_of("plain english") == "en"


def test_official_links_are_repositories_papers_and_first_hand_sites_never_the_reports_own_site():
    sites = {"openai.com", "nvidia.com"}
    links = [
        "https://github.com/acme/tool",
        "https://arxiv.org/abs/2410.12345",
        "https://blogs.nvidia.com/blog/x",
        "https://openai.com/",
        "https://other.com/page",
        "https://news.example.com/self",
    ]
    assert official_links(links, "https://news.example.com/report", sites) == links[:3]
    assert official_links(links, "https://openai.com/index/x", sites) == []


def test_a_news_page_becomes_a_sources_file_and_the_official_source_it_links_to_joins_the_topic(conn, workspace, tag):
    url = f"https://news.example.com/{tag}/acme"
    _, cid = candidate(conn, url)
    write_inbox(conn)
    t = open_topic(conn, [cid], formats=["threads"])
    get = pages(
        {
            url: (200, "text/html", ARTICLE),
            "https://api.github.com/repos/acme/model-x/readme": (
                200,
                "text/plain",
                "# Model X\n\n" + "The official repository of the Model X release with many details. " * 6,
            ),
            "https://api.github.com/repos/acme/model-x/releases/latest": (404, "", ""),
            "https://export.arxiv.org/api/query?id_list=2410.12345": (
                200,
                "text/xml",
                "<entry><title>Paper</title><summary>" + "words " * 60 + "</summary></entry>",
            ),
        }
    )
    r = fetch_topic(conn, t.topic_id, get=get)
    assert not r.blocked and len(r.fetched) == 3 and sum(f["official"] for f in r.fetched) == 2
    sid = source_id_of(url)
    text = (workspace / "topics" / t.topic_id / "sources" / f"{sid}.md").read_text(encoding="utf-8")
    assert text.startswith("---\nschema_version: 1\n") and "fetch_method: readability" in text and "language: en" in text and "Paragraph 3" in text
    assert conn.execute("SELECT count(*)::int AS n FROM topic_sources WHERE topic_id = %s", (t.topic_id,)).fetchone()["n"] == 3
    again = fetch_topic(conn, t.topic_id, get=offline)
    assert again.fetched == [] and len(again.kept) == 3
    assert len(fetch_topic(conn, t.topic_id, get=get, force=True).fetched) >= 1


def test_when_nothing_can_be_read_the_topic_is_blocked_and_the_notes_say_how_to_paste_the_text(conn, workspace, tag):
    _, cid = candidate(conn, f"https://nothing.example.com/{tag}")
    write_inbox(conn)
    t = open_topic(conn, [cid], formats=["threads"])
    r = fetch_topic(conn, t.topic_id, get=pages({}))
    assert r.blocked and r.failed
    notes = (workspace / "topics" / t.topic_id / "notes.md").read_text(encoding="utf-8")
    assert "原文擷取失敗" in notes and "sources/manual-1.md" in notes
    fetch_topic(conn, t.topic_id, get=pages({}))
    assert (workspace / "topics" / t.topic_id / "notes.md").read_text(encoding="utf-8").count("原文擷取失敗") == 1


# ── newsletters ─────────────────────────────────────────────────────────────────────────

ISSUE = """# The Daily AI

[Acme ships a new open model with long context](https://acme.example/blog/model-x)
[Sponsor: buy our thing today](https://ads.example/promo)
[Follow us on X for more](https://x.com/thedaily)
[Subscribe to the newsletter](https://daily.example/subscribe)
[image](https://i.redd.it/abc.png)
[Another paper on test-time compute scaling](https://arxiv.org/abs/2410.99999)
[Their own archive page of past issues](https://daily.example/archive/2026)
[front page of a site](https://front.example/)
![pic](https://example.com/p.png)
"""


def test_which_links_of_an_issues_markdown_are_stories():
    links = story_links(ISSUE, "https://daily.example/issue/1")
    assert [link.url for link in links] == ["https://acme.example/blog/model-x", "https://arxiv.org/abs/2410.99999"]


def test_an_issue_is_split_into_one_article_per_story_link_and_mentions_become_a_heat_signal(conn, workspace, tag):
    conn.execute("DELETE FROM newsletter_mentions")
    conn.execute("DELETE FROM newsletter_issues")
    sid = f"nl-{tag}"
    conn.execute(
        "INSERT INTO sources (id, name, kind, tier, tags, next_fetch_at) VALUES (%s, 'Daily', 'rss', 'T2', %s, '2100-01-01')", (sid, ["電子報"])
    )
    issue_url = f"https://daily.example/{tag}/issue"
    from newsroom.collect.materials import upsert_manual_material

    issue = upsert_manual_material(conn, sid, issue_url, "The Daily AI issue", via="fetch")
    body = (
        "<html><body><article>"
        + "".join(f"<p>{ln}</p>" for ln in ("Today in AI " * 20,))
        + "".join(
            f"<p><a href='{u}'>{t}</a></p>"
            for u, t in [
                (f"https://acme.example/{tag}/model-x", "Acme ships a new open model with long context"),
                ("https://ads.example/promo", "Sponsor: buy our thing today"),
            ]
        )
        + "</article></body></html>"
    )
    r = split_newsletters(conn, get=pages({issue_url: (200, "text/html", body)}))
    assert r.issues == 1 and r.links == 1 and r.created == 1
    key = identity_key_for_url(f"https://acme.example/{tag}/model-x")
    assert (
        conn.execute(
            "SELECT value FROM signal_points WHERE identity_key = %s AND metric = 'newsletter_mentions' ORDER BY observed_at DESC LIMIT 1", (key,)
        ).fetchone()["value"]
        == 1
    )
    assert split_newsletters(conn, get=offline).issues == 0
    assert issue.article_id


# ── page-change monitoring ──────────────────────────────────────────────────────────────


def test_the_summary_keeps_the_added_and_removed_lines_short():
    diff = "(changed) Price 10\n(into) Price 12\nunchanged line\n+ new line\n- old line\n+++ header\n"
    assert summarize(diff) == "(changed) Price 10\n(into) Price 12\n+ new line\n- old line"
    assert summarize("\n".join(f"+ line {i} " + "x" * 50 for i in range(30))).endswith("…")


def test_a_change_is_brought_in_once_and_a_service_that_is_down_breaks_nothing(conn, workspace, tag):
    now = datetime(2026, 11, 4, 12, tzinfo=UTC)
    stamp = int((now - timedelta(hours=1)).timestamp())
    page = f"https://pricing.example.com/{tag}"

    def get(path):
        if path == "api/v1/watch":
            return 200, json.dumps(
                {
                    "u1": {"url": page, "title": "Pricing", "last_changed": stamp},
                    "u2": {"url": "https://old.example/x", "last_changed": stamp - 3 * 86400},
                }
            )
        if path.endswith("/history"):
            return 200, json.dumps({str(stamp - 1000): "a.txt", str(stamp): "b.txt"})
        return 200, "(changed) Plan A $10\n(into) Plan A $12\n"

    first = poll_watches(conn, get, now)
    assert (first["added"], first["skipped"]) == (1, 1)
    link = conn.execute(
        "SELECT a.title, m.notes FROM manual_candidates m JOIN articles a ON a.id = m.article_id WHERE a.url LIKE %s", (f"{page}%",)
    ).fetchone()
    assert link["title"] == "Pricing 有更新" and "Plan A $12" in link["notes"] and "[變動監測]" in link["notes"]
    assert poll_watches(conn, get, now)["added"] == 0
    assert "error" in poll_watches(conn, lambda p: (_ for _ in ()).throw(OSError("down")), now)
    assert "回應 500" in poll_watches(conn, lambda p: (500, ""), now)["error"]


# ── init, doctor, self-test ─────────────────────────────────────────────────────────────


def test_an_empty_folder_becomes_a_workspace_and_running_it_again_changes_nothing(tmp_path):
    skills = tmp_path / "skills"
    (skills / "open-topic").mkdir(parents=True)
    (skills / "open-topic" / "SKILL.md").write_text("# s", encoding="utf-8")
    (skills / "_conventions").mkdir()
    root = tmp_path / "ws"
    r = init_workspace(root, skills)
    assert (
        "config.toml" in r.created
        and "inbox/" in r.created
        and ".claude/skills/open-topic" in r.created
        and not any("_conventions" in c for c in r.created)
    )
    assert (root / ".claude" / "skills" / "open-topic" / "SKILL.md").exists() and "newsroom" in (root / "CLAUDE.md").read_text(encoding="utf-8")
    (root / "voice.md").write_text("我寫的口吻", encoding="utf-8")
    mtime = (root / "config.toml").stat().st_mtime_ns
    again = init_workspace(root, skills)
    assert (
        again.created == [] and (root / "voice.md").read_text(encoding="utf-8") == "我寫的口吻" and (root / "config.toml").stat().st_mtime_ns == mtime
    )
    (root / "glossary.md").unlink()
    assert init_workspace(root, skills).created == ["glossary.md"]


def test_the_workspace_is_found_from_newsroom_home_or_going_up(tmp_path):
    root = tmp_path / "newsroom"
    init_workspace(root, tmp_path / "none")
    assert find_workspace(tmp_path, {"NEWSROOM_HOME": str(root)}) == root.resolve()
    assert find_workspace(root / "topics", {}) == root
    assert find_workspace(tmp_path, {}) == root


def make_deps(**over):
    base = dict(
        exec=lambda c, a: (True, f"{c} 1.0"),
        env={"DATABASE_URL": "postgres://x", "LLM_API_KEY": "k"},
        reach=lambda u: True,
        database=lambda: None,
        python_version=(3, 12, 1),
        workspace="/ws",
    )
    return Deps(**{**base, **over})


def test_doctor_a_machine_with_everything_has_no_failure_and_missing_things_say_how_to_fix_them():
    assert exit_code_of(run_checks(make_deps())) == 0
    checks = run_checks(make_deps(exec=lambda c, a: (False, "") if c in ("ffmpeg", "ffprobe") else (True, "x")))
    assert exit_code_of(checks) == 1 and "winget" in next(c for c in checks if c.name == "ffmpeg").fix
    checks = run_checks(make_deps(python_version=(3, 9, 0), env={}, database=lambda: "connection refused"))
    failed = {c.name for c in checks if c.status == "fail"}
    assert {"Python", "資料庫"} <= failed
    soft = {c.name: c.status for c in run_checks(make_deps(env={"DATABASE_URL": "x"}, workspace=None))}
    assert soft["THREADS_ACCESS_TOKEN"] == "warn" and soft["工作區"] == "warn" and soft["模型金鑰"] == "warn"


def test_selftest_each_layer_on_its_own_and_versions_compare_as_dates(conn):
    assert (
        compare_versions("2026.10.26", "2026.10.26.123456") < 0
        and compare_versions("v2026.11.01", "2026.10.30") > 0
        and compare_versions("1.2", "1.2.0") == 0
    )
    t = load_targets()
    assert t["extract"] and t["l2"] and t["l3"]
    targets = {"extract": ["https://dead.example/x"], "l2": ["https://nomedia.example/p"], "l3": []}
    checks = run_selftest(
        conn,
        targets,
        get=pages({"https://nomedia.example/p": (200, "text/html", "<html><body>no media</body></html>")}),
        exec_=lambda c, a: __import__("newsroom.fetch.layers.l3_dl", fromlist=["ExecResult"]).ExecResult("ENOENT", "", ""),
        latest_ytdlp=lambda: None,
    )
    assert [(c.layer, c.status) for c in checks] == [(0, "fail"), (2, "fail")]
