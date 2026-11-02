import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { identityKeyForUrl } from "@aihot/backend/lib/url";
import { addSource } from "../manual.ts";
import { writeInbox } from "../server.ts";
import { collectSignals, describeSignals, growthPerHour } from "../signals.ts";
import type { Get, Page } from "../source-extract.ts";

const ok = (body: unknown): Page => ({ status: 200, url: "", type: "application/json", text: JSON.stringify(body) });
const HN_FRONT = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60";
const HN_AI = "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&numericFilters=points%3E20&hitsPerPage=60";

/** Recorded responses by address; the rest is a 404. `hn` sets the front page's hits. */
function recorded(hn: unknown[], extra: Record<string, Page> = {}): Get {
  const pages: Record<string, Page> = {
    [HN_FRONT]: ok({ hits: hn }),
    [HN_AI]: ok({ hits: [] }),
    "https://huggingface.co/api/daily_papers": ok([{ paper: { id: "2410.12345", upvotes: 87 } }]),
    "https://huggingface.co/api/models?sort=trendingScore&limit=30": ok([{ id: "acme/model-1", likes: 321, downloads: 9000 }]),
    "https://api.github.com/repos/acme/tool": ok({ stargazers_count: 2345 }),
    ...extra,
  };
  return async (url) => pages[url] ?? { status: 404, url, type: "text/plain", text: "" };
}

let dir: string;
before(async () => {
  dir = await mkdtemp(path.join(tmpdir(), "inbox-signals-"));
  process.env.INBOX_DIR = dir;
  await sql`INSERT INTO sources (id, name, kind, config, next_fetch_at) VALUES (${`gh-test-${tag()}`}, 'acme/tool', 'rss', ${sql.json({ feedUrl: "https://github.com/acme/tool/releases.atom" } as never)}, '2100-01-01')`;
});
after(async () => {
  delete process.env.INBOX_DIR;
  await rm(dir, { recursive: true, force: true });
  await closeDb();
});

const value = async (key: string, metric: string) =>
  (await sql<{ value: number }[]>`SELECT value FROM signal_points WHERE identity_key = ${key} AND metric = ${metric} ORDER BY observed_at DESC LIMIT 1`)[0]?.value;
const points = async (key: string, metric: string) =>
  (await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM signal_points WHERE identity_key = ${key} AND metric = ${metric}`)[0]!.n;

test("points and comments, paper upvotes, model likes and repository stars are recorded against the address they are about", async () => {
  const story = `https://news.example.com/story-${tag()}`;
  const report = await collectSignals({
    get: recorded([{ objectID: "1", url: story, points: 412, num_comments: 120 }, { objectID: "2", points: 50, num_comments: 9 }]),
    now: new Date("2026-11-02T10:20:00Z"),
  });
  assert.deepEqual(report, { hn: 2, hf: 3, github: 1 });
  const key = identityKeyForUrl(story)!;
  assert.equal(await value(key, "hn_points"), 412);
  assert.equal(await value(key, "hn_comments"), 120);
  assert.equal(await value("arxiv:2410.12345", "hf_upvotes"), 87);
  assert.equal(await value(identityKeyForUrl("https://huggingface.co/acme/model-1")!, "hf_likes"), 321);
  assert.equal(await value("github:acme/tool", "gh_stars"), 2345);
});

test("the same hour again updates the value; a later hour adds a point, and the growth per hour follows", async () => {
  const story = `https://news.example.com/series-${tag()}`;
  const key = identityKeyForUrl(story)!;
  const at = (h: number, m = 0) => new Date(Date.UTC(2026, 10, 2, h, m));
  const hit = (points: number) => [{ objectID: "9", url: story, points, num_comments: 1 }];
  await collectSignals({ get: recorded(hit(100)), now: at(10, 5) });
  await collectSignals({ get: recorded(hit(110)), now: at(10, 45) });
  assert.equal(await points(key, "hn_points"), 1);
  assert.equal(await value(key, "hn_points"), 110);
  assert.equal(await growthPerHour(key, "hn_points"), null);
  await collectSignals({ get: recorded(hit(170)), now: at(12, 10) });
  assert.equal(await points(key, "hn_points"), 2);
  assert.equal(await growthPerHour(key, "hn_points"), 30);
});

test("a source that does not answer is reported and the others are still recorded", async () => {
  const report = await collectSignals({ get: recorded([], { [HN_FRONT]: { status: 500, url: "", type: "text/plain", text: "" } }), now: new Date("2026-11-02T14:00:00Z") });
  assert.equal(report.hn, "HTTP 500");
  assert.equal(report.hf, 3);
  assert.equal(report.github, 1);
});

test("signals are written as people read them", () => {
  assert.equal(describeSignals({ hn_points: 412, hn_comments: 120, gh_stars: 2345 }), "HN 412 分、120 則留言、GitHub ★2345");
  assert.equal(describeSignals({}), "");
});

test("a candidate shows what people are saying about its address", async () => {
  const url = `https://news.example.com/inbox-${tag()}`;
  await collectSignals({ get: recorded([{ objectID: "7", url, points: 412, num_comments: 120 }]), now: new Date() });
  await addSource(url, { title: `有討論的題目 ${tag()}`, queue: false });
  assert.ok(await writeInbox());
  const text = await readFile(path.join(dir, (await readdir(dir)).sort().at(-1)!), "utf8");
  assert.match(text, /共振 1 源｜手動、HN 412 分、120 則留言｜首次出現/);
});
