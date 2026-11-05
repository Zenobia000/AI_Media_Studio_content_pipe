"""Reuse translated wording when only the source's layout, media or link destinations change. Port of content/presentation.ts."""

from __future__ import annotations

from newsroom.engine._readability_p1 import js_trim
from newsroom.engine.fragment import (
    ancestors,
    attrib,
    attribs,
    elements,
    inner_html,
    outer_html,
    parse,
    replace_with_html,
    replace_with_text,
    set_attribs,
    set_inner_html,
    text_of,
)
from newsroom.engine.lib.text import collapse_whitespace

BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "figcaption", "td", "th", "dt", "dd", "caption"}
MEDIA = {"picture", "video", "img"}


class PresentationMismatch(Exception):
    pass


def presentation_counts(html: str | None) -> dict:
    els = elements(parse(html or ""))
    return {"images": sum(e.tagName == "img" for e in els), "links": sum(e.tagName == "a" and e.hasAttribute("href") for e in els)}


def _text_blocks(root) -> list:
    return [el for el in elements(root) if el.tagName in BLOCK and not any(d.tagName in BLOCK for d in elements(el)) and js_trim(text_of(el))]


def _top_media(el) -> list:
    """The pictures, videos and images inside an element that are not inside another of them."""
    return [m for m in elements(el) if m.tagName in MEDIA and not any(a.tagName in MEDIA for a in ancestors(m, el))]


def _shell(html: str) -> str:
    root = parse(html)
    for i, a in enumerate(e for e in elements(root) if e.tagName == "a"):
        set_attribs(a, [("id", str(i))])
    for el in [e for e in elements(root) if e.tagName in MEDIA]:
        if not any(a.tagName in MEDIA for a in ancestors(el)):
            replace_with_text(el, "⟦media⟧")
    return collapse_whitespace(inner_html(root))


def rebase_translation(previous: str, next_: str, translated: str) -> str:
    """Preserve wording only where each old source block still has the same inline text structure."""
    old, current, zh = parse(previous), parse(next_), parse(translated)
    old_blocks, new_blocks, translated_blocks = _text_blocks(old), _text_blocks(current), _text_blocks(zh)
    if len(old_blocks) != len(new_blocks) or len(old_blocks) != len(translated_blocks):
        raise PresentationMismatch("正文段落結構已變化，需要正常重翻，不能只重新整理展示")
    for i, block in enumerate(old_blocks):
        fresh = new_blocks[i]
        if _shell(inner_html(block)) != _shell(inner_html(fresh)):
            raise PresentationMismatch(f"第 {i + 1} 段的文字或內聯結構已變化，需要正常重翻，不能只重新整理展示")
        rendered = parse(inner_html(translated_blocks[i]))
        links = [e for e in elements(fresh) if e.tagName == "a"]
        old_links = [e for e in elements(block) if e.tagName == "a"]
        zh_links = [e for e in elements(rendered) if e.tagName == "a"]
        media, old_media = _top_media(fresh), _top_media(block)
        zh_media = [e for e in elements(rendered) if e.tagName in MEDIA and not any(a.tagName in MEDIA for a in ancestors(e))]
        if (
            len(links) != len(zh_links)
            or len(media) != len(zh_media)
            or any(attrib(link, "href") != (attrib(zh_links[k], "href") if k < len(zh_links) else None) for k, link in enumerate(old_links))
        ):
            raise PresentationMismatch(f"第 {i + 1} 段的譯文連結或圖片無法對應，需要正常重翻")
        for k, link in enumerate(zh_links):
            set_attribs(link, attribs(links[k]))
        # The model may move media tokens within a sentence. Match the original token, never its position in the translated wording; repeated identical tokens
        # must have the same replacement.
        replacements: dict[str, list[str]] = {}
        for k, el in enumerate(old_media):
            key, replacement = outer_html(el), outer_html(media[k])
            matches = replacements.setdefault(key, [])
            if any(value != replacement for value in matches):
                raise PresentationMismatch(f"第 {i + 1} 段的重複圖片無法唯一對應，需要正常重翻")
            matches.append(replacement)
        for el in zh_media:
            queue = replacements.get(outer_html(el))
            if not queue:
                raise PresentationMismatch(f"第 {i + 1} 段的譯文圖片無法對應，需要正常重翻")
            replace_with_html(el, queue.pop(0))
        set_inner_html(fresh, inner_html(rendered))
    return inner_html(current)
