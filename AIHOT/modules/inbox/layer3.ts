// L3 (FET-FR-004): the downloaders for what a page does not hand over directly. yt-dlp for YouTube, X posts, Vimeo and
// the videos a page embeds, gallery-dl for picture sets. Video is taken at most 1080p and H.264. A source whose page
// L2 already gave a video is not asked again. Cookies of the person's own account may be named in YTDLP_COOKIES_FILE
// (the file never goes into version control); nothing here works around a login or a bot check, it says what is needed.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rename, rm } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { sql } from "@aihot/backend/db";
import { parseHTML } from "linkedom";
import { recordAttempts } from "./attempts.ts";
import { MAX_BYTES } from "./layer2.ts";
import { defaultGet, type Get } from "./source-extract.ts";
import { workspaceDir } from "./workspace.ts";

const run = promisify(execFile);

export interface ExecResult {
  code: number | "ENOENT";
  out: string;
  err: string;
}
export type Exec = (command: string, args: string[]) => Promise<ExecResult>;

export const defaultExec: Exec = async (command, args) => {
  try {
    const { stdout, stderr } = await run(command, args, { maxBuffer: 16 * 1024 * 1024, timeout: 15 * 60_000, windowsHide: true });
    return { code: 0, out: stdout, err: stderr };
  } catch (error) {
    const e = error as NodeJS.ErrnoException & { stdout?: string; stderr?: string; code?: number | string };
    return { code: e.code === "ENOENT" ? "ENOENT" : typeof e.code === "number" ? e.code : 1, out: e.stdout ?? "", err: e.stderr ?? e.message };
  }
};

/** A downloader may be on PATH, or only reachable as a Python module. */
export async function tool(exec: Exec, names: { bin: string; module: string }): Promise<string[] | null> {
  for (const base of [[names.bin], ["python", "-m", names.module], ["py", "-m", names.module]]) {
    const probe = await exec(base[0]!, [...base.slice(1), "--version"]);
    if (probe.code === 0) return base;
  }
  return null;
}

/** yt-dlp's format choice: up to 1080p, H.264 video with AAC audio when there is one, else the best single file up to 1080p. */
export const VIDEO_FORMAT = "bv*[height<=1080][vcodec~='^(avc|h264)']+ba[acodec~='^(mp4a|aac)']/b[height<=1080][ext=mp4]/b[height<=1080]";

const YTDLP_HOSTS = /(^|\.)(youtube\.com|youtu\.be|vimeo\.com|x\.com|twitter\.com)$/i;
const GALLERY_HOSTS = /(^|\.)(imgur\.com|flickr\.com|pinterest\.com|artstation\.com)$/i;

/** The addresses a page embeds as players: a YouTube or Vimeo iframe becomes the page of that video. */
export function embeddedVideos(html: string, base: string): string[] {
  const { document } = parseHTML(html);
  const out = new Set<string>();
  for (const frame of Array.from(document.querySelectorAll("iframe[src], iframe[data-src]")) as Array<{ getAttribute(n: string): string | null }>) {
    let src: URL;
    try {
      src = new URL(frame.getAttribute("src") ?? frame.getAttribute("data-src") ?? "", base);
    } catch {
      continue;
    }
    const youtube = /^\/embed\/([\w-]{11})/.exec(src.pathname);
    const vimeo = /^\/video\/(\d+)/.exec(src.pathname);
    if (/(^|\.)(youtube\.com|youtube-nocookie\.com)$/.test(src.hostname) && youtube) out.add(`https://www.youtube.com/watch?v=${youtube[1]}`);
    else if (src.hostname === "player.vimeo.com" && vimeo) out.add(`https://vimeo.com/${vimeo[1]}`);
  }
  return [...out];
}

export interface Failure {
  code: "JS_RUNTIME" | "LOGIN" | "OUTDATED" | "MISSING_TOOL" | "TOO_LARGE" | "FAILED";
  message: string;
}

/** What a downloader's complaint means for the person, and what to do about it. */
export function explain(err: string, downloader: "yt-dlp" | "gallery-dl"): Failure {
  const update = downloader === "yt-dlp" ? "python -m pip install -U yt-dlp" : "python -m pip install -U gallery-dl";
  if (/javascript runtime|no supported js|deno/i.test(err)) return { code: "JS_RUNTIME", message: "yt-dlp 讀 YouTube 需要 JavaScript 執行環境。請安裝 Deno（Windows：`winget install DenoLand.Deno`），或確認 Node.js 在 PATH 裡，再重試" };
  if (/sign in|log ?in|cookies|age-restricted|private|members-only|confirm you.re not a bot/i.test(err)) return { code: "LOGIN", message: "這個影片要登入才能看。若要用自己的帳號，匯出 cookies 檔並用環境變數 YTDLP_COOKIES_FILE 指定路徑；或改請人自己錄影放進 media/raw/" };
  if (/please update|out of date|outdated|unable to extract|nsig extraction|http error 403/i.test(err)) return { code: "OUTDATED", message: `${downloader} 可能太舊了，網站改版後常會失敗。更新：\`${update}\`` };
  if (/larger than max-filesize|file is larger/i.test(err)) return { code: "TOO_LARGE", message: "檔案超過 500 MB，沒有下載" };
  return { code: "FAILED", message: err.split("\n").filter(l => /error/i.test(l)).at(-1)?.slice(0, 200) ?? err.slice(-200) };
}

export interface Layer3Result {
  topicId: string;
  downloaded: Array<{ path: string; url: string; kind: "video" | "image"; method: "yt-dlp" | "gallery-dl"; sourceId: string }>;
  failed: Array<{ url: string; code: Failure["code"]; message: string }>;
  /** Sources left alone because L2 already gave them a video. */
  skipped: string[];
}

export interface Layer3Options {
  exec?: Exec;
  get?: Get;
  /** Sources whose page L2 already gave a video. */
  haveVideo?: ReadonlySet<string>;
  cookiesFile?: string | null;
}

const VIDEO_FILE = /\.(mp4|webm|mkv|mov|m4v)$/i;
const IMAGE_FILE = /\.(png|jpe?g|gif|webp)$/i;

/** Downloads, with the downloaders, the videos and picture sets of the sources of a topic. */
export async function fetchLayer3(topicId: string, opts: Layer3Options = {}): Promise<Layer3Result> {
  const exec = opts.exec ?? defaultExec;
  const get = opts.get ?? defaultGet;
  const cookies = opts.cookiesFile === undefined ? (process.env.YTDLP_COOKIES_FILE ?? null) : opts.cookiesFile;
  const result: Layer3Result = { topicId, downloaded: [], failed: [], skipped: [] };
  const rawDir = path.join(workspaceDir(), "topics", topicId, "media", "raw");
  const topicDir = path.join(workspaceDir(), "topics", topicId);
  const sources = await sql<{ source_id: string; url: string }[]>`SELECT source_id, url FROM topic_sources WHERE topic_id = ${topicId} AND url IS NOT NULL ORDER BY source_id`;
  let ytdlp: string[] | null | undefined;
  let gallery: string[] | null | undefined;

  const register = async (file: string, kind: "video" | "image", method: "yt-dlp" | "gallery-dl", sourceId: string, url: string) => {
    const body = await readFile(file);
    const sha = createHash("sha256").update(body).digest("hex");
    const dest = path.join(rawDir, `l3-${sha.slice(0, 8)}${path.extname(file).toLowerCase()}`);
    if (file !== dest) await rename(file, dest).catch(async () => rm(file, { force: true }));
    const rel = path.relative(topicDir, dest).split(path.sep).join("/");
    await sql`
      INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
      VALUES (${topicId}, ${`ast_${sha.slice(0, 12)}`}, ${rel}, ${kind}, 'third_party', ${sourceId}, ${url}, 3, ${method}, ${sha})
      ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL`;
    result.downloaded.push({ path: rel, url, kind, method, sourceId });
  };

  const video = async (url: string, sourceId: string) => {
    ytdlp ??= await tool(exec, { bin: "yt-dlp", module: "yt_dlp" });
    if (!ytdlp) {
      result.failed.push({ url, code: "MISSING_TOOL", message: "找不到 yt-dlp。安裝：`python -m pip install yt-dlp`" });
      return;
    }
    await mkdir(rawDir, { recursive: true });
    const args = [...ytdlp.slice(1), "-f", VIDEO_FORMAT, "--merge-output-format", "mp4", "--no-playlist", "--max-filesize", `${Math.round(MAX_BYTES / 1024 / 1024)}M`,
      "--restrict-filenames", "--no-progress", "--no-warnings", "-o", path.join(rawDir, ".l3-%(id)s.%(ext)s"), "--print", "after_move:filepath", ...(cookies ? ["--cookies", cookies] : []), url];
    const r = await exec(ytdlp[0]!, args);
    const file = r.out.split(/\r?\n/).map(l => l.trim()).filter(l => VIDEO_FILE.test(l)).at(-1);
    if (r.code !== 0 || !file) {
      const f = explain(`${r.err}\n${r.out}`, "yt-dlp");
      result.failed.push({ url, code: f.code, message: f.message });
      return;
    }
    await register(file, "video", "yt-dlp", sourceId, url);
  };

  const images = async (url: string, sourceId: string) => {
    gallery ??= await tool(exec, { bin: "gallery-dl", module: "gallery_dl" });
    if (!gallery) {
      result.failed.push({ url, code: "MISSING_TOOL", message: "找不到 gallery-dl。安裝：`python -m pip install gallery-dl`" });
      return;
    }
    await mkdir(rawDir, { recursive: true });
    const tmp = path.join(rawDir, `.l3-gallery-${createHash("sha1").update(url).digest("hex").slice(0, 8)}`);
    const r = await exec(gallery[0]!, [...gallery.slice(1), "-D", tmp, "--range", "1-10", "--filesize-max", `${Math.round(MAX_BYTES / 1024 / 1024)}M`, ...(cookies ? ["--cookies", cookies] : []), url]);
    const got = (await readdir(tmp).catch(() => [] as string[])).filter(n => IMAGE_FILE.test(n));
    if (r.code !== 0 && !got.length) {
      const f = explain(`${r.err}\n${r.out}`, "gallery-dl");
      result.failed.push({ url, code: f.code, message: f.message });
    }
    for (const name of got) await register(path.join(tmp, name), "image", "gallery-dl", sourceId, url);
    await rm(tmp, { recursive: true, force: true });
  };

  for (const source of sources) {
    if (opts.haveVideo?.has(source.source_id)) {
      result.skipped.push(source.source_id);
      continue;
    }
    const host = new URL(source.url).hostname;
    const wanted: Array<["video" | "images", string]> = [];
    if (YTDLP_HOSTS.test(host)) wanted.push(["video", source.url]);
    if (GALLERY_HOSTS.test(host) || /(^|\.)(x|twitter)\.com$/.test(host)) wanted.push(["images", source.url]);
    if (!wanted.length) {
      const page = await get(source.url).catch(() => null);
      if (page && page.status === 200 && /html/.test(page.type)) for (const embedded of embeddedVideos(page.text, page.url)) wanted.push(["video", embedded]);
    }
    // A post with a video is taken as that video; its pictures are looked for only when there is none.
    const gotVideo = () => result.downloaded.some(d => d.sourceId === source.source_id && d.kind === "video");
    for (const [kind, url] of wanted) {
      if (kind === "video") await video(url, source.source_id);
      else if (!gotVideo()) await images(url, source.source_id);
    }
  }
  await recordAttempts([...new Set(result.downloaded.map(d => d.url))].map(url => ({ layer: 3, url, ok: true })));
  await recordAttempts(result.failed.map(f => ({ layer: 3, url: f.url, ok: false, reason: f.message })));
  return result;
}
