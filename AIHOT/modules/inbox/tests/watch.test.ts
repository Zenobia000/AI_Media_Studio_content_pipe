import assert from "node:assert/strict";
import { test } from "node:test";
import { pollWatches, summarize } from "../watch.ts";

test("the summary keeps the added and removed lines, short", () => {
  assert.equal(summarize("--- a\n+++ b\n unchanged\n+ Pro plan $20\n- Pro plan $10"), "+ Pro plan $20\n- Pro plan $10");
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
