import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { closeDb, sql } from "@aihot/backend/db";
import { loadHeatConfig, loadHeatInputs, scoreHeat } from "../heat.ts";
import { engagementOf, median, velocityOf, xVelocities } from "../x-velocity.ts";

after(closeDb);

test("engagement is likes plus twice the reposts; no counts, no engagement", () => {
  assert.equal(engagementOf({ favorite: 100, retweet: 10, views: 5000 }), 120);
  assert.equal(engagementOf({ favorite: 7, retweet: null }), 7);
  assert.equal(engagementOf({ views: 5 }), null);
  assert.equal(engagementOf(null), null);
});

test("velocity is against the account's median, and needs enough history", () => {
  assert.equal(median([1, 5, 3]), 3);
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(velocityOf(300, [100, 100, 100, 100, 100]), 3);
  assert.equal(velocityOf(300, [100, 100, 100]), null);
  assert.equal(velocityOf(null, [1, 1, 1, 1, 1]), null);
  assert.equal(velocityOf(10, [0, 0, 0, 0, 0]), 10, "a silent account does not divide by zero");
});

test("a post far above its account's usual is graded from the stored counts, and feeds the heat input", async () => {
  const t = tag();
  const source = `x-vel-${t}`;
  await sql`INSERT INTO sources (id, name, kind, tier, next_fetch_at) VALUES (${source}, 'X', 'rss', 'T1', '2100-01-01')`;
  const handle = `acct_${t}`;
  const post = async (n: number, favorite: number, retweet: number) =>
    (await upsertMaterial({ sourceId: source, url: `https://x.com/${handle}/status/${n}`, title: `post ${n}`, author: handle, identityKey: `x:${t}${n}`, via: "fetch", raw: { favorite, retweet, views: null } })).articleId;
  for (let n = 1; n <= 6; n++) await post(n, 100, 0);
  const hotId = await post(7, 250, 25);
  const keys = [`x:${t}7`, `x:${t}1`, "https://example.com/not-x"];
  const v = await xVelocities(keys);
  assert.equal(v.get(`x:${t}7`), 3);
  assert.equal(v.has("https://example.com/not-x"), false);

  const [story] = await sql<{ id: number }[]>`INSERT INTO stories (public_id, title) VALUES (gen_random_uuid(), ${`X story ${t}`}) RETURNING id`;
  await sql`INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (${story!.id}, ${hotId}, ${`source:${source}`}, ${source}, 'editorial', now())`;
  const config = await loadHeatConfig();
  const input = (await loadHeatInputs({ storyIds: [story!.id] }, config, new Date())).byStory.get(story!.id)!;
  assert.equal(input.xVelocity, 3);

  const off = scoreHeat(input, config);
  assert.equal(off.breakdown.find(b => b.signal === "x_velocity")!.points, 0, "switched off in the shipped config");
  const on = scoreHeat(input, { ...config, signals: { ...config.signals, x_velocity: { ...config.signals.x_velocity, enabled: true } } });
  assert.ok(on.breakdown.find(b => b.signal === "x_velocity")!.points > 0);
});
