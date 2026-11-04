// L1 (FET-FR-005): the media a platform hands over itself, the steadiest way to a video. An X post: the rendition with the
// highest bitrate among its mp4 files, from the post as the API gives it. A Threads post: the video its page names (og:video).
// A source that gets a video here is not asked again by the layers below; a source that does not simply falls through to them.
import { createHash } from "node:crypto";
import { mkdir, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import { credential } from "@aihot/backend/config";
import { getTweet, type SdMedia, type SdTweet } from "@aihot/backend/providers/socialdata";
import { parseHTML } from "linkedom";
import { recordAttempts } from "./attempts.ts";
import { defaultGetBinary, MAX_BYTES, type GetBinary } from "./layer2.ts";
import { defaultGet, type Get } from "./source-extract.ts";
import { workspaceDir } from "./workspace.ts";

export type LookupTweet = (id: string) => Promise<SdTweet | null>;

export interface Layer1Result {
  topicId: string;
  downloaded: Array<{ path: string; url: string; kind: "video"; method: "x-api" | "threads-og"; sourceId: string }>;
  /** Sources that gave nothing here, with why; they go on to the next layer. */
  skipped: Array<{ url: string; reason: string; failed: boolean }>;
}

export interface Layer1Options {
  lookup?: LookupTweet;
  get?: Get;
  getBinary?: GetBinary;
  maxBytes?: number;
  /** Whether the post lookup is available (a key is set); read from the credentials when absent. */
  xConfigured?: boolean;
}

const X_STATUS = /^https?:\/\/(?:www\.|mobile\.)?(?:x|twitter)\.com\/[^/]+\/status\/(\d+)/i;
const THREADS_POST = /^https?:\/\/(?:www\.)?threads\.(?:net|com)\/@[^/]+\/post\//i;

/** The mp4 with the highest bitrate among the renditions of the post's videos; null when it has none. */
export function bestMp4(media: readonly SdMedia[]): string | null {
  const files = media.filter(m => m.type !== "photo")
    .flatMap(m => m.video_info?.variants ?? [])
    .filter(v => v.content_type === "video/mp4" && typeof v.bitrate === "number");
  files.sort((a, b) => b.bitrate! - a.bitrate!);
  return files[0]?.url ?? null;
}

export function threadsVideo(html: string, base: string): string | null {
  const { document } = parseHTML(html);
  for (const selector of ['meta[property="og:video"]', 'meta[property="og:video:secure_url"]', 'meta[name="twitter:player:stream"]']) {
    const content = (document.querySelector(selector) as { getAttribute(n: string): string | null } | null)?.getAttribute("content");
    if (!content) continue;
    try {
      const url = new URL(content.trim(), base);
      if (url.protocol === "https:") return url.toString();
    } catch { /* next */ }
  }
  return null;
}

const defaultLookup: LookupTweet = id => getTweet(id, { purpose: "fetch-media", subject: `l1:${id}` });

export async function fetchLayer1(topicId: string, opts: Layer1Options = {}): Promise<Layer1Result> {
  const lookup = opts.lookup ?? defaultLookup;
  const get = opts.get ?? defaultGet;
  const getBinary = opts.getBinary ?? defaultGetBinary;
  const maxBytes = opts.maxBytes ?? MAX_BYTES;
  const xConfigured = opts.xConfigured ?? Boolean(credential("collectors", "SOCIALDATA_API_KEY"));
  const result: Layer1Result = { topicId, downloaded: [], skipped: [] };
  const topicDir = path.join(workspaceDir(), "topics", topicId);
  const rawDir = path.join(topicDir, "media", "raw");
  const sources = await sql<{ source_id: string; url: string }[]>`SELECT source_id, url FROM topic_sources WHERE topic_id = ${topicId} AND url IS NOT NULL ORDER BY source_id`;

  for (const source of sources) {
    const x = X_STATUS.exec(source.url);
    const threads = THREADS_POST.test(source.url);
    if (!x && !threads) continue;
    // Not failures: nothing was tried, so they do not count against the layer.
    const skip = (reason: string, failed: boolean) => result.skipped.push({ url: source.url, reason, failed });
    try {
      let file: string | null;
      if (x) {
        if (!xConfigured) { skip("沒有設定 X 的資料金鑰，略過 L1", false); continue; }
        const tweet = await lookup(x[1]!);
        if (!tweet) { skip("查不到這則貼文", true); continue; }
        file = bestMp4(tweet.extended_entities?.media ?? tweet.entities?.media ?? []);
        if (!file) { skip("這則貼文沒有影片", false); continue; }
      } else {
        const page = await get(source.url);
        if (page.status !== 200) { skip(`頁面 HTTP ${page.status}`, true); continue; }
        file = threadsVideo(page.text, source.url);
        if (!file) { skip("頁面沒有影片", false); continue; }
      }
      const bin = await getBinary(file, { maxBytes });
      if (bin.status !== 200 || !bin.body.length) { skip(`影片檔 HTTP ${bin.status}`, true); continue; }
      const sha = createHash("sha256").update(bin.body).digest("hex");
      await mkdir(rawDir, { recursive: true });
      const temp = path.join(rawDir, `.l1-${sha.slice(0, 8)}.tmp`);
      await writeFile(temp, bin.body);
      const dest = path.join(rawDir, `l1-${sha.slice(0, 8)}.mp4`);
      await rename(temp, dest);
      const rel = path.relative(topicDir, dest).split(path.sep).join("/");
      const method = x ? "x-api" : "threads-og";
      await sql`
        INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
        VALUES (${topicId}, ${`ast_${sha.slice(0, 12)}`}, ${rel}, 'video', 'official', ${source.source_id}, ${file}, 1, ${method}, ${sha})
        ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL`;
      result.downloaded.push({ path: rel, url: source.url, kind: "video", method, sourceId: source.source_id });
    } catch (error) {
      skip((error as Error).message.slice(0, 120), true);
    }
  }
  await recordAttempts(result.downloaded.map(d => ({ layer: 1, url: d.url, ok: true })));
  await recordAttempts(result.skipped.filter(s => s.failed).map(s => ({ layer: 1, url: s.url, ok: false, reason: s.reason })));
  return result;
}
