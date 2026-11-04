"""FET-FR-001: the text of one source as Markdown. The sites that have an interface of their own are read through it (GitHub, arXiv,
YouTube); everything else goes through Readability extraction, then Jina Reader when a key is set (SPK-FET-1: docs/spikes/SPK-FET-1.md).
A page that gives under 200 characters is not a source.

Readability here is readability-lxml with a plain clean-up; AIHOT's TypeScript extractor (content/extract.ts) also handled streamed
content, hidden elements, figure drawings and per-source page rules, which this does not."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

from lxml import html as lxml_html

from newsroom.core.config import credential
from newsroom.core.http import Get, Page, default_get
from newsroom.core.urls import site_identity_for_url
from newsroom.fetch.html_markdown import html_to_markdown

MIN_CHARS = 200
_CJK = re.compile("[㐀-鿿]")
_OTHER = re.compile(r"[A-Za-z0-9À-ɏ]+")


class ExtractFailure(Exception):
    """Why nothing could be taken from an address."""


@dataclass
class Extracted:
    kind: str  # article | tweet | release | video | paper | forum | other
    title: str
    publisher: str | None
    author: str | None
    published_at: str | None  # ISO-8601 in UTC, when the page says
    language: str
    method: str  # github-api | arxiv-api | youtube | readability | jina
    markdown: str
    links: list[str] = field(default_factory=list)  # addresses the text links to


def word_count(markdown: str) -> int:
    """Characters of text, Chinese counting one each and other words one each (a measure of length, not a word count)."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"[#>*`_~|-]", " ", text)
    return len(_CJK.findall(text)) + len(_OTHER.findall(_CJK.sub(" ", text)))


def language_of(text: str) -> str:
    return "zh" if len(_CJK.findall(text)) / max(1, len(text)) > 0.15 else "en"


def _long_enough(markdown: str) -> bool:
    return len(re.sub(r"\s+", "", markdown)) >= MIN_CHARS


def _meta(doc, *names: str) -> str | None:
    for name in names:
        found = doc.xpath(f'//meta[@property="{name}" or @name="{name}"]/@content')
        for content in found:
            if content.strip():
                return content.strip()
    return None


def _json(get: Get, url: str, headers: dict[str, str] | None = None):
    page = get(url, {"accept": "application/vnd.github+json", **(headers or {})})
    return json.loads(page.text) if page.status == 200 else None


def _github(get: Get, owner: str, repo: str, tag: str | None) -> Extracted:
    token = credential("collectors", "GITHUB_TOKEN")
    auth = {"authorization": f"Bearer {token}"} if token else {}
    readme = get(f"https://api.github.com/repos/{owner}/{repo}/readme", {"accept": "application/vnd.github.raw+json", **auth})
    ref = f"tags/{quote(tag, safe='')}" if tag else "latest"
    release = _json(get, f"https://api.github.com/repos/{owner}/{repo}/releases/{ref}", auth)
    parts = [f"# {owner}/{repo}"]
    if release and release.get("body"):
        name = release.get("name")
        parts += [f"## Release {release['tag_name']}" + (f"：{name}" if name and name != release["tag_name"] else ""), release["body"].strip()]
    if not tag and readme.status == 200:
        parts += ["## README", readme.text.strip()]
    markdown = "\n\n".join(parts)
    if not _long_enough(markdown):
        raise ExtractFailure(f"GitHub 沒有給出足夠的內容（README HTTP {readme.status}{'' if release else '，沒有 release'}）")
    return Extracted(
        "release" if release and release.get("body") else "other",
        f"{owner}/{repo}",
        "GitHub",
        owner,
        (release or {}).get("published_at"),
        language_of(markdown),
        "github-api",
        markdown,
        re.findall(r"\]\((https?:[^)\s]+)\)", markdown),
    )


def _arxiv(get: Get, arxiv_id: str) -> Extracted:
    page = get(f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='')}")
    i = page.text.find("<entry>")
    entry = page.text[max(0, i) :]
    summary = re.search(r"<summary>([\s\S]*?)</summary>", entry)
    title = re.search(r"<title>([\s\S]*?)</title>", entry)
    summary_t = re.sub(r"\s+", " ", summary.group(1)).strip() if summary else ""
    title_t = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
    if page.status != 200 or not summary_t or not title_t:
        raise ExtractFailure(f"arXiv 沒有找到 {arxiv_id} 的摘要（HTTP {page.status}）")
    authors = [a.strip() for a in re.findall(r"<author>\s*<name>([^<]+)</name>", entry)]
    markdown = f"# {title_t}\n\n作者：{'、'.join(authors)}\n\n## 摘要\n\n{summary_t}\n\nPDF：https://arxiv.org/pdf/{arxiv_id}"
    published = re.search(r"<published>([^<]+)</published>", entry)
    return Extracted(
        "paper",
        title_t,
        "arXiv",
        authors[0] if authors else None,
        published.group(1) if published else None,
        "en",
        "arxiv-api",
        markdown,
        [f"https://arxiv.org/pdf/{arxiv_id}"],
    )


def _youtube(get: Get, video_id: str) -> Extracted:
    watch = f"https://www.youtube.com/watch?v={video_id}"
    oembed = _json(get, f"https://www.youtube.com/oembed?url={quote(watch, safe='')}&format=json", {"accept": "application/json"})
    if not oembed:
        raise ExtractFailure("YouTube 沒有回應這支影片（可能是私人或已刪除）")
    page = get(watch)
    description = _meta(lxml_html.fromstring(page.text), "og:description", "description") if page.text.strip() else None
    markdown = f"# {oembed['title']}\n\n頻道：{oembed['author_name']}\n\n{description or ''}\n\n影片：{watch}"
    if not _long_enough(markdown):
        raise ExtractFailure("影片說明太短，沒有足夠的文字可以當原文；請把重點貼進 sources/manual-1.md")
    return Extracted("video", oembed["title"], "YouTube", oembed["author_name"], None, language_of(markdown), "youtube", markdown, [])


def readable(html: str, url: str) -> dict | None:
    """The article body of a page: {html, title, published_at}, or None when no body is found."""
    from readability import Document  # noqa: PLC0415

    try:
        doc = Document(html, url=url)
        body = doc.summary(html_partial=True)
        title = doc.short_title()
    except Exception:  # noqa: BLE001
        return None
    text = re.sub(r"\s+", "", lxml_html.fromstring(f"<div>{body}</div>").text_content()) if body else ""
    if len(text) < MIN_CHARS:
        return None
    return {"html": body, "title": title}


def _from_body(body: dict, res: Page, method: str) -> Extracted:
    markdown = html_to_markdown(body["html"], res.url)
    if not _long_enough(markdown):
        raise ExtractFailure(f"正文不到 {MIN_CHARS} 字")
    doc = lxml_html.fromstring(res.text) if res.text.strip() else lxml_html.fromstring("<html></html>")
    h1 = "".join(doc.xpath("//h1[1]//text()")).strip()
    title = h1 or _meta(doc, "og:title") or "".join(doc.xpath("//title/text()")).strip() or res.url
    published = _meta(doc, "article:published_time", "og:article:published_time", "date")
    if not published:
        times = doc.xpath("//time/@datetime")
        published = times[0] if times else None
    iso = None
    if published:
        try:
            d = datetime.fromisoformat(published.strip().replace("Z", "+00:00"))
            iso = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            iso = None
    lang = (doc.get("lang") or "")[:2] or language_of(markdown)
    host = re.sub(r"^www\.", "", urlsplit(res.url).hostname or "")
    links = [m.replace("&amp;", "&") for m in re.findall(r'href="(https?:[^"]+)"', body["html"])]
    return Extracted("article", title, _meta(doc, "og:site_name") or host, _meta(doc, "author", "article:author"), iso, lang, method, markdown, links)


def _page(get: Get, url: str) -> Extracted:
    failure = "沒有抓到內容"
    try:
        res = get(url, {"accept-language": "zh-TW,zh;q=0.9,en;q=0.8"})
        if res.status != 200:
            failure = f"HTTP {res.status}"
        elif "html" not in res.type:
            failure = f"不是網頁（{res.type or '未知型別'}）"
        else:
            body = readable(res.text, res.url)
            if body:
                return _from_body(body, res, "readability")
            failure = "網頁裡找不到足夠的正文（可能要執行 JavaScript，或在付費牆後面）"
    except ExtractFailure:
        raise
    except Exception as e:  # noqa: BLE001
        failure = str(e)[:120]
    key = credential("collectors", "JINA_API_KEY")
    if key:
        try:
            jina = get(f"https://r.jina.ai/{url}", {"authorization": f"Bearer {key}", "accept": "text/html", "x-return-format": "html"})
            body = readable(jina.text, url) if jina.status == 200 else None
            if body:
                return _from_body(body, Page(200, url, "text/html", ""), "jina")
        except ExtractFailure:
            raise
        except Exception:  # noqa: BLE001
            pass
        failure += "；Jina Reader 也沒有讀到"
    raise ExtractFailure(failure)


def extract_source(url: str, get: Get | None = None) -> Extracted:
    get = get or default_get
    site = site_identity_for_url(url)
    if site and site.startswith("arxiv:"):
        return _arxiv(get, site[len("arxiv:") :])
    if site and site.startswith("youtube:"):
        return _youtube(get, site[len("youtube:") :])
    if site and site.startswith("github:"):
        owner, repo = [p for p in urlsplit(url).path.split("/") if p][:2]
        return _github(get, owner, re.sub(r"\.git$", "", repo), None)
    release = re.match(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/releases/tag/([^/?#]+)", url, re.I)
    if release:
        return _github(get, release.group(1), release.group(2), unquote(release.group(3)))
    if re.match(r"^https?://(?:(?:www|mobile|m)\.)?(?:x|twitter)\.com/", url, re.I):
        raise ExtractFailure("推文沒有公開的讀取方式（需要 X API 或 SocialData，R4 才支援）")
    return _page(get, url)
