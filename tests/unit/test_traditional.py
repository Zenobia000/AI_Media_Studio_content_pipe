"""Simplified -> Traditional for what a page shows (engine/traditional.py): off by default, converts only text between tags, never the markup or URLs."""

from newsroom.engine import traditional


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("TRADITIONAL_DISPLAY", raising=False)
    assert traditional.for_display("<p>软件</p>") == "<p>软件</p>"


def test_converts_characters_and_taiwan_words(monkeypatch):
    monkeypatch.setenv("TRADITIONAL_DISPLAY", "true")
    assert traditional.for_display("<p>软件和内存</p>") == "<p>軟體和記憶體</p>"


def test_markup_and_urls_are_untouched(monkeypatch):
    monkeypatch.setenv("TRADITIONAL_DISPLAY", "true")
    source = '<a href="https://例子.com/软件" title="软件">软件</a><img src="/图.png" alt="图">'
    assert traditional.for_display(source) == '<a href="https://例子.com/软件" title="软件">軟體</a><img src="/图.png" alt="图">'


def test_plain_text_titles(monkeypatch):
    monkeypatch.delenv("TRADITIONAL_DISPLAY", raising=False)
    assert traditional.plain_for_display("软件") == "软件"
    monkeypatch.setenv("TRADITIONAL_DISPLAY", "true")
    assert traditional.plain_for_display("软件") == "軟體"
    assert traditional.plain_for_display(None) is None
    assert traditional.plain_for_display("") == ""


def test_inbox_title_shown_in_traditional(monkeypatch):
    from newsroom.collect.inbox import Candidate, render_block  # noqa: PLC0415

    candidate = Candidate("cand_x", "Uber订单仅4单", "B", 2, ["The Verge"], "08:00", "ai-today", ["threads"])
    monkeypatch.delenv("TRADITIONAL_DISPLAY", raising=False)
    assert "**Uber订单仅4单**" in render_block([candidate], {})
    monkeypatch.setenv("TRADITIONAL_DISPLAY", "true")
    assert "**Uber訂單僅4單**" in render_block([candidate], {})
    assert candidate.title == "Uber订单仅4单"


def test_traditional_text_stays(monkeypatch):
    monkeypatch.setenv("TRADITIONAL_DISPLAY", "true")
    assert traditional.for_display("<p>乾隆皇帝</p>") == "<p>乾隆皇帝</p>"
    assert traditional.for_display("") == ""
