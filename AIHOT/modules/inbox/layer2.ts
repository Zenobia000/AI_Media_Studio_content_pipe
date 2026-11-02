// L2 (FET-FR-003): the media a page gives directly: <video> and <source>, og:video, twitter:player:stream, GIFs, the
// main picture, and the media a repository's README links. Nothing is rendered or recorded, only downloaded: the
// robots.txt of the site is read first, a host is asked at most once a second, a file over 500 MB is skipped and
// left as a gap, and a picture with a side under 400 px (a logo, an icon) is dropped.
import { createHash } from "node:crypto";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { guardedFetch } from "@aihot/backend/lib/http-fetch";
import { sql } from "@aihot/backend/db";
import { siteIdentityForUrl } from "@aihot/backend/lib/url";
import { parseHTML } from "linkedom";
import { probe } from "./assets.ts";
import { defaultGet, type Get } from "./source-extract.ts";
import { workspaceDir } from "./workspace.ts";

export const MAX_BYTES = 500 * 1024 * 1024;
export const MIN_SIDE = 400;
/** A page can name dozens of clips; the first ten (videos before GIFs before pictures) are enough to choose from. */
export const MAX_PER_PAGE = 10;
const ROBOTS_NAMES = ["mediahotbot", "*"];

export interface MediaCandidate {
  url: string;
  kind: "video" | "gif" | "image";
  /** What gave it away: video-tag, source-tag, og:video, twitter:player, gif-img, og:image, link, readme. */
  via: string;
}

export interface Binary {
  status: number;
  url: string;
  type: string;
  /** The size the server states, when it does. */
  length: number | null;
  body: Buffer;
}
export type GetBinary = (url: string, opts: { head?: boolean; maxBytes: number }) => Promise<Binary>;

export const defaultGetBinary: GetBinary = async (url, opts) => {
  const res = await guardedFetch(url, { method: opts.head ? "HEAD" : "GET", maxBytes: opts.maxBytes, timeoutMs: opts.head ? 20_000 : 300_000 });
  const length = Number(res.headers.get("content-length"));
  return { status: res.status, url: res.url, type: res.headers.get("content-type") ?? "", length: Number.isFinite(length) && length > 0 ? length : null, body: res.body };
};

const VIDEO_EXT = /\.(mp4|webm|mov|m4v)(\?|#|$)/i;
const GIF_EXT = /\.gif(\?|#|$)/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|avif)(\?|#|$)/i;
/** GitHub gives the videos of a README an address with no extension. */
const GITHUB_ASSET = /^https:\/\/github\.com\/user-attachments\/assets\/[0-9a-f-]{36}$/i;

function absolute(href: string | null | undefined, base: string): string | null {
  if (!href) return null;
  try {
    const url = new URL(href.trim(), base);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function classify(url: string): MediaCandidate["kind"] | null {
  if (VIDEO_EXT.test(url) || GITHUB_ASSET.test(url)) return "video";
  if (GIF_EXT.test(url)) return "gif";
  if (IMAGE_EXT.test(url)) return "image";
  return null;
}

type El = { getAttribute(name: string): string | null };
/** linkedom types its node lists loosely; these are elements. */
const all = (document: { querySelectorAll(selector: string): Iterable<unknown> }, selector: string) => Array.from(document.querySelectorAll(selector)) as El[];

/** The media a web page names. */
export function findMedia(html: string, pageUrl: string): MediaCandidate[] {
  const { document } = parseHTML(html);
  const out = new Map<string, MediaCandidate>();
  const add = (href: string | null | undefined, via: string, kind?: MediaCandidate["kind"]) => {
    const url = absolute(href, pageUrl);
    const k = url ? (kind ?? classify(url)) : null;
    if (url && k && !out.has(url)) out.set(url, { url, kind: k, via });
  };
  for (const v of all(document, "video[src]")) add(v.getAttribute("src"), "video-tag", "video");
  for (const s of all(document, "video source[src], source[src]")) {
    if (/^video\//i.test(s.getAttribute("type") ?? "") || VIDEO_EXT.test(s.getAttribute("src") ?? "")) add(s.getAttribute("src"), "source-tag", "video");
  }
  for (const name of ["og:video", "og:video:url", "og:video:secure_url"]) add(document.querySelector(`meta[property="${name}"]`)?.getAttribute("content"), "og:video", "video");
  add(document.querySelector('meta[name="twitter:player:stream"], meta[property="twitter:player:stream"]')?.getAttribute("content"), "twitter:player", "video");
  for (const img of all(document, "img")) {
    const src = img.getAttribute("src") ?? img.getAttribute("data-src");
    if (src && GIF_EXT.test(src)) add(src, "gif-img", "gif");
  }
  for (const a of all(document, "a[href]")) {
    const href = a.getAttribute("href");
    if (href && (VIDEO_EXT.test(href) || GIF_EXT.test(href))) add(href, "link");
  }
  add(document.querySelector('meta[property="og:image"], meta[property="og:image:secure_url"]')?.getAttribute("content"), "og:image", "image");
  return [...out.values()];
}

/** The media a README (Markdown) links or embeds. */
export function findMediaInMarkdown(markdown: string, baseUrl: string): MediaCandidate[] {
  const out = new Map<string, MediaCandidate>();
  for (const m of markdown.matchAll(/(?:\]\(|src=["']|href=["']|^|\s)(https?:\/\/[^\s)"'<>]+|(?:\.{0,2}\/)?[\w./%-]+\.(?:mp4|webm|mov|gif))/gim)) {
    const url = absolute(m[1], baseUrl);
    const kind = url ? classify(url) : null;
    if (url && kind && kind !== "image" && !out.has(url)) out.set(url, { url, kind, via: "readme" });
  }
  return [...out.values()];
}

/** Whether robots.txt lets the crawler fetch a path: the longest matching rule wins, Allow over Disallow on a tie. */
export function robotsAllows(robots: string, pathname: string): boolean {
  let applies = false;
  let best: { len: number; allow: boolean } | null = null;
  let group: string[] = [];
  let inRules = false;
  for (const raw of robots.split(/\r?\n/)) {
    const line = raw.replace(/#.*/, "").trim();
    const [field, ...rest] = line.split(":");
    const value = rest.join(":").trim();
    if (!field || !line.includes(":")) continue;
    const key = field.toLowerCase();
    if (key === "user-agent") {
      if (inRules) group = [];
      inRules = false;
      group.push(value.toLowerCase());
      applies = group.some(g => ROBOTS_NAMES.includes(g));
    } else if ((key === "disallow" || key === "allow") && applies) {
      inRules = true;
      if (!value) continue;
      const pattern = new RegExp(`^${value.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\\\$$/, "$")}`);
      if (pattern.test(pathname) && (!best || value.length > best.len || (value.length === best.len && key === "allow"))) best = { len: value.length, allow: key === "allow" };
    }
  }
  return best ? best.allow : true;
}

const EXTENSION_BY_TYPE: Record<string, string> = { "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov", "image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp" };

export interface Layer2Result {
  topicId: string;
  downloaded: Array<{ path: string; url: string; kind: MediaCandidate["kind"]; method: string }>;
  skipped: Array<{ url: string; reason: string }>;
}

export interface Layer2Options {
  get?: Get;
  getBinary?: GetBinary;
  /** Pause between two requests to the same host. */
  delayMs?: number;
  maxBytes?: number;
  maxPerPage?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}

/** Reads media off every source page of a topic. */
export async function fetchLayer2(topicId: string, opts: Layer2Options = {}): Promise<Layer2Result> {
  const get = opts.get ?? defaultGet;
  const getBinary = opts.getBinary ?? defaultGetBinary;
  const delayMs = opts.delayMs ?? 1000;
  const maxBytes = opts.maxBytes ?? MAX_BYTES;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
  const result: Layer2Result = { topicId, downloaded: [], skipped: [], };
  const rawDir = path.join(workspaceDir(), "topics", topicId, "media", "raw");
  const sources = await sql<{ source_id: string; url: string }[]>`SELECT source_id, url FROM topic_sources WHERE topic_id = ${topicId} AND url IS NOT NULL ORDER BY source_id`;
  const gaps = new Map<string, { need: string; reason: string; suggestion: string }>();
  const robots = new Map<string, string>();
  const lastAt = new Map<string, number>();

  const wait = async (host: string) => {
    const gap = (lastAt.get(host) ?? 0) + delayMs - (opts.now?.() ?? Date.now());
    if (gap > 0) await sleep(gap);
    lastAt.set(host, opts.now?.() ?? Date.now());
  };
  const allowed = async (url: string): Promise<boolean> => {
    const origin = new URL(url).origin;
    if (!robots.has(origin)) {
      await wait(new URL(url).host);
      const page = await get(`${origin}/robots.txt`).catch(() => null);
      robots.set(origin, page && page.status === 200 ? page.text : "");
    }
    return robotsAllows(robots.get(origin)!, new URL(url).pathname);
  };

  for (const source of sources) {
    let candidates: MediaCandidate[];
    try {
      if (!(await allowed(source.url))) {
        result.skipped.push({ url: source.url, reason: "robots.txt 不允許讀取這個頁面" });
        continue;
      }
      candidates = await candidatesOf(source.url, get, wait);
    } catch (error) {
      result.skipped.push({ url: source.url, reason: `頁面讀不到：${(error as Error).message.slice(0, 100)}` });
      continue;
    }
    const order = { video: 0, gif: 1, image: 2 };
    const ranked = [...candidates].sort((a, b) => order[a.kind] - order[b.kind]);
    const cap = opts.maxPerPage ?? MAX_PER_PAGE;
    if (ranked.length > cap) result.skipped.push({ url: source.url, reason: `頁面列出 ${ranked.length} 個媒體，只取前 ${cap} 個（影片優先）` });
    for (const media of ranked.slice(0, cap)) {
      const skip = (reason: string) => result.skipped.push({ url: media.url, reason });
      try {
        if (!(await allowed(media.url))) {
          skip("robots.txt 不允許下載這個檔案");
          continue;
        }
        await wait(new URL(media.url).host);
        // The size a server states is a shortcut, not a gate: some refuse HEAD (a signed address allows only GET).
        const head = await getBinary(media.url, { head: true, maxBytes }).catch(() => null);
        const limit = `${Math.round(maxBytes / 1024 / 1024)} MB`;
        const tooBig = () => {
          gaps.set(media.url, { need: "media", reason: `${media.url} 超過 ${limit}，沒有下載`, suggestion: "手動下載需要的片段，或錄一段較短的畫面，放進 media/raw/" });
          skip(`超過 ${limit}`);
        };
        if (head && head.status < 400 && head.length !== null && head.length > maxBytes) {
          tooBig();
          continue;
        }
        await wait(new URL(media.url).host);
        let file: Binary;
        try {
          file = await getBinary(media.url, { maxBytes });
        } catch (error) {
          if (/too large/i.test((error as Error).message)) tooBig();
          else skip((error as Error).message.slice(0, 120));
          continue;
        }
        if (file.status !== 200 || !file.body.length) {
          skip(`HTTP ${file.status}`);
          continue;
        }
        const kind: MediaCandidate["kind"] = /^video\//.test(file.type) ? "video" : file.type === "image/gif" ? "gif" : /^image\//.test(file.type) ? "image" : media.kind;
        const ext = EXTENSION_BY_TYPE[file.type.split(";")[0]!.trim()] ?? path.extname(new URL(media.url).pathname) ?? "";
        const sha = createHash("sha256").update(file.body).digest("hex");
        await mkdir(rawDir, { recursive: true });
        const temp = path.join(rawDir, `.l2-${sha.slice(0, 8)}.tmp`);
        await writeFile(temp, file.body);
        if (kind !== "video") {
          const size = await probe(temp).catch(() => null);
          if (!size || (size.width ?? 0) < MIN_SIDE || (size.height ?? 0) < MIN_SIDE) {
            await rm(temp, { force: true });
            skip(`圖片太小（${size?.width ?? "?"}×${size?.height ?? "?"}，需要兩邊都至少 ${MIN_SIDE} px）`);
            continue;
          }
        }
        const dest = path.join(rawDir, `l2-${sha.slice(0, 8)}${ext || (kind === "video" ? ".mp4" : ".bin")}`);
        await rename(temp, dest);
        const rel = path.relative(path.join(workspaceDir(), "topics", topicId), dest).split(path.sep).join("/");
        const method = media.via === "og:image" || media.via === "gif-img" ? "html-image" : media.via === "readme" ? "readme-media" : "html-video";
        await sql`
          INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
          VALUES (${topicId}, ${`ast_${sha.slice(0, 12)}`}, ${rel}, ${kind}, 'official', ${source.source_id}, ${media.url}, 2, ${method}, ${sha})
          ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL`;
        result.downloaded.push({ path: rel, url: media.url, kind, method });
      } catch (error) {
        skip((error as Error).message.slice(0, 120));
      }
    }
  }

  await sql`DELETE FROM topic_fetch_gaps WHERE topic_id = ${topicId} AND layer = 2`;
  for (const g of gaps.values()) await sql`INSERT INTO topic_fetch_gaps (topic_id, layer, need, reason, suggestion) VALUES (${topicId}, 2, ${g.need}, ${g.reason}, ${g.suggestion}) ON CONFLICT DO NOTHING`;
  return result;
}

async function candidatesOf(url: string, get: Get, wait: (host: string) => Promise<void>): Promise<MediaCandidate[]> {
  const site = siteIdentityForUrl(url);
  await wait(new URL(url).host);
  if (site?.startsWith("github:")) {
    const [owner, repo] = new URL(url).pathname.split("/").filter(Boolean) as [string, string];
    const readme = await get(`https://api.github.com/repos/${owner}/${repo}/readme`, { accept: "application/vnd.github.raw+json" });
    if (readme.status !== 200) throw new Error(`README HTTP ${readme.status}`);
    return findMediaInMarkdown(readme.text, `https://raw.githubusercontent.com/${owner}/${repo}/HEAD/`);
  }
  const page = await get(url, { "accept-language": "zh-TW,zh;q=0.9,en;q=0.8" });
  if (page.status !== 200 || !/html/.test(page.type)) throw new Error(`HTTP ${page.status}`);
  return findMedia(page.text, page.url);
}
