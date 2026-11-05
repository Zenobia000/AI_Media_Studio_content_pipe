"""Usage: python -I tests/tools/collect_real_pages.py <output.json>

Collects real article pages from many kinds of sites: reads each feed for its first links, downloads those pages, and saves [{name, url, html}] for the comparison."""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx

OUT = sys.argv[1]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
FEEDS = {
    "ithome": "https://www.ithome.com/rss/",
    "36kr": "https://36kr.com/feed",
    "sspai": "https://sspai.com/feed",
    "ifanr": "https://www.ifanr.com/feed",
    "huxiu": "https://www.huxiu.com/rss/0.xml",
    "jiqizhixin": "https://www.jiqizhixin.com/rss",
    "qbitai": "https://www.qbitai.com/feed",
    "solidot": "https://www.solidot.org/index.rss",
    "landian": "https://www.landiannews.com/feed",
    "geekpark": "https://www.geekpark.net/rss",
    "ithome_tw": "https://www.ithome.com.tw/rss",
    "technews_tw": "https://technews.tw/feed/",
    "bnext": "https://www.bnext.com.tw/rss",
    "udn": "https://udn.com/rssfeed/news/2/6644?ch=news",
    "cna": "https://feeds.feedburner.com/cnaFirstNews",
    "hn": "https://hnrss.org/frontpage",
    "verge": "https://www.theverge.com/rss/index.xml",
    "techcrunch": "https://techcrunch.com/feed/",
    "ars": "https://arstechnica.com/feed/",
    "wired": "https://www.wired.com/feed/rss",
    "bbc_tech": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "guardian": "https://www.theguardian.com/uk/technology/rss",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "github_blog": "https://github.blog/feed/",
    "openai": "https://openai.com/news/rss.xml",
    "simonw": "https://simonwillison.net/atom/everything/",
    "reddit": "https://www.reddit.com/r/programming/.rss",
    "lobsters": "https://lobste.rs/rss",
    "devto": "https://dev.to/feed",
    "so_blog": "https://stackoverflow.blog/feed/",
    "google_blog": "https://blog.google/rss/",
    "hf_blog": "https://huggingface.co/blog/feed.xml",
    "v2ex": "https://www.v2ex.com/index.xml",
    "linuxdo": "https://linux.do/latest.rss",
    "zenn": "https://zenn.dev/feed",
    "medium": "https://medium.com/feed/tag/artificial-intelligence",
    "arxiv": "http://export.arxiv.org/rss/cs.CL",
    "nyt": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "mit_tr": "https://www.technologyreview.com/feed/",
    "infoq_cn": "https://www.infoq.cn/feed",
    "oschina": "https://www.oschina.net/news/rss",
    "cnblogs": "https://www.cnblogs.com/rss",
    "juejin": "https://rsshub.app/juejin/trending/all/weekly",
    "ruanyifeng": "https://www.ruanyifeng.com/blog/atom.xml",
}
DIRECT = {
    "wiki_zh_ai": "https://zh.wikipedia.org/wiki/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
    "wiki_en_python": "https://en.wikipedia.org/wiki/Python_(programming_language)",
    "py_docs": "https://docs.python.org/3/whatsnew/3.13.html",
    "mdn": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide",
    "anthropic_news": "https://www.anthropic.com/news",
    "wechat": "https://mp.weixin.qq.com/s/example",
    "github_readme": "https://github.com/psf/requests",
    "ptt": "https://www.ptt.cc/bbs/Tech_Job/index.html",
    "bahamut": "https://forum.gamer.com.tw/",
    "zhihu": "https://www.zhihu.com/question/19550225",
}


def get(url):
    try:
        r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}, timeout=20, follow_redirects=True)
        return r
    except Exception:
        return None


def links(xml: str, n: int = 2):
    found = re.findall(r"<link[^>]*href=\"([^\"]+)\"[^>]*/?>", xml) + re.findall(r"<link>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\s\]]+)", xml)
    seen = []
    for u in found:
        if u.startswith("http") and u not in seen and not u.endswith(("/rss", "/rss/", "/feed", "/feed/", ".xml", "/atom.xml")):
            seen.append(u)
    return seen[1 : n + 1] if len(seen) > n else seen[:n]


def work(item):
    name, feed = item
    r = get(feed)
    if r is None or r.status_code != 200:
        return []
    out = []
    for i, u in enumerate(links(r.text)):
        p = get(u)
        if p is not None and p.status_code == 200 and "html" in p.headers.get("content-type", "") and len(p.text) > 1500:
            out.append({"name": f"{name}-{i}", "url": str(p.url), "html": p.text})
    return out


def direct(item):
    name, u = item
    p = get(u)
    if p is not None and p.status_code in (200, 403, 404) and "html" in p.headers.get("content-type", "") and len(p.text) > 500:
        return [{"name": f"{name}", "url": str(p.url), "html": p.text, "status": p.status_code}]
    return []


with ThreadPoolExecutor(12) as ex:
    pages = [p for res in ex.map(work, FEEDS.items()) for p in res] + [p for res in ex.map(direct, DIRECT.items()) for p in res]
json.dump(pages, open(OUT, "w", encoding="utf8"), ensure_ascii=False)
print(len(pages), "pages from", len({p["name"].rsplit("-", 1)[0] for p in pages}), "sources")
for p in pages:
    print(p["name"], p["status"] if "status" in p else 200, len(p["html"]), p["url"][:90])
