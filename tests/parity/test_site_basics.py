"""/api/site meta, contact, stats, changelog and item availability."""

from __future__ import annotations


def test_meta_changelog_contact(compare):
    for path in ("/api/site/meta", "/api/site/changelog", "/api/site/contact"):
        got = compare(path)
        assert got["status"] == 200 and "etag" in got["headers"]


def test_stats(compare):
    got = compare("/api/site/stats")
    assert got["status"] == 200 and got["body"]["items"] >= 10 and got["body"]["sampleSources"]


def test_availability(compare):
    items = compare("/api/v1/items?mode=all&limit=5")["body"]["items"]
    ids = ",".join(i["id"] for i in items)
    got = compare(f"/api/site/items/availability?ids={ids},nope,bad%20id")
    assert got["status"] == 200 and got["headers"]["cache-control"] == "no-store"
    assert [got["body"][i["id"]]["status"] for i in items] == ["public"] * 5
    compare("/api/site/items/availability")


def test_conditional(compare):
    first = compare("/api/site/meta")
    assert compare("/api/site/meta", headers={"if-none-match": first["headers"]["etag"]})["status"] == 304


def test_the_sitemap_is_saved_and_bridges_a_failed_rebuild(database_url, settings_env, tmp_path, monkeypatch):
    """The last good sitemap is written to the data folder; when a rebuild fails in a new process, a copy younger than its lifetime is served, an older one is not."""
    import os
    import time

    import pytest
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from newsroom.engine.publication import sitemap as S

    monkeypatch.setenv("AIHOT_DATA_DIR", str(tmp_path))
    with ConnectionPool(database_url, min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, open=True) as pool:
        S._last.clear()
        first = S.load_sitemap(pool)
        saved = tmp_path / "sitemap-last.json"
        assert saved.exists() and set(first["files"]) == set(__import__("json").loads(saved.read_text(encoding="utf-8")))
        S._last.clear()  # a new process
        monkeypatch.setattr(S, "build", lambda *a: (_ for _ in ()).throw(RuntimeError("database down")))
        bridged = S.load_sitemap(pool)
        assert bridged["files"] == first["files"]
        S._last.clear()
        old = time.time() - 3600
        os.utime(saved, (old, old))
        with pytest.raises(RuntimeError, match="database down"):
            S.load_sitemap(pool)
        S._last.clear()
