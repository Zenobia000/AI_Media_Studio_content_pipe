"""Body HTML on the read side: plain text to HTML (X posts, translations), the signing of body images and the video clean-up. Port of
content/sanitize.ts (textToHtml), content/video.ts and media/imgproxy.ts (proxyBodyImages). Bodies are sanitized when they are saved; this
only prepares the saved HTML for a reader.

Markup is edited with html5lib, whose parser and serializer follow the same HTML specification as the TypeScript's parse5."""

from __future__ import annotations

import re
import time

import html5lib

from newsroom.engine.lib.image_url import is_non_article_image
from newsroom.engine.media.imgproxy import proxied_image, proxied_image_set
from newsroom.engine.media.renditions import AVIF_MODE_SUFFIX, IMAGE_WIDTHS  # noqa: F401

LIFETIME_SECONDS = 48 * 3600


def esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# A bare address ends at whitespace, CJK text, full-width punctuation or emoji; trailing sentence punctuation belongs to the sentence, not the link.
BARE_URL = re.compile('https?://[^\\s<>"^`{|}　-〿㐀-䶿一-鿿＀-￯぀-ヿ가-힯☀-➿️‍\U0001f300-\U0001faff]+')
TRAILING_PUNCT = re.compile("[.,;:!?*'\"”’»…、。，；：！？）\\]】}」』》]+$")
INLINE = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?://[^)\s]+\))")
LIST_ITEM = re.compile(r"^[ \t]*([-*+•]|\d+[.)])[ \t]+")
ORDERED = re.compile(r"^[ \t]*\d+[.)][ \t]+")
QUOTE = re.compile(r"^[ \t]*>[ \t]?")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+)$")


def _balanced(url: str) -> str:
    """An address without a closing bracket it never opened ("(see https://a.b/c)" keeps its ")")."""
    u = url
    while u.endswith(")") and u.count("(") < u.count(")"):
        u = u[:-1]
    return u


def _link(href: str, text: str) -> str:
    return f'<a href="{esc_html(href)}" target="_blank" rel="noopener noreferrer nofollow">{esc_html(text)}</a>'


def _linkify(text: str) -> str:
    """Escaped text with its bare addresses made into links."""
    out, last = "", 0
    for m in BARE_URL.finditer(text):
        url = _balanced(TRAILING_PUNCT.sub("", m.group(0)))
        if not re.match(r"^https?://[^/]+\.[^/]", url):
            continue
        out += esc_html(text[last : m.start()]) + _link(url, url)
        last = m.start() + len(url)
    return out + esc_html(text[last:])


def _inline(text: str) -> str:
    """Inline code, **bold** and [text](url), then bare addresses; everything else escaped."""
    parts = INLINE.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(_linkify(part))
        elif part.startswith("`"):
            out.append(f"<code>{esc_html(part[1:-1])}</code>")
        elif part.startswith("**"):
            out.append(f"<strong>{_linkify(part[2:-2])}</strong>")
        else:
            m = re.match(r"^\[([^\]]+)\]\((https?://[^)\s]+)\)$", part)
            out.append(_link(m.group(2), m.group(1)))  # type: ignore[union-attr]
    return "".join(out)


def _block(para: str) -> str:
    """One blank-line separated block: fenced code, a quote, a list, a heading, or a paragraph."""
    if para.startswith("```"):
        code = re.sub(r"\n?```$", "", re.sub(r"^```[^\n]*\n?", "", para, count=1))
        return f"<pre><code>{esc_html(code)}</code></pre>"
    lines = [line for line in para.split("\n") if line.strip()]
    if lines and all(QUOTE.match(line) for line in lines):
        return "<blockquote><p>" + "<br>".join(_inline(QUOTE.sub("", line, count=1)) for line in lines) + "</p></blockquote>"
    if len(lines) > 1 and all(LIST_ITEM.match(line) for line in lines):
        tag = "ol" if ORDERED.match(lines[0]) else "ul"
        return f"<{tag}>" + "".join(f"<li>{_inline(LIST_ITEM.sub('', line, count=1))}</li>" for line in lines) + f"</{tag}>"
    heading = HEADING.match(lines[0]) if len(lines) == 1 else None
    if heading:
        level = min(len(heading.group(1)) + 1, 6)
        return f"<h{level}>{_inline(heading.group(2))}</h{level}>"
    return "<p>" + "<br>".join(_inline(line) for line in para.split("\n")) + "</p>"


def text_to_html(text: str) -> str:
    """Plain text to HTML, for sources that only give text (X posts, translations): paragraphs and line breaks, the few Markdown marks people
    type (code, bold, links, lists, quotes, headings) and safe links for bare addresses. The text is never taken as HTML."""
    paras = [p.strip() for p in re.split(r"\n{2,}", re.sub(r"\r\n?", "\n", text))]
    return "".join(_block(p) for p in paras if p)


# ── HTML trees ──────────────────────────────────────────────────────────────────────────


def _parse(html: str):
    return html5lib.parseFragment(html, treebuilder="etree", namespaceHTMLElements=False)


def _serialize(root) -> str:
    walker = html5lib.getTreeWalker("etree")
    stream = html5lib.serializer.HTMLSerializer(
        quote_attr_values="always", omit_optional_tags=False, minimize_boolean_attributes=False, use_trailing_solidus=False,
        escape_lt_in_attrs=False, resolve_entities=False,
    )  # fmt: skip
    return stream.render(walker(root)).replace(" ", "&nbsp;")  # parse5 writes a no-break space as an entity, in text and values


def _text_of(el) -> str:
    return "".join(el.itertext())


def normalize_videos(root) -> None:
    """Only video sources survive; a rejected poster must not leave a player with nothing to show."""
    parents = {c: p for p in root.iter() for c in p}
    for source in [e for e in root.iter() if e.tag == "source"]:
        parent = parents.get(source)
        if parent is None or parent.tag != "video" or not (source.get("src") or "").strip():
            _remove(parents, source)
    for video in [e for e in root.iter() if e.tag == "video"]:
        poster = video.get("poster")
        if poster and is_non_article_image(poster):
            del video.attrib["poster"]
        has_source = any(c.tag == "source" and c.get("src") is not None and c.get("src") != "" for c in video)
        if (video.get("src") or "").strip() or has_source:
            video.set("controls", "")
            video.set("playsinline", "")
            video.set("preload", "none")
        else:
            for name in ("controls", "playsinline", "preload"):
                video.attrib.pop(name, None)
            if not video.get("poster"):
                _unwrap(parents, video)


def _remove(parents: dict, el) -> None:
    parent = parents[el]
    siblings = list(parent)
    i = siblings.index(el)
    tail = el.tail or ""
    parent.remove(el)
    if i == 0:
        parent.text = (parent.text or "") + tail
    else:
        prev = siblings[i - 1]
        prev.tail = (prev.tail or "") + tail


def _unwrap(parents: dict, video) -> None:
    """Replace the player with its fallback content, without joining the surrounding words."""
    parent = parents[video]
    siblings = list(parent)
    i = siblings.index(video)
    has_prev = i > 0 or bool(parent.text)
    has_next = i < len(siblings) - 1 or bool(video.tail)
    prefix = " " if has_prev else ""
    suffix = " " if has_next else ""
    inner_text = video.text or ""
    children = list(video)
    tail = video.tail or ""
    parent.remove(video)
    # Rebuild in place: text before, the fallback content, text after.
    anchor_text = prefix + inner_text
    if i == 0:
        parent.text = (parent.text or "") + anchor_text
    else:
        prev = siblings[i - 1]
        prev.tail = (prev.tail or "") + anchor_text
    pos = i
    for c in children:
        parent.insert(pos, c)
        pos += 1
    end = suffix + tail
    if children:
        children[-1].tail = (children[-1].tail or "") + end
    elif i == 0:
        parent.text = (parent.text or "") + end
    else:
        siblings[i - 1].tail = (siblings[i - 1].tail or "") + end


def proxy_body_images(html: str, absolute: bool = False, lifetime_seconds: int = LIFETIME_SECONDS) -> str:
    """Rewrites <img>/<video poster> sources in whitelisted body HTML to signed proxy URLs. Feed readers keep items for days, so full RSS asks
    for a longer signature than a page."""
    now = time.time() * 1000

    def img(m: re.Match[str]) -> str:
        tag, attrs = m.group(0), m.group(1)
        src = re.search(r'\ssrc="([^"]+)"', attrs, re.I)
        if not src:
            return tag
        w, h = re.search(r'\bwidth="([^"]+)"', attrs, re.I), re.search(r'\bheight="([^"]+)"', attrs, re.I)
        if is_non_article_image(src.group(1), w.group(1) if w else None, h.group(1) if h else None):
            return ""
        decoded = src.group(1).replace("&amp;", "&")
        proxied = proxied_image(decoded, "full", absolute, now, lifetime_seconds)
        # Width descriptors change an image's natural CSS size. Only use them for a body image with an explicit display width.
        wd = re.search(r'\bwidth="(\d+)"', attrs, re.I)
        width = int(wd.group(1)) if wd else 0
        candidates = proxied_image_set(decoded, "body", False, now, lifetime_seconds) if not absolute and width >= IMAGE_WIDTHS["thumb"] else None
        sizes = ' sizes="(min-width: 1536px) 760px, (min-width: 1024px) calc(100vw - 524px), (min-width: 640px) 608px, calc(100vw - 32px)"'
        responsive = f' srcset="{candidates.replace("&", "&amp;")}"{sizes}' if candidates else ""
        source = f' src="{proxied.replace("&", "&amp;")}"{responsive} loading="lazy" decoding="async"' if proxied else ""
        out = f"<img{attrs.replace(src.group(0), source, 1)}>"
        if not candidates or not proxied:
            return out
        # Browsers without AVIF skip the source and load the img's own candidates. A publisher's own picture takes the source: pictures do not nest.
        avif = f'<source type="image/avif" srcset="{avif_candidates(candidates).replace("&", "&amp;")}"{sizes}>'
        before = m.string[max(0, m.start() - 200) : m.start()]
        return f"{avif}{out}" if re.search(r"<picture\b[^>]*>\s*$", before, re.I) else f'<picture class="contents">{avif}{out}</picture>'

    def video(m: re.Match[str]) -> str:
        pre, src = m.group(1), m.group(2)
        proxied = proxied_image(src.replace("&amp;", "&"), "thumb", absolute, now, lifetime_seconds)
        return f'<video{pre} poster="{proxied.replace("&", "&amp;")}"' if proxied else f"<video{pre}"

    rewritten = re.sub(r"<video\b([^>]*?)\sposter=\"([^\"]+)\"", video, re.sub(r"<img\b([^>]*)>", img, html, flags=re.I), flags=re.I)
    if not re.search(r"<video\b", rewritten, re.I):
        return rewritten
    root = _parse(rewritten)
    normalize_videos(root)
    return _serialize(root)


def avif_candidates(src_set: str) -> str:
    from newsroom.engine.media.imgproxy import avif_candidates as impl  # noqa: PLC0415

    return impl(src_set)
