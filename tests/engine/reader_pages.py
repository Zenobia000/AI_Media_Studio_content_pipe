"""Pages for comparing the article reader with the TypeScript one: the layouts articles come in, and the clutter around them."""

from __future__ import annotations


def para(n: int, words: int = 40, commas: bool = True) -> str:
    base = "The committee reviewed the proposal" + (", and" if commas else " and") + f" after a long debate{', ' if commas else ' '}paragraph {n} concluded that the plan was sound. "
    return "<p>" + (base * max(1, words // 12)).strip() + "</p>"


def body(count: int = 6, **kw) -> str:
    return "\n".join(para(i, **kw) for i in range(count))


NAV = '<nav class="main-nav"><ul><li><a href="/">Home</a></li><li><a href="/world">World</a></li><li><a href="/tech">Tech</a></li><li><a href="/login">Log in</a></li></ul></nav>'
FOOT = '<footer id="footer"><p>Copyright 2026 Example News. <a href="/terms">Terms</a> <a href="/privacy">Privacy</a></p></footer>'
SIDE = '<aside class="sidebar related-posts"><h3>Related</h3><ul>' + "".join(f'<li><a href="/r/{i}">Related story number {i}</a></li>' for i in range(8)) + "</ul></aside>"
COMMENTS = '<div id="comments" class="comments"><h3>Comments</h3>' + "".join(f'<div class="comment"><p>Great article number {i}, thanks!</p></div>' for i in range(5)) + "</div>"
SHARE = '<div class="share-buttons"><a href="#">Share</a><a href="#">Tweet</a></div>'


def page(title: str, main: str, head: str = "", lang: str = "en", extra_body: str = "") -> str:
    return f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8"><title>{title}</title>{head}</head><body>{extra_body}{main}</body></html>'


PAGES: dict[str, str] = {}

PAGES["news"] = page(
    "Council approves new transit plan | Example News",
    f'<header class="site-header">{NAV}</header><div id="wrapper"><article class="post hentry"><h1 class="entry-title">Council approves new transit plan</h1>'
    f'<p class="byline">By <span class="author">Jane Reporter</span></p><div class="entry-content">{body(7)}{SHARE}</div>{COMMENTS}</article>{SIDE}</div>{FOOT}',
)
PAGES["blog_lazy_images"] = page(
    "A day at the market - Travel Blog",
    '<div class="content"><h1>A day at the market</h1>'
    f"{para(1)}<figure><img src=\"data:image/gif;base64,R0lGODlhAQABAAAAACw=\" data-src=\"/img/market1.jpg\" class=\"lazy\"><figcaption>The market at dawn</figcaption></figure>{para(2)}"
    '<noscript><img src="/img/market2.jpg"></noscript><img src="/img/placeholder.gif" alt="">'
    '<img data-srcset="/img/m3.jpg 1x, /img/m3@2x.jpg 2x" class="lazy"><picture><source srcset="/img/m4.webp"><img src="/img/m4.jpg"></picture>'
    f'{body(4)}<a href="javascript:void(0)">Click</a> <a href="javascript:go()"><b>Bold</b> link</a> <a href="#top">Top</a> <a href="../up/page.html">Up</a></div>',
    head='<base href="https://blog.example.org/posts/day/">',
)
PAGES["jsonld_meta"] = page(
    "Ignored page title",
    f'<div id="main"><h1>The Real Headline Of This Story</h1><div class="byline">Staff</div>{body(6)}</div>',
    head=(
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"The Real Headline Of This Story","name":"Site Name Home",'
        '"author":{"@type":"Person","name":" Alice Writer "},"description":"A short description &amp; summary.","publisher":{"@type":"Organization","name":"The Daily"},'
        '"datePublished":"2026-10-30T08:00:00Z"}</script>'
        '<meta property="og:title" content="OG title"><meta property="og:description" content="OG desc &quot;quoted&quot;"><meta name="author" content="Meta Author">'
        '<meta property="article:published_time" content="2026-10-29T00:00:00Z"><meta name="twitter:title" content="Tw">'
    ),
)
PAGES["jsonld_array_graph"] = page(
    "Graph page",
    f"<main>{body(6)}</main>",
    head=(
        '<script type="application/ld+json">[{"@type":"WebSite","name":"x"},{"@context":"https://schema.org","@type":"BlogPosting","name":"Blog post name","author":[{"name":"A"},{"name":"B"}]}]</script>'
        '<script type="application/ld+json">{"@context":"http://schema.org/","@graph":[{"@type":"Person"},{"@type":"TechArticle","headline":"Graph headline"}]}</script>'
        '<script type="application/ld+json">{broken json</script><script type="application/ld+json">null</script>'
    ),
)
PAGES["br_paragraphs"] = page(
    "Old style article",
    f'<div id="content"><font size="3">First paragraph of the old style article, with several clauses, and commas, that goes on for a while.<br><br>'
    f"Second paragraph follows the double break, and also has commas, plus enough words to count as text.<br/>  <br>Third paragraph after another break, <b>with bold</b> text and <i>italic</i> text, finishing here.<br><br>"
    f'<div>Block child</div>Trailing text, after the block, with some more words to read.</font><p>{"A normal paragraph, with commas, to follow. " * 8}</p></div>',
)
PAGES["layout_table"] = page(
    "Table layout page",
    '<table width="600" border="0"><tr><td width="150"><a href="/a">Link A</a><br><a href="/b">Link B</a></td><td>'
    f'{body(5)}<table class="data"><caption>Results</caption><thead><tr><th>Name</th><th>Score</th></tr></thead><tbody><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></tbody></table>'
    "</td></tr></table>",
)
PAGES["single_cell_tables"] = page(
    "Nested single cell",
    f'<div><table><tbody><tr><td>{body(3)}</td></tr></tbody></table><table><tr><td>Just a phrase, in a cell</td></tr></table>{body(3)}</div>',
)
PAGES["short_needs_retries"] = page(
    "Short story",
    f'<div class="comment-box sidebar"><p>{"A short story told in few words, with commas, and little else. " * 3}</p></div><div class="footer-note"><p>{"Footer text, with commas, and more. " * 4}</p></div>',
)
PAGES["hidden_stuff"] = page(
    "Hidden elements",
    f'<div id="a"><h1>Visible heading of the article</h1><div style="display:none"><p>{"Hidden by style, with commas. " * 6}</p></div>'
    f'<div style="DISPLAY: None"><p>Odd case, not hidden</p></div><div hidden><p>{"Hidden attr text. " * 8}</p></div><div aria-hidden="true"><p>{"Aria hidden. " * 8}</p></div>'
    f'<div aria-hidden="true" class="fallback-image"><p>Fallback shown, with commas, and text.</p></div><div style="visibility: hidden"><p>{"Invisible. " * 8}</p></div>'
    f'<div role="dialog" aria-modal="true"><p>{"A modal dialog. " * 8}</p></div><div role="navigation"><p>{"Navigation role text, with commas. " * 6}</p></div>{body(5)}</div>',
)
PAGES["lists_quotes_code"] = page(
    "Mixed blocks",
    f'<article><h1>Mixed blocks article</h1>{para(1)}<ul><li>First item of the list, with commas, and some text.</li><li>Second item, also with commas, and text.</li></ul>'
    f"<blockquote>{para(2)}</blockquote><pre><code>def f(x):\n    return x + 1\n</code></pre><ol><li><a href=\"/x\">link only</a></li><li><a href=\"/y\">another link</a></li></ol>{body(3)}</article>",
)
PAGES["nested_divs"] = page(
    "Nested wrappers",
    f'<div id="outer"><div><section><div><div><section>{body(6)}</section></div></div></section></div></div>',
)
PAGES["no_body_tag"] = f"<html><head><title>No body here</title></head><div>{body(6)}</div></html>"
PAGES["fragment"] = f"<div><h2>A fragment</h2>{body(6)}</div>"
PAGES["title_variants"] = page("Section: The long title after the colon is the real one", f"<div>{body(6)}</div>")
PAGES["title_pipe_short"] = page("Home | A Very Long Site Name That Goes On", f"<div><h1>Home</h1>{body(6)}</div>")
PAGES["title_h1_only"] = page("Hi", f"<div><h1>The Only Heading Of This Page</h1>{body(6)}</div>")
PAGES["chinese"] = page(
    "測試文章｜示例網站",
    '<div class="article-content"><h1>測試文章</h1>'
    + "".join(f"<p>這是第{i}段，內容包含許多逗號，用來測試中文逗號的計分，還有更多文字，確保長度足夠，讓演算法認為這是正文。</p>" for i in range(8))
    + "</div>",
    lang="zh-TW",
)
PAGES["link_heavy"] = page(
    "Link heavy",
    '<div id="menu-block">' + "".join(f'<a href="/x/{i}">Navigation link {i} with some words</a> ' for i in range(30)) + f'</div><div class="story">{body(5)}</div>',
)
PAGES["share_and_ads"] = page(
    "Ads and shares",
    f'<div class="article-body"><div class="sharedaddy"><p>Share this: Facebook Twitter</p></div>{body(4)}<div id="ad-slot" class="advertisement"><p>Advertisement</p></div>'
    f'<div class="social-share"><p>Share on social media</p></div>{body(3)}<div><p>Loading...</p></div><div class="related"><h3>You may also like</h3><p><a href="/z">Z</a></p></div></div>',
)
PAGES["iframes_embeds"] = page(
    "Embeds",
    f'<div class="post">{body(3)}<iframe src="https://www.youtube.com/embed/abc"></iframe><iframe src="https://ads.example.com/frame"></iframe>'
    '<object data="x.swf"><embed src="https://player.vimeo.com/video/1"></object><embed src="y.swf">'
    f'<form action="/s"><input type="text"><button>Go</button></form>{body(3)}</div>',
)
PAGES["svg_figure"] = page(
    "Figures",
    f'<article>{body(3)}<figure><svg viewBox="0 0 10 10"><title>Chart title</title><rect width="5" height="5"/></svg><figcaption>Chart caption text</figcaption></figure>{body(3)}'
    '<figure><img src="/a.png" width="300" height="200" style="float:left" align="left" border="1"><figcaption>An image</figcaption></figure></article>',
)
PAGES["byline_variants"] = page(
    "Bylines",
    f'<div class="article"><div class="post-meta"><span itemprop="author"><span itemprop="name">Pat Itemprop</span></span> <time>2026</time></div>{body(5)}<p class="byline">Late byline text</p></div>',
)
PAGES["dir_rtl"] = page("RTL page", f'<div dir="rtl" class="content">{body(6)}</div>', lang="ar")
PAGES["unlikely_but_ok"] = page(
    "Unlikely",
    f'<div class="header-and-article" id="main-column"><div class="comment-area content"><p>{"Comment-ish but in content, with commas, and words. " * 6}</p></div>{body(4)}</div>',
)
PAGES["entities_text"] = page(
    "Entities &amp; things &#8211; Example",
    f"<div><p>Caf&eacute; &amp; bar &lt;b&gt; &copy; 2026 &nbsp; non&#8209;breaking, and entities, and more text to make it long enough for the scorer.</p>{body(5)}</div>",
)
PAGES["comments_in_dom"] = page("Comments", f"<div><!-- a comment -->{body(5)}<!-- another --></div>")
PAGES["empty_wrappers"] = page("Empty", f'<div class="x"><div></div><div><br></div><section><hr></section>{body(5)}<h2></h2><p></p><p>&nbsp;</p></div>')


def random_pages(count: int, seed: int = 7) -> dict[str, str]:
    """Pages assembled at random from the blocks above and a pool of class names, ids, tags and attributes the scorer looks at."""
    import random

    rng = random.Random(seed)
    classes = ["content", "article", "post", "entry", "main", "story", "text", "comment", "sidebar", "footer", "header", "menu", "ad-break", "share", "related", "hidden",
               "promo", "widget", "banner", "social", "sponsor", "page", "blog", "body", "column", "and", "tags", "meta", "media", "print", "byline", "author", "x", ""]  # fmt: skip
    wrappers = ["div", "div", "div", "section", "article", "main", "span", "blockquote", "td", "li", "aside", "header", "footer", "figure", "details", "p"]
    leaves = [lambda: para(rng.randrange(99), rng.choice([12, 30, 80])), lambda: f'<a href="/x/{rng.randrange(50)}">link {rng.randrange(50)} text</a>',
              lambda: f"<h{rng.randrange(1, 7)}>Heading {rng.randrange(99)} of the part</h{rng.randrange(1, 7)}>".replace("</h", "</h", 1), lambda: "<br>", lambda: "<hr>",
              lambda: "<ul><li>one, two</li><li>three</li></ul>", lambda: '<img src="/i/%d.jpg" width="%d">' % (rng.randrange(9), rng.choice([10, 300, 800])),
              lambda: "<pre>code block, here</pre>", lambda: "text node with, commas and a long sentence that keeps going on and on for some words",
              lambda: '<table><tr><td>cell one</td><td>cell two</td></tr><tr><td>cell three</td><td>cell four</td></tr></table>', lambda: "<!-- note -->", lambda: "<p></p>",
              lambda: f'<iframe src="https://{rng.choice(["www.youtube.com", "ads.example.com"])}/e"></iframe>', lambda: "<form><input name=q><button>Go</button></form>"]  # fmt: skip
    leaves += [lambda: '<svg class="icon share" viewBox="0 0 1 1"><path d="M0"/></svg>', lambda: '<figure><svg><title>t</title></svg><figcaption>cap</figcaption></figure>',
               lambda: '<noscript><img src="/n/%d.jpg"></noscript>' % rng.randrange(9), lambda: '<img data-src="/lazy/%d.jpg" class="lazy">' % rng.randrange(9),
               lambda: '<picture><source srcset="/p.webp"><img src="/p.jpg"></picture>', lambda: "<font color=red>old font text, with commas, here</font>",
               lambda: '<DIV CLASS="Upper Case">Upper case markup, with commas, in a div</DIV>', lambda: "<p>para<br><br>after double break, with commas<br><br>and again</p>",
               lambda: '<a href="javascript:void(0)">js</a><a href="#frag">frag</a><a href="rel/path">rel</a>', lambda: "<h1>Page heading</h1>",
               lambda: '<time datetime="2026-01-01">Jan</time><span class="byline">By Someone</span>', lambda: "<dl><dt>t</dt><dd>d</dd></dl>"]

    def block(depth: int) -> str:
        if depth <= 0 or rng.random() < 0.3:
            return rng.choice(leaves)()
        tag = rng.choice(wrappers)
        attrs = ""
        if rng.random() < 0.6:
            attrs += f' class="{rng.choice(classes)} {rng.choice(classes)}"'.replace(" ", " ", 1)
        if rng.random() < 0.3:
            attrs += f' id="{rng.choice(classes)}"'
        if rng.random() < 0.08:
            attrs += rng.choice([' style="display:none"', " hidden", ' aria-hidden="true"', ' role="navigation"', ' itemprop="author"', ' rel="author"', ' style="width:10px"'])
        inner = "".join(block(depth - 1) for _ in range(rng.randrange(1, 5)))
        return f"<{tag}{attrs}>{inner}</{tag}>"

    pages = {}
    for i in range(count):
        main = "".join(block(rng.randrange(2, 6)) for _ in range(rng.randrange(1, 5)))
        pages[f"random{i}"] = page(rng.choice(["Random page title here", "Short | Site", "A: colon title for the page", "Hi"]), main)
    return pages
