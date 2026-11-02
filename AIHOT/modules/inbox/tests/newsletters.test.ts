import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { closeDb, sql } from "@aihot/backend/db";
import { identityKeyForUrl } from "@aihot/backend/lib/url";
import { LINKS_SOURCE, splitNewsletters, storyLinks } from "../newsletters.ts";
import type { Get, Page } from "../source-extract.ts";

after(() => closeDb());

const text = "這一期電子報的內文夠長，才不會被當成導覽列或廣告而被忽略，它每一段都在介紹一則新聞。".repeat(3);
const issueHtml = (links: Array<[href: string, label: string]>) =>
  `<!doctype html><html lang="en"><head><title>Issue</title></head><body><article><h1>Today</h1><p>${text}</p><p>${text}</p>${links.map(([href, label]) => `<p>${text}<a href="${href}">${label}</a></p>`).join("")}</article></body></html>`;
const page = (body: string, url: string): Page => ({ status: 200, url, type: "text/html; charset=utf-8", text: body });
const recorded = (pages: Record<string, string>): Get => async (url) => (pages[url] !== undefined ? page(pages[url]!, url) : { status: 404, url, type: "text/plain", text: "" });

async function newsletter(name: string) {
  const id = `test-news-${tag()}`;
  await sql`INSERT INTO sources (id, name, kind, tags, next_fetch_at) VALUES (${id}, ${name}, 'rss', ${["電子報"]}, '2100-01-01')`;
  return id;
}
const issueOf = async (sourceId: string, n: string) => {
  const url = `https://letters.example.org/issue-${n}-${tag()}`;
  const { articleId } = await upsertMaterial({ sourceId, url, title: `Issue ${n}`, via: "fetch" });
  return { id: articleId, url };
};
const articlesOf = async (url: string): Promise<string[]> => (await sql<{ source_id: string }[]>`SELECT source_id FROM articles WHERE identity_key = ${identityKeyForUrl(url)!}`).map(r => r.source_id);
const mentions = async (url: string) => (await sql<{ value: number }[]>`SELECT value FROM signal_points WHERE identity_key = ${identityKeyForUrl(url)!} AND metric = 'newsletter_mentions' ORDER BY observed_at DESC LIMIT 1`)[0]?.value;

test("an issue is split into one article per story link; sponsors, social links and its own pages are left out", async () => {
  const source = await newsletter("Letters");
  const issue = await issueOf(source, "a");
  const official = `https://blog.official.example/launch-${tag()}`;
  const other = `https://other.example/story-${tag()}`;
  const result = await splitNewsletters({
    queue: false,
    get: recorded({ [issue.url]: issueHtml([[official, "Official launches a new model"], [`${other}?utm_source=tldr`, "Another story about agents"], ["https://example-ads.com/buy", "Sponsor: buy our product"], ["https://x.com/someone/status/1", "A thread worth reading"], ["https://letters.example.org/subscribe", "Subscribe to the newsletter"]]) }),
  });
  assert.equal(result.issues, 1);
  assert.equal(result.links, 2);
  assert.equal(result.created, 2);
  assert.deepEqual(await articlesOf(official), [LINKS_SOURCE]);
  assert.deepEqual(await articlesOf(other), [LINKS_SOURCE]);
  assert.deepEqual(await articlesOf("https://example-ads.com/buy"), []);
  assert.equal(await mentions(official), 1);
});

test("a link that a feed already brought stays one article, and every newsletter that mentions it adds to its count", async () => {
  const first = await newsletter("Letters one");
  const second = await newsletter("Letters two");
  const a = await issueOf(first, "x");
  const b = await issueOf(second, "y");
  const shared = `https://blog.official.example/shared-${tag()}`;
  const fromFeed = `test-feed-${tag()}`;
  await sql`INSERT INTO sources (id, name, kind, next_fetch_at) VALUES (${fromFeed}, 'Feed', 'rss', '2100-01-01')`;
  await upsertMaterial({ sourceId: fromFeed, url: shared, title: "Shared (from the feed)", via: "fetch" });
  const get = recorded({ [a.url]: issueHtml([[shared, "The shared story"]]), [b.url]: issueHtml([[`${shared}?utm_source=other`, "The shared story, again"]]) });
  const result = await splitNewsletters({ get, queue: false });
  assert.equal(result.issues, 2);
  assert.equal(result.created, 0, "no new article: the feed had it");
  assert.deepEqual(await articlesOf(shared), [fromFeed]);
  assert.equal(await mentions(shared), 2);
});

test("an issue that has been split is not split again; one that could not be read is tried again", async () => {
  const source = await newsletter("Letters retry");
  const issue = await issueOf(source, "r");
  const story = `https://blog.official.example/retry-${tag()}`;
  const first = await splitNewsletters({ get: recorded({}), queue: false });
  assert.equal(first.issues, 0);
  assert.deepEqual(first.failed.map(f => f.url), [issue.url]);
  assert.equal(first.failed[0]!.reason, "HTTP 404");

  const second = await splitNewsletters({ get: recorded({ [issue.url]: issueHtml([[story, "A story that is back"]]) }), queue: false });
  assert.equal(second.issues, 1);
  const third = await splitNewsletters({ get: recorded({ [issue.url]: issueHtml([[story, "A story that is back"]]) }), queue: false });
  assert.equal(third.issues, 0);
  assert.deepEqual(third.failed, []);
});

test("which links of an issue's Markdown are stories", () => {
  const md = [
    "[OpenAI ships a new model](https://openai.com/index/new-model?utm_source=tldrai)",
    "[Same story, other spelling](https://www.openai.com/index/new-model)",
    "[Sponsored: try our tool](https://ads.example.com/x)",
    "[Follow us](https://twitter.com/tldrnewsletter)",
    "[Front page only](https://some.example/)",
    "[The newsletter's own page](https://letters.example.org/archive/2026-11-01)",
    "![A picture](https://img.example/a.png)",
    "[Preferences](https://other.example/preferences)",
    "[**image**](https://other.example/gallery/1)",
    "[**Hugging Face**](https://huggingface.co/acme/model-1)",
    "[unsloth.ai/docs/models/muse](https://unsloth.ai/docs/models/muse)",
    "[A long enough title for a picture](https://i.redd.it/abc123.png)",
    "[A clip of the launch event](https://other.example/media/launch.mp4)",
  ].join("\n\n");
  assert.deepEqual(storyLinks(md, "https://letters.example.org/issue-1").map(l => l.title), ["OpenAI ships a new model"]);
  assert.equal(storyLinks(Array.from({ length: 50 }, (_, i) => `[Story number ${i} here](https://s${i}.example/path/${i})`).join("\n"), "https://letters.example.org/i").length, 30);
});
