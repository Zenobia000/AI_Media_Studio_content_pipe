import assert from "node:assert/strict";
import { test } from "node:test";
import { after } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { pollWatches, summarize } from "../watch.ts";

after(async () => {
  await stopBoss();
  await closeDb();
});

test("the summary keeps the added and removed lines, short", () => {
  assert.equal(summarize("--- a\n+++ b\n unchanged\n+ Pro plan $20\n- Pro plan $10"), "+ Pro plan $20\n- Pro plan $10");
  assert.equal(summarize("Pricing\n\n(changed) Pro plan: $10 per month\n(into) Pro plan: $20 per month\n(into) \n(into) New: Team plan"), "(changed) Pro plan: $10 per month\n(into) Pro plan: $20 per month\n(into) New: Team plan");
  assert.ok(summarize("+ x".repeat(1000)).endsWith("…"));
});

test("a service that is down or refuses gives an error and nothing else breaks", async () => {
  const down = await pollWatches(async () => { throw new Error("ECONNREFUSED"); });
  assert.match(down.error!, /連不上/);
  const refused = await pollWatches(async () => ({ status: 403, text: "" }));
  assert.match(refused.error!, /403/);
});

test("pages with no recent change are skipped before anything else is asked", async () => {
  const asked: string[] = [];
  const now = new Date("2026-11-02T00:00:00Z");
  const r = await pollWatches(async (p) => {
    asked.push(p);
    return { status: 200, text: JSON.stringify({ a: { url: "https://x.example/", last_changed: 0 }, b: { url: "https://y.example/", last_changed: Math.floor(now.getTime() / 1000) - 3 * 86400 } }) };
  }, now);
  assert.deepEqual(r, { added: 0, skipped: 2 });
  assert.deepEqual(asked, ["api/v1/watch"]);
});

test("a change is brought in once: polling again does not repeat its note", async () => {
  const now = new Date();
  const changed = Math.floor(now.getTime() / 1000) - 60;
  const page = `https://watch-${Date.now().toString(36)}.example/pricing`;
  const get = async (p: string) => {
    if (p === "api/v1/watch") return { status: 200, text: JSON.stringify({ u1: { url: page, title: "Acme Pricing", last_changed: changed } }) };
    if (p.endsWith("/history")) return { status: 200, text: JSON.stringify({ "1": "a", "2": "b" }) };
    return { status: 200, text: "(changed) Pro: $10\n(into) Pro: $20" };
  };
  assert.deepEqual(await pollWatches(get, now), { added: 1, skipped: 0 });
  assert.deepEqual(await pollWatches(get, now), { added: 0, skipped: 1 });
  const [row] = await sql<{ notes: string }[]>`SELECT m.notes FROM manual_candidates m JOIN articles a ON a.id = m.article_id WHERE a.title = 'Acme Pricing 有更新' AND m.notes LIKE ${`%${page}%`}`;
  assert.equal(row!.notes.match(/\[變動監測\]/g)!.length, 1);
  assert.match(row!.notes, /\(into\) Pro: \$20/);
});
