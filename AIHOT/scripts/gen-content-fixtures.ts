// Runs the TypeScript body helpers on fixed inputs and prints the results as JSON, for the Python port's tests
// (tests/engine/content_fixtures.json). Needs IMG_PROXY_SIGN_SECRET and SITE_URL, as the parity harness sets them.
//   node scripts/gen-content-fixtures.ts > ../tests/engine/content_fixtures.json
import * as cheerio from "cheerio";
import { textToHtml, sanitizeBody, trimTrailingChrome } from "@aihot/backend/content/sanitize";
import { normalizeVideos } from "@aihot/backend/content/video";
import { bodyToMarkdown } from "@aihot/backend/content/markdown";
import { proxyBodyImages } from "@aihot/backend/media/imgproxy";

const texts = [
  "Hello world", "Line one\nline two\n\nSecond paragraph", "# Title\n\nSome **bold** and `code` and [link](https://a.example/x)",
  "- one\n- two\n- three", "1. first\n2) second", "> quoted\n> text", "```js\nlet a = 1 < 2;\n```", "See https://example.com/path?a=1&b=2, and (https://x.org/a_(b)) done.",
  "中文網址 https://example.org/測試 之後", "<script>alert(1)</script> & \"quotes\"", "Mixed\r\nline endings\r\n\r\nnext", "", "   \n\n  ", "* a\n* b\n\nplain",
  "https://nodots/ and https://a.b/c.", "emoji 😀 https://e.example/x😀y", "## Heading two", "####### too many",
];
const images = [
  '<p>No image</p>', '<p><img src="https://example.org/a.png" alt="a"></p>', '<p><img src="https://example.org/a.png?x=1&amp;y=2" width="800" height="600" alt="b"></p>',
  '<p><img src="https://example.org/s.png" width="1" height="1"></p>', '<picture><img src="https://example.org/p.png" width="900"></picture>', '<p><img alt="nosrc"></p>',
  '<img src="data:image/png;base64,AAAA" width="720">', '<img src="/relative.png" width="800">', '<video src="https://v.example/a.mp4" poster="https://example.org/poster.jpg"></video>',
  '<p>a <video><source src="https://v.example/a.webm" type="video/webm"></video> b</p>', '<p>x <video poster="https://example.org/p.jpg"><source src=""></video> y</p>',
  '<p>one<video>fallback text</video>two</p>', '<video controls><source src="https://v.example/x.mp4"><p>old</p></video>', '<p>Tom &amp; Jerry</p><video src=" "></video>',
];
const videos = [
  '<video src="https://a/b.mp4"></video>', '<p>a<video>f</video>b</p>', '<video poster="data:text/html,x"></video>', '<video><source src="https://a/b.webm"></video>',
  '<div><source src="https://a/c.webm"></div>', '<p>before <video><b>bold</b> fallback</video> after</p>', '<video></video><p>x</p>', '<p>&amp; &lt; &gt; "q"   x</p><video src="a"></video>',
];
const bodies = [
  '<p>Hello <b>world</b></p>', '<h2>Title</h2><p>Text with <a href="https://a.example/x">link</a>.</p><ul><li>one</li><li>two</li></ul>',
  '<pre><code class="language-js">let a = 1;</code></pre>', '<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>',
  '<blockquote><p>Quote</p></blockquote><p><img src="https://example.org/a.png" alt="pic"></p>', '<div>loose <span>text</span></div><section>section</section>',
  '<h1>Big</h1><h6>Small</h6><p>x</p>', '<p>a</p><script>alert(1)</script><style>p{}</style><p>b</p>', '<p>Topics</p>',
  '<p>Real paragraph.</p><p>Topics</p><p>Subscribe now</p>', '<ol><li>a</li><li>b</li></ol><hr><p><em>em</em> <del>gone</del></p>',
];

const out = {
  generatedAtMs: Date.now(),
  textToHtml: texts.map((t) => ({ in: t, out: textToHtml(t) })),
  proxyBodyImages: images.flatMap((h) => [false, true].map((absolute) => ({ in: h, absolute, out: proxyBodyImages(h, absolute) }))),
  normalizeVideos: videos.map((h) => {
    const $ = cheerio.load(h, null, false);
    normalizeVideos($);
    return { in: h, out: $.html() };
  }),
  sanitizeBody: bodies.map((h) => ({ in: h, base: "https://site.example/post/1", out: sanitizeBody(h, "https://site.example/post/1") })),
  trimTrailingChrome: bodies.map((h) => ({ in: h, out: trimTrailingChrome(h) })),
  bodyToMarkdown: bodies.map((h) => ({ in: h, base: "https://site.example/post/1", out: bodyToMarkdown(h, "https://site.example/post/1") })),
};
process.stdout.write(JSON.stringify(out));
