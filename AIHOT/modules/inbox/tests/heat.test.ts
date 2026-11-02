import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { backtestHeat, describeBreakdown, loadHeatConfig, loadHeatInputs, programFor, scoreHeat, type HeatConfig, type HeatInput } from "../heat.ts";
import { writeInbox } from "../server.ts";
import { store } from "../signals.ts";

let config: HeatConfig;
let dir: string;
before(async () => {
  config = await loadHeatConfig();
  dir = await mkdtemp(path.join(tmpdir(), "inbox-heat-"));
  process.env.INBOX_DIR = dir;
});
after(async () => {
  delete process.env.INBOX_DIR;
  await rm(dir, { recursive: true, force: true });
  await closeDb();
});

const withSignals = (over: Partial<HeatConfig["signals"]>): HeatConfig => ({ ...config, signals: { ...config.signals, ...over } as HeatConfig["signals"] });
const hot: HeatInput = { resonanceSources: 1, hnPointsPerHour: 40, starsPerHour: 10, newsletterMentions: 2, bestTier: "T1", identityKeys: ["url:https://example.com/a"] };

test("the same input gives the same score and the same breakdown", () => {
  const first = scoreHeat(hot, config);
  const second = scoreHeat(structuredClone(hot), structuredClone(config));
  assert.deepEqual(second, first);
  const sum = first.breakdown.reduce((s, b) => s + b.points, 0);
  assert.ok(Math.abs(sum - first.score) <= 1, `the points ${sum} add up to the score ${first.score}`);
  assert.ok(first.score >= 0 && first.score <= 100);
});

test("a signal that is switched off has weight 0, is left out of the score, and raises no error", () => {
  const input: HeatInput = { ...hot, xVelocity: 3, localGap: true };
  const off = scoreHeat(input, config);
  for (const name of ["x_velocity", "local_gap"] as const) {
    const b = off.breakdown.find(x => x.signal === name)!;
    assert.deepEqual([b.enabled, b.weight, b.points], [false, 0, 0], name);
  }
  const on = scoreHeat(input, withSignals({ x_velocity: { ...config.signals.x_velocity, enabled: true }, local_gap: { ...config.signals.local_gap, enabled: true } }));
  assert.notEqual(on.score, off.score, "turning them on changes the score");
  // Switching one off that the input has no data for changes nothing about the others' points.
  const noHn = scoreHeat({ ...hot, hnPointsPerHour: null }, withSignals({ hn_velocity: { ...config.signals.hn_velocity, enabled: false } }));
  assert.equal(noHn.breakdown.find(b => b.signal === "hn_velocity")!.weight, 0);
  // A signal that is on but unknown for this candidate counts as nothing.
  assert.equal(scoreHeat({ resonanceSources: 0 }, config).score, 0);
});

test("S, A and B: three sources in six hours or a launch that just ended is S, two sources is A, the rest is B", () => {
  const s3 = scoreHeat({ resonanceSources: 3 }, config);
  assert.equal(s3.tier, "S");
  assert.match(s3.reasons.join(), /6 小時內 3 個來源共振/);
  assert.equal(scoreHeat({ resonanceSources: 1, eventEnded: true }, config).tier, "S");
  assert.match(scoreHeat({ resonanceSources: 1, eventEnded: true }, config).reasons[0]!, /發表會/);
  assert.equal(scoreHeat({ resonanceSources: 2 }, config).tier, "A");
  const b = scoreHeat({ resonanceSources: 1, bestTier: "T1" }, config);
  assert.equal(b.tier, "B");
  assert.match(b.reasons[0]!, /沒有達到 A 級/);
  const byScore = scoreHeat({ resonanceSources: 1, hnPointsPerHour: 200, starsPerHour: 100, newsletterMentions: 5, bestTier: "T1" }, config);
  assert.ok(byScore.score >= config.tiers.A.minScore, `score ${byScore.score}`);
  assert.notEqual(byScore.tier, "B");
});

test("each tier suggests formats, and the program follows what the thing is", () => {
  assert.deepEqual(scoreHeat({ resonanceSources: 3 }, config).formats, ["threads", "carousel", "reel"]);
  assert.deepEqual(scoreHeat({ resonanceSources: 2 }, config).formats, ["carousel", "reel"]);
  assert.deepEqual(scoreHeat({ resonanceSources: 1 }, config).formats, ["threads"]);
  assert.equal(programFor(["arxiv:2410.12345"]), "ai-engineering");
  assert.equal(programFor(["github:ollama/ollama"]), "tool-review");
  assert.equal(programFor(["url:https://huggingface.co/acme/model"]), "tool-review");
  assert.equal(programFor(["url:https://news.example.com/a"]), "ai-today");
  assert.equal(programFor([]), "ai-today");
});

test("the breakdown is written as people read it", () => {
  const text = describeBreakdown(scoreHeat(hot, config));
  assert.match(text, /^\d+ 分（共振 .*HN 增速 .*）$/);
  assert.doesNotMatch(text, /X 互動|在地缺口/, "switched off signals are not listed");
});

test("the signals of the database become the inputs: sources in the window, the rise per hour, mentions and tiers", async () => {
  const now = new Date("2026-11-02T12:00:00Z");
  const t = tag();
  const src = (n: string, tier: string) => sql`INSERT INTO sources (id, name, kind, tier, next_fetch_at) VALUES (${`heat-${n}-${t}`}, ${n}, 'rss', ${tier}, '2100-01-01')`;
  await src("a", "T1");
  await src("b", "T2");
  await src("c", "T2");
  const url = (n: string) => `https://news.example.com/${n}-${t}`;
  const make = async (sourceId: string, n: string) => (await upsertMaterial({ sourceId: `heat-${sourceId}-${t}`, url: url(n), title: `Story ${n}`, via: "fetch" })).articleId;
  const [a, b, c] = [await make("a", "1"), await make("b", "2"), await make("c", "3")];
  const [story] = await sql<{ id: number }[]>`INSERT INTO stories (public_id, title) VALUES (gen_random_uuid(), ${`Heat story ${t}`}) RETURNING id`;
  const hoursAgo = (h: number) => new Date(now.getTime() - h * 3600_000);
  for (const [id, source, h] of [[a, "a", 2], [b, "b", 5], [c, "c", 30]] as const) {
    await sql`INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (${story!.id}, ${id}, ${`source:${source}`}, ${`heat-${source}-${t}`}, 'editorial', ${hoursAgo(h)})`;
  }
  const key = (await sql<{ identity_key: string }[]>`SELECT identity_key FROM articles WHERE id = ${a}`)[0]!.identity_key;
  await store([{ identityKey: key, metric: "hn_points", value: 100 }, { identityKey: key, metric: "newsletter_mentions", value: 2 }], hoursAgo(3));
  await store([{ identityKey: key, metric: "hn_points", value: 160 }], hoursAgo(1));

  const { byStory } = await loadHeatInputs({ storyIds: [story!.id] }, config, now);
  const input = byStory.get(story!.id)!;
  assert.equal(input.resonanceSources, 2, "the third report is 30 hours old, outside the 6-hour window");
  assert.equal(input.hnPointsPerHour, 30);
  assert.equal(input.newsletterMentions, 2);
  assert.equal(input.bestTier, "T1");
  assert.equal(scoreHeat(input, config).tier, "A");

  // The same data read again at the same time gives the same grade.
  assert.deepEqual(scoreHeat((await loadHeatInputs({ storyIds: [story!.id] }, config, now)).byStory.get(story!.id)!, config), scoreHeat(input, config));
});

test("the inbox lists the hottest first, with the score, why, and the suggested program and formats", async () => {
  const t = tag();
  const cool = await addSource(`https://github.com/acme/cool-${t}`, { title: `Cool repo ${t}`, queue: false });
  const hotOne = await addSource(`https://arxiv.org/abs/2410.${String(Math.floor(Math.random() * 90000) + 10000)}`, { title: `Hot paper ${t}`, queue: false });
  await sql`INSERT INTO signal_points (identity_key, metric, value, observed_at) SELECT identity_key, 'hn_points', 20, now() - interval '3 hours' FROM articles WHERE id = ${hotOne.articleId}`;
  await sql`INSERT INTO signal_points (identity_key, metric, value, observed_at) SELECT identity_key, 'hn_points', 140, now() - interval '1 hour' FROM articles WHERE id = ${hotOne.articleId}`;
  assert.ok(await writeInbox());
  const text = await readFile(path.join(dir, (await readdir(dir)).sort().at(-1)!), "utf8");
  assert.ok(text.indexOf(`Hot paper ${t}`) < text.indexOf(`Cool repo ${t}`), "the one with the rising signal comes first");
  assert.match(text, new RegExp(`Hot paper ${t}[^\\n]*\\n[^\\n]*\\n  - 熱度：\\d+ 分（[^\\n]*HN 增速[^\\n]*）｜`));
  assert.match(text, /建議：ai-engineering｜/);
  assert.match(text, /建議：tool-review｜/);
  assert.ok(text.includes(`Cool repo ${t}`) && cool.articleId, "both are listed");
});

test("the backtest says how many of the stories a person marked S are graded S or A, from the data of the time they name", async () => {
  const t = tag();
  const now = new Date("2026-11-02T12:00:00Z");
  const hoursAgo = (h: number) => new Date(now.getTime() - h * 3600_000);
  await sql`INSERT INTO sources (id, name, kind, tier, next_fetch_at) VALUES (${`bt-a-${t}`}, 'A', 'rss', 'T1', '2100-01-01'), (${`bt-b-${t}`}, 'B', 'rss', 'T2', '2100-01-01'), (${`bt-c-${t}`}, 'C', 'rss', 'T2', '2100-01-01')`;
  const story = async (title: string, reports: Array<[string, number]>) => {
    const [s] = await sql<{ id: number; public_id: string }[]>`INSERT INTO stories (public_id, title) VALUES (gen_random_uuid(), ${`${title} ${t}`}) RETURNING id, public_id::text`;
    for (const [i, [source, h]] of reports.entries()) {
      const { articleId } = await upsertMaterial({ sourceId: `bt-${source}-${t}`, url: `https://news.example.com/${title}-${i}-${t}`, title: `${title} ${i}`, via: "fetch" });
      await sql`INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (${s!.id}, ${articleId}, ${`source:${source}`}, ${`bt-${source}-${t}`}, 'editorial', ${hoursAgo(h)})`;
    }
    return s!.public_id;
  };
  const big = await story("big", [["a", 1], ["b", 2], ["c", 3]]);
  const medium = await story("medium", [["a", 1], ["b", 2]]);
  const quiet = await story("quiet", [["a", 1]]);
  const r = await backtestHeat([
    { story: big, label: "S" }, { story: medium, label: "S" }, { story: quiet, label: "S" }, { story: quiet, label: "B" },
    { story: "00000000-0000-0000-0000-000000000000", label: "S" },
    { story: big, label: "S", asOf: hoursAgo(20).toISOString() },
  ], config, now);
  assert.deepEqual(r.rows.map(x => x.tier), ["S", "A", "B", "B", null, "B"]);
  assert.equal(r.rows[4]!.found, false);
  assert.deepEqual([r.sHits, r.sTotal], [2, 4], "the story that cannot be found is not counted; the one graded as of 20 hours earlier had no reports yet");
  assert.equal(r.sRate, 0.5);
  assert.equal((await backtestHeat([{ story: quiet, label: "B" }], config, now)).sRate, null);
});
