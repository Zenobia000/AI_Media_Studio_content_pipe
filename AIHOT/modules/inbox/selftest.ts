// Self-test of the fetch layers (FET-FR-010): a fixed address for each layer, run one after another, each on its own, so a
// layer that no longer works shows as failed and the others are still tried. The downloader's version is checked too.
// The addresses are in selftest.json next to this file; change them when a page goes away.
import { readFile } from "node:fs/promises";
import path from "node:path";
import { guardedFetch } from "@aihot/backend/lib/http-fetch";
import { recordAttempts } from "./attempts.ts";
import { findMedia } from "./layer2.ts";
import { defaultExec, explain, tool, type Exec } from "./layer3.ts";
import { defaultGet, extractSource, wordCount, type Get } from "./source-extract.ts";

export interface SelftestTargets {
  extract: string[];
  l2: string[];
  l3: string[];
}

export interface SelftestCheck {
  /** The fetch layer (0 is the source text). */
  layer: number;
  name: string;
  url?: string;
  status: "ok" | "warn" | "fail";
  detail: string;
}

export interface SelftestDeps {
  get?: Get;
  exec?: Exec;
  /** Whether the first bytes of a file can be read. */
  reachable?: (url: string) => Promise<boolean>;
  /** The newest yt-dlp release, or null when it cannot be learned. */
  latestYtDlp?: () => Promise<string | null>;
  now?: Date;
}

export const SELFTEST_FILE = path.join(import.meta.dirname, "selftest.json");
/** A yt-dlp older than this many days is called out even when the newest release cannot be looked up. */
const STALE_DAYS = 45;

export async function loadTargets(file = SELFTEST_FILE): Promise<SelftestTargets> {
  const t = JSON.parse(await readFile(file, "utf8")) as Partial<SelftestTargets>;
  return { extract: t.extract ?? [], l2: t.l2 ?? [], l3: t.l3 ?? [] };
}

const defaultReachable = async (url: string): Promise<boolean> => {
  const res = await guardedFetch(url, { headers: { range: "bytes=0-1023" }, maxBytes: 4096, truncate: true, timeoutMs: 20_000 });
  return res.status === 200 || res.status === 206;
};

const defaultLatest = async (get: Get): Promise<string | null> => {
  const page = await get("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", { accept: "application/vnd.github+json" }).catch(() => null);
  return page && page.status === 200 ? (JSON.parse(page.text) as { tag_name?: string }).tag_name ?? null : null;
};

/** Compares yt-dlp versions ("2026.10.26", "2026.10.26.123456"): negative when `a` is older than `b`. */
export function compareVersions(a: string, b: string): number {
  const [x, y] = [a, b].map(v => v.replace(/^v/, "").split(".").map(Number));
  for (let i = 0; i < Math.max(x!.length, y!.length); i++) {
    const d = (x![i] ?? 0) - (y![i] ?? 0);
    if (d) return d;
  }
  return 0;
}

const versionDate = (v: string): Date | null => {
  const m = /^v?(\d{4})\.(\d{2})\.(\d{2})/.exec(v);
  return m ? new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))) : null;
};

export async function runSelftest(targets: SelftestTargets, deps: SelftestDeps = {}): Promise<SelftestCheck[]> {
  const get = deps.get ?? defaultGet;
  const exec = deps.exec ?? defaultExec;
  const reachable = deps.reachable ?? defaultReachable;
  const now = deps.now ?? new Date();
  const checks: SelftestCheck[] = [];
  const check = async (layer: number, name: string, url: string | undefined, fn: () => Promise<string>) => {
    try {
      checks.push({ layer, name, url, status: "ok", detail: await fn() });
    } catch (error) {
      checks.push({ layer, name, url, status: "fail", detail: (error as Error).message.slice(0, 200) });
    }
  };

  for (const url of targets.extract) {
    await check(0, "原文擷取", url, async () => {
      const x = await extractSource(url, { get });
      return `${x.method}，${wordCount(x.markdown)} 字`;
    });
  }
  for (const url of targets.l2) {
    await check(2, "L2 網頁檔案", url, async () => {
      const page = await get(url);
      if (page.status !== 200) throw new Error(`頁面回 HTTP ${page.status}`);
      const media = findMedia(page.text, page.url).filter(m => m.kind !== "image");
      if (!media.length) throw new Error("頁面上找不到影片或 GIF（頁面可能改版）");
      if (!(await reachable(media[0]!.url))) throw new Error(`找到 ${media.length} 個媒體，但第一個讀不到：${media[0]!.url.slice(0, 80)}`);
      return `找到 ${media.length} 個媒體，可讀取`;
    });
  }
  const ytdlp = await tool(exec, { bin: "yt-dlp", module: "yt_dlp" });
  for (const url of targets.l3) {
    await check(3, "L3 下載器（yt-dlp）", url, async () => {
      if (!ytdlp) throw new Error("找不到 yt-dlp。安裝：`python -m pip install yt-dlp`");
      const r = await exec(ytdlp[0]!, [...ytdlp.slice(1), "--simulate", "--no-warnings", "--no-playlist", "--print", "%(title)s", url]);
      if (r.code !== 0) throw new Error(explain(`${r.err}\n${r.out}`, "yt-dlp").message);
      return `讀得到影片：${r.out.trim().split("\n")[0]!.slice(0, 60)}`;
    });
  }

  // The downloaders break when sites change; the fix is nearly always a newer version.
  if (ytdlp) {
    const installed = (await exec(ytdlp[0]!, [...ytdlp.slice(1), "--version"])).out.trim();
    const latest = await (deps.latestYtDlp ?? (() => defaultLatest(get)))();
    const update = "更新：`python -m pip install -U yt-dlp`";
    const born = versionDate(installed);
    const age = born ? Math.floor((now.getTime() - born.getTime()) / 86_400_000) : null;
    if (latest && compareVersions(installed, latest) < 0) checks.push({ layer: 3, name: "yt-dlp 版本", status: "warn", detail: `已安裝 ${installed}，最新是 ${latest}。${update}` });
    else if (!latest && age !== null && age > STALE_DAYS) checks.push({ layer: 3, name: "yt-dlp 版本", status: "warn", detail: `已安裝 ${installed}（${age} 天前的版本），查不到最新版。${update}` });
    else checks.push({ layer: 3, name: "yt-dlp 版本", status: "ok", detail: latest ? `${installed}（已是最新）` : `${installed}（查不到最新版，但還不算舊）` });
  }
  await recordAttempts(checks.filter(c => c.url).map(c => ({ layer: c.layer, url: c.url!, ok: c.status !== "fail", reason: c.status === "fail" ? c.detail : null })), "selftest", now);
  return checks;
}
