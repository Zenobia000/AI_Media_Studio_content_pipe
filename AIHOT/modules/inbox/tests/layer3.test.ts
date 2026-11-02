import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { inventory } from "../assets.ts";
import { embeddedVideos, explain, fetchLayer3, VIDEO_FORMAT, type Exec, type ExecResult } from "../layer3.ts";
import { addSource } from "../manual.ts";
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

let home: string;
let clip: string;
let still: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-l3-"));
  process.env.NEWSROOM_HOME = home;
  clip = path.join(home, "clip.mp4");
  still = path.join(home, "still.png");
  if (hasFfmpeg) {
    execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25", "-pix_fmt", "yuv420p", clip], { stdio: "ignore" });
    execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:size=640x480", "-frames:v", "1", still], { stdio: "ignore" });
  }
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

const ok = (out = ""): ExecResult => ({ code: 0, out, err: "" });

interface Fake {
  exec: Exec;
  calls: string[][];
}
/** A stand-in for the downloaders: yt-dlp writes the clip where it was told, gallery-dl two pictures. */
function fake(opts: { video?: "ok" | { err: string }; gallery?: boolean; missing?: string[] } = {}): Fake {
  const calls: string[][] = [];
  const exec: Exec = async (command, args) => {
    calls.push([command, ...args]);
    const name = args[0] === "-m" ? args[1] : command;
    if (opts.missing?.includes(command)) return { code: "ENOENT", out: "", err: "not found" };
    if (args.at(-1) === "--version") return ok("1.0");
    if (name === "yt-dlp" || name === "yt_dlp") {
      if (typeof opts.video === "object") return { code: 1, out: "", err: opts.video.err };
      const template = args[args.indexOf("-o") + 1]!;
      const file = template.replace("%(id)s", "abc123").replace("%(ext)s", "mp4");
      await mkdir(path.dirname(file), { recursive: true });
      await copyFile(clip, file);
      return ok(`${file}\n`);
    }
    if (name === "gallery-dl" || name === "gallery_dl") {
      const dir = args[args.indexOf("-D") + 1]!;
      await mkdir(dir, { recursive: true });
      await copyFile(still, path.join(dir, "1.png"));
      return ok("");
    }
    return { code: 1, out: "", err: `unexpected ${command}` };
  };
  return { exec, calls };
}

async function topicAt(url: string) {
  const added = await addSource(url, { title: `下載 ${tag()}`, queue: false });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
}
const rowsOf = async (topicId: string) => sql<{ origin: string; fetch_layer: number; fetch_method: string; source_url: string }[]>`SELECT origin, fetch_layer, fetch_method, source_url FROM topic_assets WHERE topic_id = ${topicId}`;
const noPage: Get = async (url) => ({ status: 404, url, type: "text/plain", text: "" });

test("a YouTube source is downloaded at up to 1080p H.264, recorded as third party, and given a usage note", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const url = `https://www.youtube.com/watch?v=${tag().replace(/[^A-Za-z0-9]/g, "").padEnd(11, "x").slice(0, 11)}`;
  const t = await topicAt(url);
  const f = fake({ video: "ok" });
  const result = await fetchLayer3(t.topicId, { exec: f.exec, get: noPage, cookiesFile: null });
  assert.deepEqual(result.downloaded.map(d => [d.kind, d.method]), [["video", "yt-dlp"]]);
  const args = f.calls.find(c => c.includes("-f"))!;
  assert.equal(args[args.indexOf("-f") + 1], VIDEO_FORMAT);
  assert.match(VIDEO_FORMAT, /height<=1080/);
  assert.match(VIDEO_FORMAT, /avc\|h264/);
  assert.equal(args[args.indexOf("--max-filesize") + 1], "500M");
  assert.ok(args.includes("--no-playlist") && !args.includes("--cookies"));
  assert.deepEqual((await rowsOf(t.topicId)).map(r => [r.origin, r.fetch_layer, r.fetch_method]), [["third_party", 3, "yt-dlp"]]);
  const asset = (await inventory(t.topicId)).assets[0]!;
  assert.equal(asset.origin, "third_party");
  assert.equal(asset.fetch_layer, 3);
  assert.match(asset.usage_note, /確認授權/);
  assert.equal(asset.duration_ms! > 1500, true);
});

test("a source L2 already gave a video is not asked again", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topicAt(`https://vimeo.com/${Date.now()}`);
  const [source] = await sql<{ source_id: string }[]>`SELECT source_id FROM topic_sources WHERE topic_id = ${t.topicId}`;
  const f = fake({ video: "ok" });
  const result = await fetchLayer3(t.topicId, { exec: f.exec, get: noPage, haveVideo: new Set([source!.source_id]) });
  assert.deepEqual(result.skipped, [source!.source_id]);
  assert.deepEqual(result.downloaded, []);
  assert.equal(f.calls.length, 0, "no downloader was run, not even for its version");
});

test("without a JavaScript runtime the error says how to install one; with a login wall it says what the person can do", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topicAt(`https://www.youtube.com/watch?v=noRuntime${tag().replace(/[^A-Za-z0-9]/g, "").slice(0, 4)}`);
  const noRuntime = await fetchLayer3(t.topicId, { exec: fake({ video: { err: "ERROR: [youtube] abc: No supported JavaScript runtime could be found. Install deno" } }).exec, get: noPage });
  assert.equal(noRuntime.failed[0]!.code, "JS_RUNTIME");
  assert.match(noRuntime.failed[0]!.message, /Deno/);
  assert.match(noRuntime.failed[0]!.message, /winget install DenoLand\.Deno/);
  const login = await fetchLayer3(t.topicId, { exec: fake({ video: { err: "ERROR: Sign in to confirm you're not a bot" } }).exec, get: noPage });
  assert.equal(login.failed[0]!.code, "LOGIN");
  assert.match(login.failed[0]!.message, /YTDLP_COOKIES_FILE/);
  assert.equal((await rowsOf(t.topicId)).length, 0, "nothing was recorded for a download that failed");
});

test("a downloader that is missing is named with how to install it; one that only runs as a Python module is found", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topicAt(`https://www.youtube.com/watch?v=missing${tag().replace(/[^A-Za-z0-9]/g, "").slice(0, 5)}`);
  const none = await fetchLayer3(t.topicId, { exec: fake({ video: "ok", missing: ["yt-dlp", "python", "py"] }).exec, get: noPage });
  assert.equal(none.failed[0]!.code, "MISSING_TOOL");
  assert.match(none.failed[0]!.message, /pip install yt-dlp/);
  const viaPython = fake({ video: "ok", missing: ["yt-dlp"] });
  const found = await fetchLayer3(t.topicId, { exec: viaPython.exec, get: noPage });
  assert.equal(found.downloaded.length, 1);
  assert.ok(viaPython.calls.some(c => c[0] === "python" && c.includes("yt_dlp") && c.includes("-f")));
});

test("the cookies file named in the settings is handed to the downloader", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const t = await topicAt(`https://www.youtube.com/watch?v=cookies${tag().replace(/[^A-Za-z0-9]/g, "").slice(0, 5)}`);
  const f = fake({ video: "ok" });
  await fetchLayer3(t.topicId, { exec: f.exec, get: noPage, cookiesFile: "/secrets/cookies.txt" });
  const args = f.calls.find(c => c.includes("-f"))!;
  assert.equal(args[args.indexOf("--cookies") + 1], "/secrets/cookies.txt");
});

test("a video a news page embeds is downloaded; a post with no video falls to the picture downloader", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://news.example.com/report-${tag()}`;
  const embedded = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  const html = `<html><body><iframe src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"></iframe><iframe src="https://ads.example/x"></iframe></body></html>`;
  const t = await topicAt(page);
  const f = fake({ video: "ok" });
  const get: Get = async (url) => (url === page ? { status: 200, url, type: "text/html", text: html } : { status: 404, url, type: "", text: "" });
  const result = await fetchLayer3(t.topicId, { exec: f.exec, get });
  assert.deepEqual(result.downloaded.map(d => [d.url, d.method]), [[embedded, "yt-dlp"]]);

  const post = await topicAt(`https://x.com/acme/status/${Date.now()}`);
  const g = fake({ video: { err: "ERROR: [twitter] No video could be found in this tweet" }, gallery: true });
  const pics = await fetchLayer3(post.topicId, { exec: g.exec, get: noPage });
  assert.deepEqual(pics.downloaded.map(d => [d.kind, d.method]), [["image", "gallery-dl"]]);
  assert.equal(pics.failed.length, 1, "the missing video is reported, the pictures are kept");
});

test("embedded players become the page of the video; what a downloader complains of is explained", () => {
  const html = `<iframe src="//www.youtube.com/embed/abcdefghijk"></iframe><iframe data-src="https://player.vimeo.com/video/123456"></iframe><iframe src="https://example.com/embed/abcdefghijk"></iframe>`;
  assert.deepEqual(embeddedVideos(html, "https://news.example.com/a"), ["https://www.youtube.com/watch?v=abcdefghijk", "https://vimeo.com/123456"]);
  assert.equal(explain("ERROR: This video is private", "yt-dlp").code, "LOGIN");
  assert.match(explain("ERROR: Unable to extract nsig function", "yt-dlp").message, /pip install -U yt-dlp/);
  assert.match(explain("HTTP Error 403: Forbidden", "gallery-dl").message, /pip install -U gallery-dl/);
  assert.equal(explain("File is larger than max-filesize", "yt-dlp").code, "TOO_LARGE");
  assert.equal(explain("ERROR: something odd happened\nmore", "yt-dlp").code, "FAILED");
});
