import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { gapsFor, inventory, typeOf, type Asset } from "../assets.ts";
import { openTopic } from "../topics.ts";
import { writeInbox } from "../server.ts";

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
  home = await mkdtemp(path.join(tmpdir(), "newsroom-assets-"));
  process.env.NEWSROOM_HOME = home;
  fixtures = path.join(home, "_fixtures");
  await mkdir(fixtures, { recursive: true });
  if (hasFfmpeg) {
    const ff = (...args: string[]) => execFileSync("ffmpeg", ["-v", "error", "-y", ...args], { stdio: "ignore" });
    ff("-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p", path.join(fixtures, "clip.mp4"));
    ff("-f", "lavfi", "-i", "sine=frequency=330:duration=1.5", path.join(fixtures, "voice.wav"));
    ff("-f", "lavfi", "-i", "color=c=red:size=640x360", "-frames:v", "1", path.join(fixtures, "cover.png"));
  }
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

async function topic(formats: string[]) {
  const added = await addSource(`https://example.com/assets-${tag()}`, { title: `素材 ${tag()}`, queue: false });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats });
}
const put = async (topicId: string, from: string, to: string) => {
  const dest = path.join(home, "topics", topicId, to);
  await mkdir(path.dirname(dest), { recursive: true });
  await copyFile(path.join(fixtures, from), dest);
  return dest;
};

test("a video and a recording a person put in come out as self with the right length", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic(["threads"]);
  await put(t.topicId, "clip.mp4", "media/raw/my-clip.mp4");
  await put(t.topicId, "voice.wav", "media/raw/voice.wav");
  const r = await inventory(t.topicId);
  const video = r.assets.find(a => a.path === "media/raw/my-clip.mp4")!;
  const audio = r.assets.find(a => a.path === "media/raw/voice.wav")!;
  assert.equal(video.type, "video");
  assert.equal(video.origin, "self");
  assert.ok(Math.abs(video.duration_ms! - 2000) <= 100, `video ${video.duration_ms}`);
  assert.deepEqual([video.width, video.height, video.fps, video.has_audio], [320, 240, 25, true]);
  assert.deepEqual(video.usable_ranges_ms, [[0, video.duration_ms!]]);
  assert.equal(audio.type, "audio");
  assert.equal(audio.origin, "self");
  assert.ok(Math.abs(audio.duration_ms! - 1500) <= 100, `audio ${audio.duration_ms}`);
  assert.equal(audio.width, null);
  const json = JSON.parse(await readFile(path.join(home, "topics", t.topicId, "assets.json"), "utf8")) as { schema_version: number; topic_id: string; assets: Asset[] };
  assert.equal(json.schema_version, 1);
  assert.equal(json.topic_id, t.topicId);
  assert.deepEqual(json.assets.map(a => a.asset_id).sort(), [video.asset_id, audio.asset_id].sort());
  assert.match(video.asset_id, /^ast_[0-9a-f]{12}$/);
  const media = await readFile(path.join(home, "topics", t.topicId, "media.md"), "utf8");
  assert.match(media, /media\/raw\/my-clip\.mp4/);
  assert.match(media, /media\/raw\/voice\.wav/);
});

test("deleting a file removes it from assets.json and marks it deleted; a rename keeps the id; notes a person wrote stay", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic(["threads"]);
  const clip = await put(t.topicId, "clip.mp4", "media/raw/a.mp4");
  await put(t.topicId, "voice.wav", "media/raw/b.wav");
  const first = await inventory(t.topicId);
  const clipId = first.assets.find(a => a.path.endsWith("a.mp4"))!.asset_id;
  const voiceId = first.assets.find(a => a.path.endsWith("b.wav"))!.asset_id;

  const file = path.join(home, "topics", t.topicId, "assets.json");
  const edited = JSON.parse(await readFile(file, "utf8")) as { assets: Asset[] };
  edited.assets.find(a => a.asset_id === clipId)!.usage_note = "自己錄的，不用標註來源";
  await writeFile(file, JSON.stringify(edited), "utf8");

  await rename(clip, path.join(home, "topics", t.topicId, "media/raw/renamed.mp4"));
  await rm(path.join(home, "topics", t.topicId, "media/raw/b.wav"));
  const second = await inventory(t.topicId);
  assert.deepEqual(second.removed, [voiceId]);
  assert.deepEqual(second.assets.map(a => [a.asset_id, a.path]), [[clipId, "media/raw/renamed.mp4"]]);
  assert.equal(second.assets[0]!.usage_note, "自己錄的，不用標註來源");
  assert.deepEqual((JSON.parse(await readFile(file, "utf8")) as { assets: Asset[] }).assets.map(a => a.asset_id), [clipId]);
  const rows = await sql<{ asset_id: string; deleted: boolean }[]>`SELECT asset_id, deleted_at IS NOT NULL AS deleted FROM topic_assets WHERE topic_id = ${t.topicId} ORDER BY asset_id`;
  assert.deepEqual(rows.find(r => r.asset_id === voiceId)?.deleted, true);
  assert.deepEqual(rows.find(r => r.asset_id === clipId)?.deleted, false);
});

test("a reel with no picture material has a gap with a suggestion; a clip closes it", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic(["reel", "carousel"]);
  const empty = await inventory(t.topicId);
  assert.deepEqual(empty.gaps.map(g => g.need).sort(), ["hero-image", "visual"]);
  assert.ok(empty.gaps.every(g => g.suggestion.length > 0));
  assert.match(await readFile(path.join(home, "topics", t.topicId, "media.md"), "utf8"), /缺口與建議/);

  await put(t.topicId, "clip.mp4", "media/raw/demo.mp4");
  await put(t.topicId, "cover.png", "media/raw/cover.png");
  const filled = await inventory(t.topicId);
  assert.deepEqual(filled.gaps, []);
  assert.equal(filled.assets.find(a => a.path.endsWith("cover.png"))!.type, "image");
});

test("which gaps a format has, and which file types count", () => {
  const asset = (type: Asset["type"]) => ({ type }) as Asset;
  assert.deepEqual(gapsFor(["threads"], []), []);
  assert.deepEqual(gapsFor(["reel"], [asset("audio")]).map(g => g.need), ["visual"]);
  assert.deepEqual(gapsFor(["reel"], [asset("video")], { voiceover: true }).map(g => g.need), ["voiceover"]);
  assert.deepEqual(gapsFor(["carousel"], [asset("screenshot")]), []);
  assert.equal(typeOf("a.MP4"), "video");
  assert.equal(typeOf("Screenshot 1.png"), "screenshot");
  assert.equal(typeOf("notes.exe"), null);
});

test("a still picture has no length", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topic(["threads"]);
  await put(t.topicId, "cover.png", "media/raw/cover.png");
  const picture = (await inventory(t.topicId)).assets[0]!;
  assert.deepEqual([picture.type, picture.duration_ms, picture.fps, picture.width, picture.height], ["image", null, null, 640, 360]);
});
