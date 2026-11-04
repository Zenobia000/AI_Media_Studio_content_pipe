import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import type { SdMedia, SdTweet } from "@aihot/backend/providers/socialdata";
import { bestMp4, fetchLayer1, threadsVideo, type LookupTweet } from "../layer1.ts";
import { fetchLayer3, type Exec } from "../layer3.ts";
import type { Binary } from "../layer2.ts";
import { addSource } from "../manual.ts";
import { writeInbox } from "../server.ts";
import type { Get } from "../source-extract.ts";
import { openTopic } from "../topics.ts";

let home: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-l1-"));
  process.env.NEWSROOM_HOME = home;
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

const video = (...variants: Array<{ bitrate?: number; content_type: string; url: string }>): SdMedia => ({ type: "video", media_url_https: "https://pbs.example/poster.jpg", video_info: { variants } });
const mp4 = (bitrate: number) => ({ bitrate, content_type: "video/mp4", url: `https://video.example/${bitrate}.mp4` });
const hls = { content_type: "application/x-mpegURL", url: "https://video.example/playlist.m3u8" };
const tweetWith = (...media: SdMedia[]) => ({ id_str: "1", extended_entities: { media } }) as unknown as SdTweet;

const bytes = Buffer.from("not really a video, but bytes");
const binary = (calls: string[]) => async (url: string): Promise<Binary> => { calls.push(url); return { status: 200, url, type: "video/mp4", length: bytes.length, body: bytes }; };
const noPage: Get = async (url) => ({ status: 404, url, type: "text/plain", text: "" });

async function topicAt(url: string) {
  const added = await addSource(url, { title: `L1 ${tag()}`, queue: false });
  await writeInbox();
  return openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
}
const assets = (topicId: string) => sql<{ origin: string; fetch_layer: number; fetch_method: string }[]>`SELECT origin, fetch_layer, fetch_method FROM topic_assets WHERE topic_id = ${topicId}`;

test("of the renditions of an X video the mp4 with the highest bitrate is chosen, never the playlist or a photo", () => {
  assert.equal(bestMp4([video(mp4(256000), mp4(2176000), mp4(832000), hls)]), "https://video.example/2176000.mp4");
  assert.equal(bestMp4([video(hls)]), null);
  assert.equal(bestMp4([{ type: "photo", media_url_https: "https://pbs.example/a.jpg" }]), null);
  assert.equal(bestMp4([]), null);
});

test("a Threads page names its video with og:video", () => {
  assert.equal(threadsVideo('<meta property="og:video" content="https://cdn.example/v.mp4">', "https://www.threads.net/@a/post/x"), "https://cdn.example/v.mp4");
  assert.equal(threadsVideo('<meta property="og:video" content="http://cdn.example/v.mp4">', "https://www.threads.net/@a/post/x"), null, "not over plain http");
  assert.equal(threadsVideo("<html></html>", "https://www.threads.net/@a/post/x"), null);
});

test("an X video post gives its best mp4 through L1, recorded as an official L1 file, and L3 is not asked for it", async () => {
  const t = await topicAt(`https://x.com/acme_${tag()}/status/1900000000000000001`);
  const got: string[] = [];
  const lookup: LookupTweet = async () => tweetWith(video(mp4(320000), mp4(2176000), hls));
  const result = await fetchLayer1(t.topicId, { lookup, getBinary: binary(got), xConfigured: true });
  assert.deepEqual(got, ["https://video.example/2176000.mp4"]);
  assert.deepEqual(result.downloaded.map(d => [d.kind, d.method]), [["video", "x-api"]]);
  assert.deepEqual((await assets(t.topicId)).map(r => [r.origin, r.fetch_layer, r.fetch_method]), [["official", 1, "x-api"]]);
  assert.deepEqual(await readFile(path.join(home, "topics", t.topicId, result.downloaded[0]!.path)), bytes);

  const calls: string[][] = [];
  const exec: Exec = async (command, args) => { calls.push([command, ...args]); return { code: 1, out: "", err: "should not run" }; };
  const l3 = await fetchLayer3(t.topicId, { exec, get: noPage, cookiesFile: null, haveVideo: new Set(result.downloaded.map(d => d.sourceId)) });
  assert.equal(calls.length, 0, "no downloader was called");
  assert.equal(l3.downloaded.length, 0);
});

test("when L1 gets nothing the source is left for the next layer: no key, no video, no such post, a failed download", async () => {
  const t = await topicAt(`https://x.com/acme_${tag()}/status/1900000000000000002`);
  const got: string[] = [];
  const off = await fetchLayer1(t.topicId, { lookup: async () => { throw new Error("must not be called"); }, getBinary: binary(got), xConfigured: false });
  assert.deepEqual(off.downloaded, []);
  assert.deepEqual(off.skipped.map(s => s.failed), [false], "no key is not a failure of the layer");

  const photoOnly = await fetchLayer1(t.topicId, { lookup: async () => tweetWith({ type: "photo", media_url_https: "https://pbs.example/a.jpg" }), getBinary: binary(got), xConfigured: true });
  assert.deepEqual(photoOnly.skipped.map(s => s.failed), [false]);

  const gone = await fetchLayer1(t.topicId, { lookup: async () => null, getBinary: binary(got), xConfigured: true });
  assert.deepEqual(gone.skipped.map(s => s.failed), [true]);

  const refused = await fetchLayer1(t.topicId, { lookup: async () => tweetWith(video(mp4(1))), getBinary: async url => ({ status: 403, url, type: "", length: null, body: Buffer.alloc(0) }), xConfigured: true });
  assert.deepEqual(refused.downloaded, []);
  assert.match(refused.skipped[0]!.reason, /HTTP 403/);
  assert.deepEqual(got, []);
  assert.equal((await assets(t.topicId)).length, 0);
});

test("a Threads post is read for its og:video; other sources are not touched", async () => {
  const t = await topicAt(`https://www.threads.net/@acme${tag().replace(/\W/g, "")}/post/AbC123`);
  const got: string[] = [];
  const get: Get = async url => ({ status: 200, url, type: "text/html", text: '<meta property="og:video" content="https://cdn.example/t.mp4">' });
  const result = await fetchLayer1(t.topicId, { get, getBinary: binary(got), lookup: async () => { throw new Error("X lookup must not run"); } });
  assert.deepEqual(got, ["https://cdn.example/t.mp4"]);
  assert.deepEqual((await assets(t.topicId)).map(r => [r.fetch_layer, r.fetch_method]), [[1, "threads-og"]]);
  assert.equal(result.skipped.length, 0);

  const plain = await topicAt(`https://news.example.com/plain-${tag()}`);
  const none = await fetchLayer1(plain.topicId, { getBinary: binary(got), lookup: async () => { throw new Error("no"); } });
  assert.deepEqual([none.downloaded, none.skipped], [[], []]);
});
