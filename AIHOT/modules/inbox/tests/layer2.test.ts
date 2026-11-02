import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { inventory, type Asset } from "../assets.ts";
import { fetchLayer2, findMedia, findMediaInMarkdown, robotsAllows, type Binary, type GetBinary } from "../layer2.ts";
import { addSource } from "../manual.ts";
import { writeInbox } from "../server.ts";
import type { Get, Page } from "../source-extract.ts";
import { openTopic } from "../topics.ts";
import { readFileSync } from "node:fs";

const hasFfmpeg = (() => {
  try {
    execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();

let home: string;
const files: Record<string, Buffer> = {};
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-l2-"));
  process.env.NEWSROOM_HOME = home;
  if (!hasFfmpeg) return;
  const fixtures = path.join(home, "_fixtures");
  await mkdir(fixtures, { recursive: true });
  const ff = (name: string, ...args: string[]) => {
    execFileSync("ffmpeg", ["-v", "error", "-y", ...args, path.join(fixtures, name)], { stdio: "ignore" });
    files[name] = readFileSync(path.join(fixtures, name));
  };
  ff("v1.mp4", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25", "-pix_fmt", "yuv420p");
  ff("v2.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25", "-pix_fmt", "yuv420p");
  ff("v3.mp4", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25", "-pix_fmt", "yuv420p");
  ff("v4.mp4", "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=25", "-pix_fmt", "yuv420p");
  ff("anim.gif", "-f", "lavfi", "-i", "testsrc=duration=1:size=500x400:rate=5");
  ff("hero.png", "-f", "lavfi", "-i", "color=c=blue:size=800x600", "-frames:v", "1");
  ff("logo.png", "-f", "lavfi", "-i", "color=c=red:size=100x100", "-frames:v", "1");
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

const typeOf: Record<string, string> = { mp4: "video/mp4", gif: "image/gif", png: "image/png" };
/** Recorded files by address; `lengths` overrides the size a server states. */
function server(map: Record<string, string>, lengths: Record<string, number> = {}): { get: Get; getBinary: GetBinary; requests: Array<{ url: string; at: number }> } {
  const requests: Array<{ url: string; at: number }> = [];
  const get: Get = async (url) => {
    requests.push({ url, at: Date.now() });
    const body = map[url];
    return body === undefined ? { status: 404, url, type: "text/plain", text: "" } : { status: 200, url, type: url.endsWith("robots.txt") ? "text/plain" : "text/html", text: body };
  };
  const getBinary: GetBinary = async (url, opts): Promise<Binary> => {
    requests.push({ url, at: Date.now() });
    const name = map[`file:${url}`];
    if (!name) return { status: 404, url, type: "", length: null, body: Buffer.alloc(0) };
    const body = files[name]!;
    return { status: 200, url, type: typeOf[name.split(".").pop()!]!, length: lengths[url] ?? body.length, body: opts.head ? Buffer.alloc(0) : body };
  };
  return { get, getBinary, requests };
}

async function topicAt(pageUrl: string) {
  const added = await addSource(pageUrl, { title: `媒體 ${tag()}`, queue: false });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
}

test("which media a page names, in each of the ways a page can name it", () => {
  const html = `<html><head>
    <meta property="og:video" content="/media/og.mp4"><meta name="twitter:player:stream" content="https://cdn.example.com/tw.mp4">
    <meta property="og:image" content="/hero.png"></head><body>
    <video src="/media/tag.mp4"></video><video><source src="/media/src.webm" type="video/webm"></video>
    <img src="/media/demo.gif"><img src="/logo.png"><a href="/files/clip.mov">clip</a><a href="/about">about</a></body></html>`;
  assert.deepEqual(findMedia(html, "https://example.com/post").map(m => [m.via, m.kind, m.url]), [
    ["video-tag", "video", "https://example.com/media/tag.mp4"],
    ["source-tag", "video", "https://example.com/media/src.webm"],
    ["og:video", "video", "https://example.com/media/og.mp4"],
    ["twitter:player", "video", "https://cdn.example.com/tw.mp4"],
    ["gif-img", "gif", "https://example.com/media/demo.gif"],
    ["link", "video", "https://example.com/files/clip.mov"],
    ["og:image", "image", "https://example.com/hero.png"],
  ]);
  const readme = "![demo](docs/demo.gif)\n\nhttps://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc\n\n<video src=\"https://example.org/x.mp4\"></video>\n[logo](logo.png)";
  assert.deepEqual(findMediaInMarkdown(readme, "https://raw.githubusercontent.com/acme/tool/HEAD/").map(m => m.url), [
    "https://raw.githubusercontent.com/acme/tool/HEAD/docs/demo.gif",
    "https://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc",
    "https://example.org/x.mp4",
  ]);
});

test("robots.txt: the longest rule wins, a rule for the crawler beats the general one", () => {
  const robots = "User-agent: *\nDisallow: /private/\nAllow: /private/public/\n\nUser-agent: SomeOtherBot\nDisallow: /\n";
  assert.equal(robotsAllows(robots, "/private/a.mp4"), false);
  assert.equal(robotsAllows(robots, "/private/public/a.mp4"), true);
  assert.equal(robotsAllows(robots, "/open/a.mp4"), true);
  assert.equal(robotsAllows("User-agent: *\nDisallow: /*.mp4$", "/x/a.mp4"), false);
  assert.equal(robotsAllows("", "/anything"), true);
});

test("a page's videos, GIF and main picture are downloaded; a logo is not", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://site.example/post-${tag()}`;
  const html = `<html><head><meta property="og:video" content="/m/v3.mp4"><meta name="twitter:player:stream" content="/m/v4.mp4"><meta property="og:image" content="/m/hero.png"></head>
    <body><video src="/m/v1.mp4"></video><video><source src="/m/v2.mp4" type="video/mp4"></video><img src="/m/anim.gif"><a href="/m/logo.png">logo</a><img src="/m/logo.png"></body></html>`;
  const map: Record<string, string> = { [page]: html };
  for (const name of ["v1.mp4", "v2.mp4", "v3.mp4", "v4.mp4", "anim.gif", "hero.png", "logo.png"]) map[`file:https://site.example/m/${name}`] = name;
  const t = await topicAt(page);
  const srv = server(map);
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 0 });
  assert.deepEqual(result.downloaded.map(d => d.kind).sort(), ["gif", "image", "video", "video", "video", "video"]);
  assert.equal(result.skipped.length, 0);

  const stock = await inventory(t.topicId);
  const l2 = stock.assets.filter((a: Asset) => a.fetch_layer === 2);
  assert.equal(l2.length, 6);
  assert.ok(l2.every(a => a.origin === "official" && a.source_url?.startsWith("https://site.example/m/") && a.path.startsWith("media/raw/l2-")));
  assert.deepEqual(l2.map(a => a.fetch_method).sort(), ["html-image", "html-image", "html-video", "html-video", "html-video", "html-video"]);
  assert.equal(stock.gaps.length, 0, "a reel with pictures and clips has no gap");
  const json = JSON.parse(await readFile(path.join(home, "topics", t.topicId, "assets.json"), "utf8")) as { assets: Asset[] };
  assert.equal(json.assets.filter(a => a.fetch_layer === 2).length, 6);
});

test("a picture with a side under 400 px is not kept", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://site.example/logo-${tag()}`;
  const map: Record<string, string> = { [page]: `<html><head><meta property="og:image" content="/m/logo.png"></head><body></body></html>`, "file:https://site.example/m/logo.png": "logo.png" };
  const t = await topicAt(page);
  const srv = server(map);
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 0 });
  assert.deepEqual(result.downloaded, []);
  assert.match(result.skipped[0]!.reason, /圖片太小（100×100/);
  assert.deepEqual(await readdir(path.join(home, "topics", t.topicId, "media", "raw")).catch(() => []), []);
});

test("a file over the limit is skipped and left as a gap for the person", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://site.example/big-${tag()}`;
  const url = "https://site.example/m/big.mp4";
  const t = await topicAt(page);
  const srv = server({ [page]: `<html><body><video src="/m/big.mp4"></video></body></html>`, [`file:${url}`]: "v1.mp4" }, { [url]: 900 * 1024 * 1024 });
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 0 });
  assert.deepEqual(result.downloaded, []);
  assert.match(result.skipped[0]!.reason, /超過 500 MB/);
  const stock = await inventory(t.topicId);
  assert.ok(stock.gaps.some(g => g.need === "media" && g.reason.includes("big.mp4")));
  // Run again with the file small: the gap is gone.
  const second = server({ [page]: `<html><body><video src="/m/big.mp4"></video></body></html>`, [`file:${url}`]: "v1.mp4" });
  await fetchLayer2(t.topicId, { get: second.get, getBinary: second.getBinary, delayMs: 0 });
  assert.equal((await inventory(t.topicId)).gaps.some(g => g.need === "media"), false);
});

test("a page or file robots.txt forbids is not fetched", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://guarded.example/post-${tag()}`;
  const map: Record<string, string> = {
    "https://guarded.example/robots.txt": "User-agent: *\nDisallow: /private/\n",
    [page]: `<html><body><video src="/private/secret.mp4"></video><video src="/open/ok.mp4"></video></body></html>`,
    "file:https://guarded.example/private/secret.mp4": "v1.mp4",
    "file:https://guarded.example/open/ok.mp4": "v2.mp4",
  };
  const t = await topicAt(page);
  const srv = server(map);
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 0 });
  assert.deepEqual(result.downloaded.map(d => d.url), ["https://guarded.example/open/ok.mp4"]);
  assert.match(result.skipped[0]!.reason, /robots\.txt/);
  assert.ok(!srv.requests.some(r => r.url.includes("/private/secret")), "the forbidden file was never requested");
});

test("requests to the same host are spaced; another host is not held back", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://paced.example/post-${tag()}`;
  const map: Record<string, string> = { [page]: `<html><body><video src="/a.mp4"></video><video src="https://other.example/b.mp4"></video></body></html>`, "file:https://paced.example/a.mp4": "v1.mp4", "file:https://other.example/b.mp4": "v2.mp4" };
  const t = await topicAt(page);
  const srv = server(map);
  let clock = 0;
  const sleeps: number[] = [];
  await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 1000, now: () => clock, sleep: async ms => { sleeps.push(ms); clock += ms; } });
  assert.ok(sleeps.length > 0 && sleeps.every(ms => ms <= 1000));
  assert.ok(srv.requests.some(r => r.url === "https://other.example/b.mp4"));
});

test("a server that refuses HEAD is still downloaded from; a body that grows past the limit while downloading is a gap", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://signed.example/post-${tag()}`;
  const ok = "https://signed.example/ok.mp4";
  const huge = "https://signed.example/huge.mp4";
  const t = await topicAt(page);
  const srv = server({ [page]: `<html><body><video src="/ok.mp4"></video><video src="/huge.mp4"></video></body></html>`, [`file:${ok}`]: "v1.mp4" });
  const getBinary: GetBinary = async (url, opts) => {
    if (opts.head) return { status: 403, url, type: "", length: null, body: Buffer.alloc(0) };
    if (url === huge) throw new Error("Response too large from signed.example");
    return srv.getBinary(url, opts);
  };
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary, delayMs: 0 });
  assert.deepEqual(result.downloaded.map(d => d.url), [ok]);
  assert.match(result.skipped.find(s => s.url === huge)!.reason, /超過 500 MB/);
  assert.ok((await inventory(t.topicId)).gaps.some(g => g.need === "media" && g.reason.includes("huge.mp4")));
});

test("a page that names more media than the cap gives the videos first and says how many it left", { skip: !hasFfmpeg && "ffmpeg is not installed" }, async () => {
  const page = `https://many.example/post-${tag()}`;
  const map: Record<string, string> = { [page]: `<html><body><img src="/m/anim.gif"><video src="/m/v1.mp4"></video><video src="/m/v2.mp4"></video><video src="/m/v3.mp4"></video></body></html>` };
  for (const name of ["v1.mp4", "v2.mp4", "v3.mp4", "anim.gif"]) map[`file:https://many.example/m/${name}`] = name;
  const t = await topicAt(page);
  const srv = server(map);
  const result = await fetchLayer2(t.topicId, { get: srv.get, getBinary: srv.getBinary, delayMs: 0, maxPerPage: 2 });
  assert.deepEqual(result.downloaded.map(d => d.kind), ["video", "video"]);
  assert.match(result.skipped[0]!.reason, /列出 4 個媒體，只取前 2 個/);
});
