import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb } from "@aihot/backend/db";
import { inventory } from "../assets.ts";
import { addSource } from "../manual.ts";
import { measureLufs, preprocess } from "../preprocess.ts";
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
  home = await mkdtemp(path.join(tmpdir(), "newsroom-pre-"));
  process.env.NEWSROOM_HOME = home;
  fixtures = path.join(home, "_fixtures");
  await mkdir(fixtures, { recursive: true });
  if (!hasFfmpeg) return;
  const ff = (name: string, ...args: string[]) => execFileSync("ffmpeg", ["-v", "error", "-y", ...args, path.join(fixtures, name)], { stdio: "ignore" });
  // 24 fps, odd width, 4:4:4, a sound: the shape of footage that goes out of step when cut together.
  ff("clip24.mp4", "-f", "lavfi", "-i", "testsrc=duration=2:size=321x240:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv444p", "-shortest");
  ff("anim.gif", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=5");
  // A recording: half a second of silence, three seconds of a quiet tone, half a second of silence (44.1 kHz stereo).
  ff("voice.wav", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=0.5", "-f", "lavfi", "-i", "sine=frequency=300:duration=3:sample_rate=44100,volume=0.05,aformat=channel_layouts=stereo", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=0.5",
    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1");
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

async function topic(files: Array<[from: string, to: string]>) {
  const added = await addSource(`https://example.com/pre-${tag()}`, { title: `前處理 ${tag()}`, queue: false });
  await writeInbox();
  const t = await openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
  for (const [from, to] of files) {
    const dest = path.join(home, "topics", t.topicId, to);
    await mkdir(path.dirname(dest), { recursive: true });
    await copyFile(path.join(fixtures, from), dest);
  }
  return t;
}
const probe = (file: string, entries: string) => JSON.parse(execFileSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_entries", entries, file], { encoding: "utf8" })) as { streams: Array<Record<string, string>> };
const sha = async (file: string) => createHash("sha256").update(await readFile(file)).digest("hex");

test("a 24 fps video comes out 30 fps constant, yuv420p, H.264 with even sides, and the original is untouched", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["clip24.mp4", "media/raw/clip.mp4"], ["anim.gif", "media/raw/anim.gif"]]);
  const original = path.join(home, "topics", t.topicId, "media/raw/clip.mp4");
  const [beforeSha, beforeMtime] = [await sha(original), (await stat(original)).mtimeMs];
  const r = await preprocess(t.topicId);
  assert.equal(r.failed.length, 0, JSON.stringify(r.failed));
  assert.equal(r.made.length, 2);

  const stock = await inventory(t.topicId);
  const source = stock.assets.find(a => a.path === "media/raw/clip.mp4")!;
  const out = path.join(home, "topics", t.topicId, "media/proc", `${source.asset_id}.mp4`);
  const v = probe(out, "stream=codec_type,codec_name,r_frame_rate,avg_frame_rate,pix_fmt,width,height").streams.find(s => s.codec_type === "video")!;
  assert.deepEqual([v.codec_name, v.r_frame_rate, v.avg_frame_rate, v.pix_fmt], ["h264", "30/1", "30/1", "yuv420p"]);
  assert.deepEqual([Number(v.width) % 2, Number(v.height) % 2], [0, 0]);
  assert.ok(probe(out, "stream=codec_type").streams.some(s => s.codec_type === "audio"), "the sound is kept");
  assert.deepEqual([await sha(original), (await stat(original)).mtimeMs], [beforeSha, beforeMtime], "the original is not changed");

  const derived = stock.assets.length ? (await inventory(t.topicId)).assets.find(a => a.path === `media/proc/${source.asset_id}.mp4`)! : null;
  assert.equal(derived!.origin, "derived");
  assert.equal(derived!.fps, 30);
  assert.match(derived!.notes, new RegExp(`由 ${source.asset_id} 前處理而來`));
});

test("what has been made is not made again", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["clip24.mp4", "media/raw/clip.mp4"]]);
  const first = await preprocess(t.topicId);
  assert.equal(first.made.length, 1);
  const out = path.join(home, "topics", t.topicId, first.made[0]!.path);
  const mtime = (await stat(out)).mtimeMs;
  await new Promise(resolve => setTimeout(resolve, 30));
  const second = await preprocess(t.topicId);
  assert.deepEqual(second.made, []);
  assert.deepEqual(second.kept, ["media/raw/clip.mp4"]);
  assert.equal((await stat(out)).mtimeMs, mtime);
});

test("a recording becomes 48 kHz WAV, loses the silence at both ends, and is near -14 LUFS", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["voice.wav", "media/raw/voice.wav"]]);
  const original = path.join(home, "topics", t.topicId, "media/raw/voice.wav");
  const quiet = await measureLufs(original);
  assert.ok(quiet < -25, `the fixture is quiet (${quiet})`);
  const r = await preprocess(t.topicId);
  assert.equal(r.failed.length, 0, JSON.stringify(r.failed));
  const out = path.join(home, "topics", t.topicId, r.made[0]!.path);
  const a = probe(out, "stream=codec_name,sample_rate,duration").streams[0]!;
  assert.deepEqual([a.codec_name, a.sample_rate], ["pcm_s16le", "48000"]);
  const seconds = Number(JSON.parse(execFileSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_entries", "format=duration", out], { encoding: "utf8" })).format.duration);
  assert.ok(seconds > 2.7 && seconds < 3.3, `silence is off both ends: ${seconds}s of a 4 s file`);
  const lufs = await measureLufs(out);
  assert.ok(Math.abs(lufs - -14) <= 1, `loudness ${lufs} LUFS`);
});

test("the loudness target can be set", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["voice.wav", "media/raw/voice.wav"]]);
  const r = await preprocess(t.topicId, { lufs: -18 });
  const lufs = await measureLufs(path.join(home, "topics", t.topicId, r.made[0]!.path));
  assert.ok(Math.abs(lufs - -18) <= 1, `loudness ${lufs} LUFS`);
});

test("only what was asked for is made; a file that cannot be read is left out and said so", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic([["clip24.mp4", "media/raw/ok.mp4"], ["anim.gif", "media/raw/anim.gif"], ["voice.wav", "media/raw/voice.wav"]]);
  await writeFile(path.join(home, "topics", t.topicId, "media/raw/broken.mp4"), "this is not a video");
  const videoOnly = await preprocess(t.topicId, { audio: false });
  assert.deepEqual(videoOnly.made.map(m => m.kind), ["video", "video"]);
  assert.deepEqual(videoOnly.failed, []);
  const rest = await preprocess(t.topicId, { video: false });
  assert.deepEqual(rest.made.map(m => m.kind), ["narration"]);
  const stock = await inventory(t.topicId);
  assert.ok(stock.warnings.some(w => w.includes("broken.mp4")));
  assert.ok(!stock.assets.some(a => a.path.endsWith("broken.mp4")));
});

test("footage that already is 30 fps H.264 yuv420p is wrapped, not encoded again", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const good = path.join(fixtures, "good30.mp4");
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", good], { stdio: "ignore" });
  const t = await topic([["good30.mp4", "media/raw/good.mp4"]]);
  const r = await preprocess(t.topicId);
  const out = path.join(home, "topics", t.topicId, r.made[0]!.path);
  const v = probe(out, "stream=codec_name,r_frame_rate,avg_frame_rate,pix_fmt").streams[0]!;
  assert.deepEqual([v.codec_name, v.r_frame_rate, v.avg_frame_rate, v.pix_fmt], ["h264", "30/1", "30/1", "yuv420p"]);
  // Wrapped, so it is about as big as the original; encoding it again at crf 18 would make a clearly different file.
  const [original, copy] = [(await stat(good)).size, (await stat(out)).size];
  assert.ok(Math.abs(copy - original) / original < 0.05, `original ${original} bytes, result ${copy} bytes`);
});
