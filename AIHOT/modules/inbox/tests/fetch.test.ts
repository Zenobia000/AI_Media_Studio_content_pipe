import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { fetchTopic, officialLinks } from "../fetch.ts";
import { htmlToMarkdown } from "../html-markdown.ts";
import { extractSource, ExtractFailure, type Get, type Page } from "../source-extract.ts";
import { openTopic, sourceIdOf } from "../topics.ts";
import { writeInbox } from "../server.ts";

let home: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-fetch-"));
  process.env.NEWSROOM_HOME = home;
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

const paragraph = "這是一段用來測試原文擷取的正文，它必須夠長，才不會被當成導覽列或廣告而被忽略。".repeat(4);
const article = (title: string, extra = "") => `<!doctype html><html lang="zh-TW"><head><title>${title}</title><meta property="og:site_name" content="Test Media"><meta name="author" content="王小明"></head>
<body><nav><a href="/">首頁</a></nav><article><h1>${title}</h1><p>${paragraph}</p><p>${paragraph}</p>${extra}</article><footer>版權所有</footer></body></html>`;
const html = (text: string): Page => ({ status: 200, url: "", type: "text/html; charset=utf-8", text });

/** Recorded responses by address; anything else is a 404. */
const recorded = (pages: Record<string, Page | string>): Get => async (url) => {
  const hit = pages[url];
  if (hit === undefined) return { status: 404, url, type: "text/plain", text: "" };
  return typeof hit === "string" ? { ...html(hit), url } : { ...hit, url: hit.url || url };
};

async function topicFor(url: string) {
  const added = await addSource(url, { title: `題目 ${tag()}`, queue: false });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["threads"] });
}
const sourcesOnDisk = async (topicId: string) => (await readdir(path.join(home, "topics", topicId, "sources")).catch(() => [])).sort();

test("a news page becomes a sources file with the IF-004 front-matter and Markdown text", async () => {
  const url = `https://media.example/news-${tag()}`;
  const opened = await topicFor(url);
  const result = await fetchTopic(opened.topicId, { get: recorded({ [url]: article("Roadster 發表") }) });
  assert.equal(result.blocked, false);
  assert.equal(result.fetched.length, 1);
  assert.equal(result.fetched[0]!.method, "readability");
  const text = await readFile(path.join(home, "topics", opened.topicId, "sources", `${sourceIdOf(url)}.md`), "utf8");
  for (const key of ["schema_version: 1", `source_id: ${sourceIdOf(url)}`, `url: ${url}`, "kind: article", 'publisher: "Test Media"', 'author: "王小明"', "fetch_method: readability", "language: zh", /word_count: \d+/, /fetched_at: \d{4}-\d{2}-\d{2}T[\d:]+\+08:00/]) {
    assert.match(text, typeof key === "string" ? new RegExp(key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) : key);
  }
  assert.match(text, /這是一段用來測試原文擷取的正文/);
});

test("fetching again keeps what was read; --force reads it again", async () => {
  const url = `https://media.example/again-${tag()}`;
  const opened = await topicFor(url);
  const get = recorded({ [url]: article("再來一次") });
  await fetchTopic(opened.topicId, { get });
  const again = await fetchTopic(opened.topicId, { get });
  assert.deepEqual(again.fetched, []);
  assert.deepEqual(again.kept, [sourceIdOf(url)]);
  assert.equal((await fetchTopic(opened.topicId, { get, force: true })).fetched.length, 1);
});

test("a report linking to an official blog brings the official source into the topic", async () => {
  const siteId = `test-official-${tag()}`;
  await sql`INSERT INTO sources (id, name, kind, config, tier, next_fetch_at) VALUES (${siteId}, 'Official', 'rss', ${sql.json({ feedUrl: "https://blog.officialco.example/feed.xml" } as never)}, 'T1', '2100-01-01')`;
  const url = `https://media.example/report-${tag()}`;
  const official = "https://www.officialco.example/blog/launch";
  const opened = await topicFor(url);
  const result = await fetchTopic(opened.topicId, {
    get: recorded({
      [url]: article("媒體報導", `<p>根據<a href="${official}">官方部落格</a>的說法，新產品今天發表，細節如下。${paragraph}</p>`),
      [official]: article("官方：新產品發表"),
    }),
  });
  assert.equal(result.fetched.length, 2);
  assert.deepEqual(result.fetched.filter(f => f.official).map(f => f.url), [official]);
  assert.deepEqual(await sourcesOnDisk(opened.topicId), [`${sourceIdOf(official)}.md`, `${sourceIdOf(url)}.md`].sort());
  const [row] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM topic_sources WHERE topic_id = ${opened.topicId}`;
  assert.equal(row!.n, 2);
  assert.match(await readFile(path.join(home, "topics", opened.topicId, "topic.md"), "utf8"), new RegExp(sourceIdOf(official)));
});

test("when nothing can be read the topic is blocked and the notes say how to paste the text", async () => {
  const url = `https://media.example/blocked-${tag()}`;
  const opened = await topicFor(url);
  const result = await fetchTopic(opened.topicId, { get: recorded({ [url]: { status: 403, url, type: "text/html", text: "forbidden" } }) });
  assert.equal(result.blocked, true);
  assert.equal(result.failed[0]!.reason, "HTTP 403");
  assert.deepEqual(await sourcesOnDisk(opened.topicId), []);
  assert.match(await readFile(path.join(home, "topics", opened.topicId, "notes.md"), "utf8"), /sources\/manual-1\.md/);
});

test("a page with under 200 characters of text is not a source", async () => {
  const url = `https://media.example/short-${tag()}`;
  await assert.rejects(extractSource(url, { get: recorded({ [url]: "<html><body><article><p>太短了。</p></article></body></html>" }) }), ExtractFailure);
});

test("GitHub, arXiv and YouTube are read through their own interfaces", async () => {
  const readme = "# Ollama\n\nRun large language models locally. ".repeat(12);
  const github = await extractSource("https://github.com/ollama/ollama", {
    get: recorded({
      "https://api.github.com/repos/ollama/ollama/readme": { status: 200, url: "", type: "text/plain", text: readme },
      "https://api.github.com/repos/ollama/ollama/releases/latest": { status: 200, url: "", type: "application/json", text: JSON.stringify({ tag_name: "v0.5.0", name: "Big release", body: "- faster\n- smaller", published_at: "2026-10-30T01:00:00Z" }) },
    }),
  });
  assert.equal(github.method, "github-api");
  assert.equal(github.kind, "release");
  assert.equal(github.publishedAt, "2026-10-30T01:00:00Z");
  assert.match(github.markdown, /Release v0\.5\.0/);

  const abstract = "We study how agents use tools. ".repeat(10);
  const feed = `<feed><entry><title>Agents Using Tools</title><summary>${abstract}</summary><author><name>A. Author</name></author><published>2026-10-28T20:00:00Z</published></entry></feed>`;
  const paper = await extractSource("https://arxiv.org/pdf/2410.12345v2", { get: recorded({ "https://export.arxiv.org/api/query?id_list=2410.12345": { status: 200, url: "", type: "application/atom+xml", text: feed } }) });
  assert.equal(paper.kind, "paper");
  assert.equal(paper.title, "Agents Using Tools");
  assert.equal(paper.author, "A. Author");

  const watch = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  const video = await extractSource("https://youtu.be/dQw4w9WgXcQ", {
    get: recorded({
      [`https://www.youtube.com/oembed?url=${encodeURIComponent(watch)}&format=json`]: { status: 200, url: "", type: "application/json", text: JSON.stringify({ title: "Demo day", author_name: "Some Channel" }) },
      [watch]: `<html><head><meta property="og:description" content="${"The full walkthrough of the launch. ".repeat(10)}"></head></html>`,
    }),
  });
  assert.equal(video.kind, "video");
  assert.equal(video.author, "Some Channel");

  await assert.rejects(extractSource("https://x.com/openai/status/1234567890", { get: recorded({}) }), ExtractFailure);
});

test("official links: repositories, papers and pages of the engine's first-hand sites, never the report's own site", () => {
  const sites = new Set(["officialco.example"]);
  const links = ["https://github.com/acme/tool", "https://arxiv.org/abs/2410.12345v2", "https://www.officialco.example/blog/launch", "https://www.officialco.example/", "https://media.example/other", "https://twitter.com/x"];
  assert.deepEqual(officialLinks(links, "https://media.example/report", sites), ["https://github.com/acme/tool", "https://arxiv.org/abs/2410.12345v2", "https://www.officialco.example/blog/launch"]);
  assert.deepEqual(officialLinks(links, "https://blog.officialco.example/post", sites), []);
});

test("HTML becomes Markdown with headings, links, lists and images kept", () => {
  const md = htmlToMarkdown('<h2>標題</h2><p>看<a href="/a">這裡</a>與<strong>重點</strong></p><ul><li>一</li><li>二</li></ul><img src="/p.png" alt="圖">', "https://example.com/post");
  assert.equal(md, "## 標題\n\n看[這裡](https://example.com/a)與**重點**\n\n- 一\n- 二\n\n![圖](https://example.com/p.png)");
});
