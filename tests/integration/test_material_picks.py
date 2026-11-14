"""素材區: articles put in the area, the topic made for each, and what a download asks the media layers for. The layers are replaced by stand-ins that note what
they were asked; their own behaviour is tested in test_fetch_layers.py."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from newsroom.collect import picks
from newsroom.collect.manual import add_source
from newsroom.fetch import assets as assets_module
from newsroom.fetch.layers import l1_api, l2_html, l3_dl

pytestmark = pytest.mark.usefixtures("conn")


def offline(url, headers=None):
    raise OSError("offline")


class _Pool:
    """The one connection of the test as a pool."""

    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture()
def workspace(conn, tmp_path, monkeypatch):
    conn.execute("DELETE FROM material_picks")
    monkeypatch.setenv("NEWSROOM_HOME", str(tmp_path))
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    return tmp_path


def article(conn, tag, name="a"):
    return add_source(conn, f"https://picks.example/{tag}/{name}", title=f"素材 {name}", get=offline).article_id


def test_wanted_kinds_come_in_a_fixed_order_and_none_means_all():
    assert picks.wanted_of(None) == ["article", "photo", "video"]
    assert picks.wanted_of(["video", "article"]) == ["article", "video"]
    assert picks.wanted_of(["nonsense"]) == ["article", "photo", "video"]


def test_only_articles_that_exist_go_in_and_the_same_one_twice_is_one_row(conn, workspace, tag):
    a, b = article(conn, tag, "a"), article(conn, tag, "b")
    assert picks.add_picks(conn, [a, b, "no-such-article", "bad id!", a]) == 2
    assert picks.add_picks(conn, [a]) == 0
    assert {p["id"] for p in picks.list_picks(conn)} == {a, b}
    assert picks.remove_pick(conn, a) is True and picks.remove_pick(conn, a) is False
    assert [p["id"] for p in picks.list_picks(conn)] == [b]


def test_the_area_is_capped(conn, workspace, tag, monkeypatch):
    a, b = article(conn, tag, "a"), article(conn, tag, "b")
    monkeypatch.setattr(picks, "MAX_PICKS", 1)
    assert picks.add_picks(conn, [a, b]) == 1


@dataclass
class _Layers:
    calls: list[tuple] = field(default_factory=list)


@pytest.fixture()
def layers(monkeypatch):
    seen = _Layers()

    @dataclass
    class Result:
        downloaded: list = field(default_factory=list)
        skipped: list = field(default_factory=list)
        failed: list = field(default_factory=list)
        fetched: list = field(default_factory=list)
        kept: list = field(default_factory=list)

    def fetch_topic(conn, topic_id, **kw):
        seen.calls.append(("article",))
        return Result(fetched=[{"sourceId": "s"}])

    def layer1(conn, topic_id, **kw):
        seen.calls.append(("l1",))
        return Result()

    def layer2(conn, topic_id, kinds=None, **kw):
        seen.calls.append(("l2", kinds))
        return Result(downloaded=[{"kind": k, "sourceId": "s"} for k in sorted(kinds or ())])

    def layer3(conn, topic_id, kinds=None, have_video=None, **kw):
        seen.calls.append(("l3", kinds))
        return Result()

    @dataclass
    class Stock:
        gaps: list = field(default_factory=list)

    monkeypatch.setattr("newsroom.fetch.topic.fetch_topic", fetch_topic)
    monkeypatch.setattr(l1_api, "fetch_layer1", layer1)
    monkeypatch.setattr(l2_html, "fetch_layer2", layer2)
    monkeypatch.setattr(l3_dl, "fetch_layer3", layer3)
    monkeypatch.setattr(assets_module, "inventory", lambda conn, topic_id: Stock())
    return seen


def run(conn, article_id, kinds):
    picks._run(_Pool(conn), article_id, picks.wanted_of(kinds))
    return next(p for p in picks.list_picks(conn) if p["id"] == article_id)


def test_asking_for_the_article_alone_reads_the_text_and_touches_no_media_layer(conn, workspace, layers, tag):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    done = run(conn, a, ["article"])
    assert layers.calls == [("article",)]
    assert done["state"] == "done" and done["wanted"] == ["article"] and done["result"]["article"]["fetched"] == 1


def test_asking_for_pictures_alone_takes_only_images_from_the_page_and_the_downloader(conn, workspace, layers, tag):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    done = run(conn, a, ["photo"])
    assert layers.calls == [("l2", frozenset({"image"})), ("l3", frozenset({"image"}))]  # layer 1 and the text are for videos and articles
    assert done["result"]["photo"] == {"downloaded": 1} and "video" not in done["result"]


def test_asking_for_videos_alone_takes_videos_and_gifs_and_no_pictures(conn, workspace, layers, tag):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    done = run(conn, a, ["video"])
    assert layers.calls == [("l1",), ("l2", frozenset({"video", "gif"})), ("l3", frozenset({"video"}))]
    assert done["result"]["video"] == {"downloaded": 2}


def test_all_three_make_one_topic_that_the_cli_tools_can_use_and_it_is_reused(conn, workspace, layers, tag):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    first = run(conn, a, None)
    assert first["topicId"] and first["folder"] == f"topics/{first['topicId']}"
    assert conn.execute("SELECT count(*)::int AS n FROM topic_sources WHERE topic_id = %s", (first["topicId"],)).fetchone()["n"] == 1
    assert (workspace / "topics" / first["topicId"] / "topic.md").exists()
    second = run(conn, a, ["photo"])
    assert second["topicId"] == first["topicId"]
    assert conn.execute("SELECT count(*)::int AS n FROM topics WHERE topic_id = %s", (first["topicId"],)).fetchone()["n"] == 1


def test_a_step_that_fails_is_written_down_and_the_others_still_run(conn, workspace, layers, monkeypatch, tag):
    def broken(conn, topic_id, **kw):
        raise RuntimeError("the page would not load")

    monkeypatch.setattr("newsroom.fetch.topic.fetch_topic", broken)
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    done = run(conn, a, ["article", "photo"])
    assert done["state"] == "done" and done["result"]["photo"] == {"downloaded": 1}
    assert any("the page would not load" in e for e in done["result"]["errors"])


def test_a_pick_being_downloaded_cannot_be_removed_or_started_again(conn, workspace, tag):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    conn.execute("UPDATE material_picks SET state = 'running', started_at = now() WHERE article_id = %s", (a,))
    assert picks.remove_pick(conn, a) is False
    assert picks.start(_Pool(conn), a, None) == "busy"
    assert picks.start(_Pool(conn), "no-such-article", None) == "missing"


def test_a_download_that_never_finished_is_listed_as_interrupted(conn, workspace, tag, monkeypatch):
    a = article(conn, tag)
    picks.add_picks(conn, [a])
    conn.execute("UPDATE material_picks SET state = 'running', started_at = now() - interval '2 hours' WHERE article_id = %s", (a,))
    assert next(p for p in picks.list_picks(conn) if p["id"] == a)["state"] == "interrupted"
    # One this process still has a thread for (waiting behind others, or a long video) was not cut off, and is not started a second time.
    monkeypatch.setattr(picks, "_live", {a})
    assert next(p for p in picks.list_picks(conn) if p["id"] == a)["state"] == "running"
    assert picks.start(_Pool(conn), a, None) == "busy"
