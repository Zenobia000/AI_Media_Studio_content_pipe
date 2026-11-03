import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { fetchLayer4, toMp4, type Recorder } from "../layer4.ts";
import { writeInbox } from "../server.ts";
import type { Get } from "../source-extract.ts";
import { openTopic } from "../topics.ts";

const hasFfmpeg = (() => {
  try {
    execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();
const skip = !hasFfmpeg && "ffmpeg is not installed";

let home: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-l4-"));
  process.env.NEWSROOM_HOME = home;
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

/** A stand-in for the browser: writes a 3-second clip from the given ffmpeg source (a test picture, or black). */
const clip = (source: string): Recorder => async (_url, out) => {
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", "-c:v", "libvpx", out], { stdio: "ignore" });
};
const picture = clip("testsrc=duration=3:size=640x360:rate=25");
const black = clip("color=c=black:duration=3:size=640x360:rate=25");
const noRobots: Get = async (url) => ({ status: 404, url, type: "text/plain", text: "" });

async function topicAt(url: string, formats = ["reel"]) {
  const added = await addSource(url, { title: `錄影 ${tag()}`, queue: false, get: async () => { throw new Error("offline"); } });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats });
}
const gaps = async (topicId: string) => sql<{ reason: string }[]>`SELECT reason FROM topic_fetch_gaps WHERE topic_id = ${topicId} AND layer = 4`;

test("a page with no video is recorded to a 1080p 30 fps H.264 file and kept as an asset", { skip }, async () => {
  const t = await topicAt(`https://example.com/demo-${tag()}`);
  const result = await fetchLayer4(t.topicId, { record: picture, get: noRobots });
  assert.equal(result.recorded.length, 1);
  const [asset] = await sql<{ fetch_layer: number; fetch_method: string; type: string; path: string }[]>`SELECT fetch_layer, fetch_method, type, path FROM topic_assets WHERE topic_id = ${t.topicId}`;
  assert.deepEqual([asset!.fetch_layer, asset!.fetch_method, asset!.type], [4, "playwright", "video"]);
  const file = path.join(home, "topics", t.topicId, asset!.path);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-print_format", "json", "-show_entries", "stream=codec_name,width,height,r_frame_rate,pix_fmt", file]).toString()).streams[0];
  assert.deepEqual([probe.codec_name, probe.width, probe.height, probe.r_frame_rate, probe.pix_fmt], ["h264", 1920, 1080, "30/1", "yuv420p"]);
  assert.equal((await gaps(t.topicId)).length, 0);
});

test("L4 does not run for a source that already has a video, or for a topic without a reel", { skip }, async () => {
  const t = await topicAt(`https://example.com/has-video-${tag()}`);
  const [src] = await sql<{ source_id: string }[]>`SELECT source_id FROM topic_sources WHERE topic_id = ${t.topicId}`;
  let calls = 0;
  const counting: Recorder = async (...args) => { calls += 1; await picture(...args); };
  const have = await fetchLayer4(t.topicId, { record: counting, get: noRobots, haveVideo: new Set([src!.source_id]) });
  assert.deepEqual([calls, have.recorded.length, have.skipped[0]!.failed], [0, 0, false]);
  const carousel = await topicAt(`https://example.com/carousel-${tag()}`, ["carousel"]);
  await fetchLayer4(carousel.topicId, { record: counting, get: noRobots });
  assert.equal(calls, 0);
});

test("a page that does not load, or records mostly black, leaves a gap and no video", { skip }, async () => {
  const slow = await topicAt(`https://example.com/slow-${tag()}`);
  const timeout: Recorder = async () => { throw new Error("page.goto: Timeout 30000ms exceeded."); };
  const r1 = await fetchLayer4(slow.topicId, { record: timeout, get: noRobots });
  assert.equal(r1.recorded.length, 0);
  assert.match((await gaps(slow.topicId))[0]!.reason, /沒有載入/);
  assert.equal((await sql`SELECT 1 FROM topic_assets WHERE topic_id = ${slow.topicId}`).length, 0);

  const dark = await topicAt(`https://example.com/dark-${tag()}`);
  await fetchLayer4(dark.topicId, { record: black, get: noRobots });
  assert.match((await gaps(dark.topicId))[0]!.reason, /黑/);
  assert.equal((await sql`SELECT 1 FROM topic_assets WHERE topic_id = ${dark.topicId}`).length, 0);
});

test("robots.txt is respected: a disallowed page is not opened and is no failure", { skip }, async () => {
  const t = await topicAt(`https://example.com/private/${tag()}`);
  let calls = 0;
  const robots: Get = async (url) => ({ status: 200, url, type: "text/plain", text: "User-agent: *\nDisallow: /private/" });
  const r = await fetchLayer4(t.topicId, { record: async (...a) => { calls += 1; await picture(...a); }, get: robots });
  assert.deepEqual([calls, r.skipped[0]!.failed], [0, false]);
  assert.equal((await gaps(t.topicId)).length, 0);
});

test("toMp4 measures how much of a recording is black", { skip }, async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "l4-unit-"));
  try {
    const src = path.join(dir, "a.webm");
    await picture("", src, 0);
    assert.ok((await toMp4(src, path.join(dir, "a.mp4"))).blackShare < 0.1);
    await black("", src, 0);
    assert.ok((await toMp4(src, path.join(dir, "b.mp4"))).blackShare > 0.9);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
