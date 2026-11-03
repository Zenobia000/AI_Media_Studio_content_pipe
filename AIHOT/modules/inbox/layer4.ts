// L4 (FET-FR-006): when no other layer gave a source's page a video and the topic is a reel, the page is opened in a
// browser, scrolled and recorded for a few seconds (1080p, 30 fps MP4). One script for every page; it records what any
// visitor sees, so robots.txt is read first like everywhere else, and a page that does not load or records black is a gap,
// never an empty video.
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import { recordAttempts } from "./attempts.ts";
import { ffmpeg } from "./ffmpeg.ts";
import { robotsAllows } from "./layer2.ts";
import { defaultGet, type Get } from "./source-extract.ts";
import { workspaceDir } from "./workspace.ts";

export const RECORD_SECONDS = 20;
const LOAD_TIMEOUT_MS = 30_000;
/** A recording whose picture is black for this share of its length is thrown away. */
const MAX_BLACK_SHARE = 0.5;

/** Records `url` for `seconds` into a video file at `out` (any container the next step can read); throws when the page does not load. */
export type Recorder = (url: string, out: string, seconds: number) => Promise<void>;

export const browserRecord: Recorder = async (url, out, seconds) => {
  // playwright-core is only needed where recording is wanted, so it is not loaded for anything else.
  const { chromium } = (await import("playwright-core")) as typeof import("playwright-core");
  const dir = await mkdtemp(path.join(tmpdir(), "l4-"));
  const browser = await chromium.launch({ channel: "chromium" });
  try {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, recordVideo: { dir, size: { width: 1920, height: 1080 } }, reducedMotion: "no-preference" });
    const page = await context.newPage();
    await page.goto(url, { waitUntil: "load", timeout: LOAD_TIMEOUT_MS });
    await page.waitForTimeout(1500);
    // Scroll evenly down the page over the recording, back to the top if the page is shorter than the time.
    const steps = Math.max(1, Math.floor((seconds * 1000 - 1500) / 500));
    for (let i = 0; i < steps; i++) {
      // A string, because this runs in the page and the backend is not compiled with the DOM types.
      await page.evaluate(`window.scrollTo({ top: ((document.documentElement.scrollHeight - window.innerHeight) * ${i + 1}) / ${steps}, behavior: "smooth" })`);
      await page.waitForTimeout(500);
    }
    const video = page.video();
    await context.close();
    await copyFile(await video!.path(), out);
  } finally {
    await browser.close();
    await rm(dir, { recursive: true, force: true });
  }
};

/** Converts a recording to the pipeline's shape (1080p, 30 fps, H.264, yuv420p, no sound) and says how much of it is black. */
export async function toMp4(input: string, output: string): Promise<{ blackShare: number }> {
  await ffmpeg(["-i", input, "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", "-movflags", "+faststart", output]);
  const report = await ffmpeg(["-i", output, "-vf", "blackdetect=d=0.2:pix_th=0.1", "-an", "-f", "null", "-"]);
  const seconds = /Duration: (\d+):(\d+):(\d+\.\d+)/.exec(report);
  const length = seconds ? Number(seconds[1]) * 3600 + Number(seconds[2]) * 60 + Number(seconds[3]) : 0;
  const black = [...report.matchAll(/black_duration:(\d+(?:\.\d+)?)/g)].reduce((sum, m) => sum + Number(m[1]), 0);
  return { blackShare: length > 0 ? Math.min(1, black / length) : 1 };
}

export interface Layer4Result {
  topicId: string;
  recorded: Array<{ path: string; url: string; sourceId: string }>;
  /** Sources not recorded, with why: a source that already has a video, a rule, or a failure. */
  skipped: Array<{ sourceId: string; url: string; reason: string; failed: boolean }>;
}

export interface Layer4Options {
  record?: Recorder;
  get?: Get;
  seconds?: number;
  /** Sources that already have a video from another layer. */
  haveVideo?: ReadonlySet<string>;
  /** The formats the topic asks for; L4 runs only when "reel" is one of them. Read from the topic when absent. */
  formats?: readonly string[];
}

export async function fetchLayer4(topicId: string, opts: Layer4Options = {}): Promise<Layer4Result> {
  const record = opts.record ?? browserRecord;
  const get = opts.get ?? defaultGet;
  const seconds = opts.seconds ?? RECORD_SECONDS;
  const result: Layer4Result = { topicId, recorded: [], skipped: [] };
  const topicDir = path.join(workspaceDir(), "topics", topicId);
  const formats = opts.formats ?? (await readdir(path.join(topicDir, "contents")).catch(() => [] as string[])).map(n => n.split("-")[0]!);
  if (!formats.includes("reel")) return result;

  const rawDir = path.join(topicDir, "media", "raw");
  const sources = await sql<{ source_id: string; url: string }[]>`SELECT source_id, url FROM topic_sources WHERE topic_id = ${topicId} AND url IS NOT NULL ORDER BY source_id`;
  const robots = new Map<string, string>();
  await sql`DELETE FROM topic_fetch_gaps WHERE topic_id = ${topicId} AND layer = 4`;
  for (const source of sources) {
    const skip = async (reason: string, failed: boolean) => {
      result.skipped.push({ sourceId: source.source_id, url: source.url, reason, failed });
      if (failed) {
        await sql`INSERT INTO topic_fetch_gaps (topic_id, layer, need, reason, suggestion) VALUES (${topicId}, 4, 'visual', ${`${source.url}：${reason}`}, '自己錄 30 秒操作畫面放進 media/raw/') ON CONFLICT DO NOTHING`;
      }
    };
    if (opts.haveVideo?.has(source.source_id)) {
      result.skipped.push({ sourceId: source.source_id, url: source.url, reason: "已有影片，不錄影", failed: false });
      continue;
    }
    if (!/^https?:/i.test(source.url)) continue;
    const origin = new URL(source.url).origin;
    if (!robots.has(origin)) {
      const page = await get(`${origin}/robots.txt`).catch(() => null);
      robots.set(origin, page && page.status === 200 ? page.text : "");
    }
    if (!robotsAllows(robots.get(origin)!, new URL(source.url).pathname)) {
      await skip("robots.txt 不允許讀取這個頁面", false);
      continue;
    }
    const work = await mkdtemp(path.join(tmpdir(), "l4-out-"));
    try {
      const raw = path.join(work, "raw.webm");
      const mp4 = path.join(work, "out.mp4");
      try {
        await record(source.url, raw, seconds);
      } catch (error) {
        await skip(`頁面沒有載入（${(error as Error).message.split("\n")[0]!.slice(0, 100)}）`, true);
        continue;
      }
      const { blackShare } = await toMp4(raw, mp4);
      if (blackShare > MAX_BLACK_SHARE) {
        await skip(`錄到的畫面大半是黑的（${Math.round(blackShare * 100)}%）`, true);
        continue;
      }
      const sha = createHash("sha256").update(await readFile(mp4)).digest("hex");
      await mkdir(rawDir, { recursive: true });
      const dest = path.join(rawDir, `l4-${sha.slice(0, 8)}.mp4`);
      await copyFile(mp4, dest);
      const rel = path.relative(topicDir, dest).split(path.sep).join("/");
      await sql`
        INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
        VALUES (${topicId}, ${`ast_${sha.slice(0, 12)}`}, ${rel}, 'video', 'third_party', ${source.source_id}, ${source.url}, 4, 'playwright', ${sha})
        ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL`;
      result.recorded.push({ path: rel, url: source.url, sourceId: source.source_id });
    } catch (error) {
      await skip((error as Error).message.slice(0, 120), true);
    } finally {
      await rm(work, { recursive: true, force: true });
    }
  }
  await recordAttempts(result.recorded.map(r => ({ layer: 4, url: r.url, ok: true })));
  await recordAttempts(result.skipped.filter(s => s.failed).map(s => ({ layer: 4, url: s.url, ok: false, reason: s.reason })));
  return result;
}
