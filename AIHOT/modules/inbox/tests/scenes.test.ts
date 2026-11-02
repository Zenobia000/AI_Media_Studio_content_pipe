import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb } from "@aihot/backend/db";
import { inventory, type Asset } from "../assets.ts";
import { addSource } from "../manual.ts";
import { rangesFromCuts, type SceneDetector } from "../scenes.ts";
import { writeInbox } from "../server.ts";
import { openTopic } from "../topics.ts";

const hasFfmpeg = (() => {
  try {
    execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();

let home: string;
let fixtures: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-scenes-"));
  process.env.NEWSROOM_HOME = home;
  fixtures = path.join(home, "_fixtures");
  await mkdir(fixtures, { recursive: true });
  if (!hasFfmpeg) return;
  const ff = (name: string, ...args: string[]) => execFileSync("ffmpeg", ["-v", "error", "-y", ...args, path.join(fixtures, name)], { stdio: "ignore" });
  // Three scenes of one second each: red, blue, green.
  ff("three.mp4", "-filter_complex", "color=c=red:s=320x240:r=25:d=1[a];color=c=blue:s=320x240:r=25:d=1[b];color=c=green:s=320x240:r=25:d=1[c];[a][b][c]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p");
  ff("one.mp4", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=2", "-pix_fmt", "yuv420p");
  ff("anim.gif", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=5");
  ff("cover.png", "-f", "lavfi", "-i", "color=c=blue:size=640x360", "-frames:v", "1");
  ff("voice.wav", "-f", "lavfi", "-i", "sine=frequency=330:duration=2");
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

async function topic(files: Array<[from: string, to: string]>) {
  const added = await addSource(`https://example.com/scenes-${tag()}`, { title: `場景 ${tag()}`, queue: false });
  await writeInbox();
  const t = await openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
  for (const [from, to] of files) {
    const dest = path.join(home, "topics", t.topicId, to);
    await mkdir(path.dirname(dest), { recursive: true });
    await copyFile(path.join(fixtures, from), dest);
  }
  return t;
}
const near = (ranges: Array<[number, number]> | null, expected: Array<[number, number]>, slack = 120) =>
  !!ranges && ranges.length === expected.length && ranges.every(([s, e], i) => Math.abs(s - expected[i]![0]) <= slack && Math.abs(e - expected[i]![1]) <= slack);

test("a video of three scenes gives three ranges", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["three.mp4", "media/raw/three.mp4"]]);
  const stock = await inventory(t.topicId);
  const ranges = stock.assets[0]!.usable_ranges_ms;
  assert.ok(near(ranges, [[0, 1000], [1000, 2000], [2000, 3000]]), JSON.stringify(ranges));
  assert.deepEqual(stock.warnings, []);
  const json = JSON.parse(await readFile(path.join(home, "topics", t.topicId, "assets.json"), "utf8")) as { assets: Asset[] };
  assert.equal(json.assets[0]!.usable_ranges_ms!.length, 3);
});

test("a video with no cut is one range", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["one.mp4", "media/raw/one.mp4"]]);
  const a = (await inventory(t.topicId)).assets[0]!;
  assert.deepEqual(a.usable_ranges_ms, [[0, a.duration_ms!]]);
});

test("when the detection fails the whole video is usable and a warning says so", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["three.mp4", "media/raw/three.mp4"]]);
  const broken: SceneDetector = async () => {
    throw new Error("ffmpeg 失敗：boom");
  };
  const stock = await inventory(t.topicId, { detect: broken });
  const a = stock.assets[0]!;
  assert.deepEqual(a.usable_ranges_ms, [[0, a.duration_ms!]]);
  assert.equal(stock.warnings.length, 1);
  assert.match(stock.warnings[0]!, /場景偵測失敗，整段視為可用：media\/raw\/three\.mp4/);
});

test("a picture, a GIF and a recording are not looked at for scenes", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["cover.png", "media/raw/cover.png"], ["anim.gif", "media/raw/anim.gif"], ["voice.wav", "media/raw/voice.wav"]]);
  let calls = 0;
  const spy: SceneDetector = async () => {
    calls += 1;
    return [];
  };
  const stock = await inventory(t.topicId, { detect: spy });
  assert.equal(calls, 0);
  const byType = Object.fromEntries(stock.assets.map(a => [a.type, a.usable_ranges_ms]));
  assert.equal(byType.image, null);
  assert.equal(byType.gif, null);
  assert.deepEqual(byType.audio, [[0, stock.assets.find(a => a.type === "audio")!.duration_ms!]]);
});

test("ranges that were written, and perhaps edited, are kept and the video is not looked at again", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["three.mp4", "media/raw/three.mp4"]]);
  await inventory(t.topicId);
  const file = path.join(home, "topics", t.topicId, "assets.json");
  const json = JSON.parse(await readFile(file, "utf8")) as { assets: Asset[] };
  json.assets[0]!.usable_ranges_ms = [[500, 1500]];
  await writeFile(file, JSON.stringify(json), "utf8");
  let calls = 0;
  const stock = await inventory(t.topicId, { detect: async () => { calls += 1; return []; } });
  assert.equal(calls, 0);
  assert.deepEqual(stock.assets[0]!.usable_ranges_ms, [[500, 1500]]);
});

test("from cuts to ranges: a flash is not a scene, and the ranges cover the whole video", () => {
  assert.deepEqual(rangesFromCuts([1000, 2000], 3000), [[0, 1000], [1000, 2000], [2000, 3000]]);
  assert.deepEqual(rangesFromCuts([], 2000), [[0, 2000]]);
  assert.deepEqual(rangesFromCuts([1000, 1200, 2000], 3000), [[0, 1000], [1000, 2000], [2000, 3000]], "a cut 200 ms after the last is dropped");
  assert.deepEqual(rangesFromCuts([100, 2900], 3000), [[0, 3000]], "cuts at the very start or end are dropped");
  assert.deepEqual(rangesFromCuts([2000, 1000], 3000).map(r => r[0]), [0, 1000, 2000], "cuts out of order");
});
