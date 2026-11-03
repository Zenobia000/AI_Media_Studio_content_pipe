import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { addSource, InvalidUrlError } from "../manual.ts";
import { writeInbox } from "../server.ts";
import type { Get } from "../source-extract.ts";

after(() => closeDb());

const offline: Get = async () => {
  throw new Error("offline");
};
const html = (body: string): Get => async (url) => ({ status: 200, url, type: "text/html; charset=utf-8", text: body });

const counts = async (url: string) => {
  const [r] = await sql<{ articles: number; candidates: number }[]>`
    SELECT count(DISTINCT a.id)::int AS articles, count(m.article_id)::int AS candidates
    FROM articles a LEFT JOIN manual_candidates m ON m.article_id = a.id WHERE a.url = ${url}`;
  return r!;
};

test("a new address gives one article and one candidate; the same address again keeps both and adds the note", async () => {
  const url = `https://example.com/manual-${tag()}`;
  const first = await addSource(url, { note: "從工具角度切入", formats: ["threads"], queue: false, get: offline });
  assert.equal(first.created, true);
  assert.equal(first.alreadyAdded, false);
  assert.match(first.inboxDate, /^\d{4}-\d{2}-\d{2}$/);

  const again = await addSource(`${url}?utm_source=x#top`, { note: "再補一句", queue: false, get: offline });
  assert.equal(again.articleId, first.articleId);
  assert.equal(again.alreadyAdded, true);
  assert.equal(again.inboxDate, first.inboxDate);
  assert.deepEqual(await counts(url), { articles: 1, candidates: 1 });

  const [row] = await sql<{ notes: string; formats: string[] }[]>`SELECT notes, formats FROM manual_candidates WHERE article_id = ${first.articleId}`;
  assert.equal(row!.notes, "從工具角度切入\n再補一句");
  assert.deepEqual(row!.formats, ["threads"]);
});

test("an article a source already brought becomes a manual candidate without a second article", async () => {
  const id = `test-manual-src-${tag()}`;
  await sql`INSERT INTO sources (id, name, kind, next_fetch_at) VALUES (${id}, 'Test feed', 'rss', '2100-01-01')`;
  const url = `https://example.com/from-feed-${tag()}`;
  const known = await upsertMaterial({ sourceId: id, url, title: "From the feed", via: "fetch" });
  const added = await addSource(url, { note: "這則要做", queue: false, get: offline });
  assert.equal(added.articleId, known.articleId);
  assert.equal(added.created, false);
  assert.deepEqual(await counts(url), { articles: 1, candidates: 1 });
});

test("a link added without a title takes the page's own title; a title given, or a page that cannot be read, is not replaced", async () => {
  const title = async (url: string) => (await sql<{ title: string }[]>`SELECT title FROM articles WHERE url = ${url}`)[0]!.title;
  const og = `https://example.com/og-${tag()}`;
  await addSource(og, { queue: false, get: html(`<html><head><title>fallback</title><meta property="og:title" content="  Model  X 發表 "></head></html>`) });
  assert.equal(await title(og), "Model X 發表");

  const plain = `https://example.com/plain-${tag()}`;
  await addSource(plain, { queue: false, get: html(`<html><head><title>Plain page</title></head></html>`) });
  assert.equal(await title(plain), "Plain page");

  const given = `https://example.com/given-${tag()}`;
  await addSource(given, { title: "我取的名字", queue: false, get: html(`<title>Other</title>`) });
  assert.equal(await title(given), "我取的名字");

  const down = `https://example.com/down-${tag()}`;
  await addSource(down, { queue: false, get: offline });
  assert.equal(await title(down), down);
});

test("an address that is not http(s) is refused", async () => {
  for (const bad of ["not a url", "ftp://example.com/a", ""]) await assert.rejects(addSource(bad, { queue: false, get: offline }), InvalidUrlError);
});

test("a hand-added link reaches today's inbox file and keeps the person's tick on the next write", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "inbox-"));
  process.env.INBOX_DIR = dir;
  try {
    const added = await addSource(`https://example.com/inbox-${tag()}`, { note: "今天要做", program: "tool-review", queue: false, get: offline });
    const written = await writeInbox();
    assert.ok(written);
    const text = await readFile(written.file, "utf8");
    assert.match(text, /## 手動新增/);
    assert.match(text, new RegExp(`\`cand_${added.articleId}\``));
    assert.match(text, /備註：今天要做/);
    await writeFile(written.file, text.replace(new RegExp(`- \\[ \\] (\\*\\*[^\\n]*\`cand_${added.articleId}\`)`), "- [x] $1"), "utf8");
    await writeInbox();
    assert.match(await readFile(written.file, "utf8"), new RegExp(`- \\[x\\] .*\`cand_${added.articleId}\``));
  } finally {
    delete process.env.INBOX_DIR;
    await rm(dir, { recursive: true, force: true });
  }
});
