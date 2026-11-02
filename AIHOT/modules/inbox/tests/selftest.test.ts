import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { fetchStats, recordAttempts } from "../attempts.ts";
import { fetchLayer3, type Exec, type ExecResult } from "../layer3.ts";
import { addSource } from "../manual.ts";
import { compareVersions, loadTargets, runSelftest, SELFTEST_FILE, type SelftestDeps } from "../selftest.ts";
import { writeInbox } from "../server.ts";
import type { Get, Page } from "../source-extract.ts";
import { openTopic } from "../topics.ts";

after(() => closeDb());

const body = "這是一段用來測試的正文，它夠長，才會被當成文章而不是導覽列。".repeat(8);
const GOOD_PAGE = `https://good.example/post-${tag()}`;
const GOOD_VIDEO_PAGE = `https://video.example/post-${tag()}`;
const html = (extra = "") => `<html lang="zh"><head><title>t</title></head><body><article><h1>標題</h1><p>${body}</p><p>${body}</p>${extra}</article></body></html>`;
const page = (url: string, text: string): Page => ({ status: 200, url, type: "text/html", text });
const ok = (out = ""): ExecResult => ({ code: 0, out, err: "" });

const get: Get = async (url) => {
  if (url === GOOD_PAGE) return page(url, html());
  if (url === GOOD_VIDEO_PAGE) return page(url, html('<video src="/m/clip.mp4"></video>'));
  return { status: 404, url, type: "text/plain", text: "" };
};
/** yt-dlp as it would answer: its version, and the title of a video. */
const exec = (version: string): Exec => async (command, args) => (args.at(-1) === "--version" ? ok(`${version}\n`) : args.includes("--simulate") ? ok("Some title\n") : { code: 1, out: "", err: `unexpected ${command}` });
const deps = (over: Partial<SelftestDeps> = {}): SelftestDeps => ({ get, exec: exec("2026.10.26"), reachable: async () => true, latestYtDlp: async () => "2026.10.26", now: new Date("2026-11-02T00:00:00Z"), ...over });
const targets = { extract: [GOOD_PAGE], l2: [GOOD_VIDEO_PAGE], l3: ["https://www.youtube.com/watch?v=jNQXAC9IVRw"] };
const statusOf = (checks: Awaited<ReturnType<typeof runSelftest>>, name: string, url?: string) => checks.find(c => c.name === name && (url === undefined || c.url === url))!.status;

test("when every layer works, every check is ok", async () => {
  const checks = await runSelftest(targets, deps());
  assert.deepEqual(checks.map(c => c.status), ["ok", "ok", "ok", "ok"]);
  assert.deepEqual(checks.map(c => c.layer), [0, 2, 3, 3]);
});

test("a dead address fails its own layer and the other layers are still tried", async () => {
  const dead = `https://gone.example/video-${tag()}`;
  const checks = await runSelftest({ extract: [GOOD_PAGE], l2: [dead, GOOD_VIDEO_PAGE], l3: targets.l3 }, deps());
  assert.equal(statusOf(checks, "L2 網頁檔案", dead), "fail");
  assert.match(checks.find(c => c.url === dead)!.detail, /HTTP 404/);
  assert.equal(statusOf(checks, "L2 網頁檔案", GOOD_VIDEO_PAGE), "ok");
  assert.equal(statusOf(checks, "原文擷取"), "ok");
  assert.equal(statusOf(checks, "L3 下載器（yt-dlp）"), "ok");
});

test("a page whose media can no longer be read, and a page with no media, fail as L2", async () => {
  assert.equal(statusOf(await runSelftest({ extract: [], l2: [GOOD_VIDEO_PAGE], l3: [] }, deps({ reachable: async () => false })), "L2 網頁檔案"), "fail");
  const noMedia = await runSelftest({ extract: [], l2: [GOOD_PAGE], l3: [] }, deps());
  assert.equal(noMedia[0]!.status, "fail");
  assert.match(noMedia[0]!.detail, /找不到影片或 GIF/);
});

test("an outdated yt-dlp is called out with how to update it", async () => {
  const checks = await runSelftest(targets, deps({ exec: exec("2026.08.01"), latestYtDlp: async () => "2026.10.26" }));
  const version = checks.find(c => c.name === "yt-dlp 版本")!;
  assert.equal(version.status, "warn");
  assert.match(version.detail, /2026\.08\.01.*2026\.10\.26/);
  assert.match(version.detail, /pip install -U yt-dlp/);
  const stale = (await runSelftest(targets, deps({ exec: exec("2026.08.01"), latestYtDlp: async () => null }))).find(c => c.name === "yt-dlp 版本")!;
  assert.equal(stale.status, "warn", "too old to trust even when the newest version cannot be looked up");
  assert.equal((await runSelftest(targets, deps({ exec: exec("2026.10.20"), latestYtDlp: async () => null }))).find(c => c.name === "yt-dlp 版本")!.status, "ok");
});

test("without yt-dlp the downloader layer fails with how to install it", async () => {
  const missing: Exec = async () => ({ code: "ENOENT", out: "", err: "not found" });
  const checks = await runSelftest(targets, deps({ exec: missing }));
  assert.equal(statusOf(checks, "L3 下載器（yt-dlp）"), "fail");
  assert.match(checks.find(c => c.layer === 3)!.detail, /pip install yt-dlp/);
  assert.equal(checks.some(c => c.name === "yt-dlp 版本"), false);
});

test("versions compare as dates with an optional build number", () => {
  assert.ok(compareVersions("2026.08.01", "2026.10.26") < 0);
  assert.ok(compareVersions("2026.10.26.123456", "2026.10.26") > 0);
  assert.equal(compareVersions("v2026.10.26", "2026.10.26"), 0);
});

test("the stats are the real attempts of the last 30 days: self-tests and older ones are not counted", async () => {
  const now = new Date("2026-11-02T12:00:00Z");
  const day = (n: number) => new Date(now.getTime() - n * 86_400_000);
  const u = (n: number) => `https://stats.example/${tag()}-${n}`;
  await recordAttempts([{ layer: 2, url: u(1), ok: true }, { layer: 2, url: u(2), ok: true }, { layer: 2, url: u(3), ok: true }, { layer: 2, url: u(4), ok: false, reason: "HTTP 403" }], "download", day(3));
  await recordAttempts([{ layer: 3, url: u(5), ok: true }, { layer: 3, url: u(6), ok: false, reason: "LOGIN" }], "download", day(10));
  await recordAttempts([{ layer: 3, url: u(7), ok: false }, { layer: 2, url: u(8), ok: false }], "download", day(40));
  await recordAttempts([{ layer: 3, url: u(9), ok: false }], "selftest", day(1));
  const stats = await fetchStats(30, now);
  const l2 = stats.find(s => s.layer === 2)!;
  const l3 = stats.find(s => s.layer === 3)!;
  // The database holds more (other tests, older rows); count what the stats must have counted.
  const [{ n: db2 }] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM fetch_attempts WHERE layer = 2 AND kind = 'download' AND attempted_at > ${day(30)} AND attempted_at <= ${now}`;
  const [{ n: dbOk3 }] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM fetch_attempts WHERE layer = 3 AND kind = 'download' AND ok AND attempted_at > ${day(30)} AND attempted_at <= ${now}`;
  assert.equal(l2.attempts, db2);
  assert.equal(l3.ok, dbOk3);
  assert.deepEqual([l2.attempts, l2.ok, Math.round(l2.rate * 100)], [4, 3, 75]);
  assert.deepEqual([l3.attempts, l3.ok, Math.round(l3.rate * 100)], [2, 1, 50]);
  assert.equal(l2.name, "L2 網頁檔案");
});

test("what the layers try is recorded: a download that fails is an attempt that failed", async () => {
  const added = await addSource(`https://www.youtube.com/watch?v=attempt${tag().replace(/[^A-Za-z0-9]/g, "").slice(0, 4)}`, { title: `紀錄 ${tag()}`, queue: false });
  await writeInbox();
  const t = await openTopic({ candidateIds: [`cand_${added.articleId}`], formats: ["reel"] });
  const failing: Exec = async (_c, args) => (args.at(-1) === "--version" ? ok("1.0") : { code: 1, out: "", err: "ERROR: Sign in to confirm you're not a bot" });
  await fetchLayer3(t.topicId, { exec: failing, get: async (url) => ({ status: 404, url, type: "", text: "" }), cookiesFile: null });
  const rows = await sql<{ layer: number; kind: string; ok: boolean; reason: string }[]>`SELECT layer, kind, ok, reason FROM fetch_attempts WHERE url LIKE ${"%attempt%"} AND layer = 3`;
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0]!.kind, rows[0]!.ok], ["download", false]);
  assert.match(rows[0]!.reason, /登入/);
});

test("the fixed addresses file lists a layer each and loads", async () => {
  const t = await loadTargets(SELFTEST_FILE);
  assert.ok(t.extract.length >= 1 && t.l2.length >= 1 && t.l3.length >= 1);
  assert.ok([...t.extract, ...t.l2, ...t.l3].every(u => /^https:\/\//.test(u)));
});
