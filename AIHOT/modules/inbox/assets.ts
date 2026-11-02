// assets (FET-FR-007, FET-FR-009): the media of a topic as assets.json (IF-005) and media.md. The files under media/raw,
// media/gen and media/proc are the truth; the table remembers where downloaded ones came from and which have been
// deleted. A file keeps its id (SHA-256) when moved or renamed.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { sql } from "@aihot/backend/db";
import { detectScenes, type Range, type SceneDetector } from "./scenes.ts";
import { FORMATS, OpenTopicError, replaceBlock, block } from "./topics.ts";
import { taipeiIso, workspaceDir, writeAtomic } from "./workspace.ts";

const run = promisify(execFile);

export type AssetType = "video" | "image" | "gif" | "audio" | "screenshot" | "document";
export type Origin = "official" | "self" | "third_party" | "generated" | "derived";

export interface Asset {
  asset_id: string;
  /** Relative to the topic folder. */
  path: string;
  type: AssetType;
  origin: Origin;
  source_id: string | null;
  source_url: string | null;
  fetch_layer: number | null;
  fetch_method: string | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_audio: boolean | null;
  usable_ranges_ms: Array<[number, number]> | null;
  notes: string;
  usage_note: string;
}

export interface Gap {
  need: string;
  reason: string;
  suggestion: string;
}

export interface InventoryResult {
  topicId: string;
  assets: Asset[];
  gaps: Gap[];
  added: string[];
  removed: string[];
  warnings: string[];
}

const EXTENSIONS: Record<string, AssetType> = {
  mp4: "video", mov: "video", webm: "video", mkv: "video", avi: "video", m4v: "video", gif: "gif",
  wav: "audio", mp3: "audio", m4a: "audio", aac: "audio", flac: "audio", ogg: "audio",
  png: "image", jpg: "image", jpeg: "image", webp: "image", avif: "image",
  pdf: "document", md: "document", txt: "document",
};
const FOLDERS: Array<[string, Origin]> = [["raw", "self"], ["gen", "generated"], ["proc", "derived"]];

export function typeOf(file: string): AssetType | null {
  const type = EXTENSIONS[path.extname(file).slice(1).toLowerCase()];
  if (!type) return null;
  return type === "image" && /^(screenshot|截圖|截图)/i.test(path.basename(file)) ? "screenshot" : type;
}

interface Probe {
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_audio: boolean | null;
}

/** Length, size, frame rate and sound of a media file, read with ffprobe. */
export async function probe(file: string): Promise<Probe> {
  let stdout: string;
  try {
    ({ stdout } = await run("ffprobe", ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", file], { maxBuffer: 8 * 1024 * 1024 }));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") throw new OpenTopicError("找不到 ffprobe；請安裝 ffmpeg 並確認它在 PATH 裡");
    throw new OpenTopicError(`ffprobe 讀不了這個檔案（可能已損壞）：${path.basename(file)}`);
  }
  const info = JSON.parse(stdout) as { format?: { duration?: string }; streams?: Array<{ codec_type: string; width?: number; height?: number; avg_frame_rate?: string }> };
  const streams = info.streams ?? [];
  const video = streams.find(s => s.codec_type === "video");
  const [num, den] = (video?.avg_frame_rate ?? "0/1").split("/").map(Number);
  const seconds = Number(info.format?.duration);
  return {
    duration_ms: Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : null,
    width: video?.width ?? null,
    height: video?.height ?? null,
    fps: den ? Math.round((num! / den) * 100) / 100 || null : null,
    has_audio: streams.some(s => s.codec_type === "audio"),
  };
}

async function filesUnder(dir: string): Promise<string[]> {
  const out: string[] = [];
  for (const entry of await readdir(dir, { withFileTypes: true }).catch(() => [])) {
    if (entry.name.startsWith(".") || entry.name.endsWith(".tmp")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await filesUnder(full)));
    else out.push(full);
  }
  return out.sort();
}

const sha256 = async (file: string) => createHash("sha256").update(await readFile(file)).digest("hex");

/** What each format of the topic needs and does not have (FET-FR-007 step 5). */
export function gapsFor(formats: readonly string[], assets: readonly Asset[], opts: { voiceover?: boolean } = {}): Gap[] {
  const gaps: Gap[] = [];
  const has = (...types: AssetType[]) => assets.some(a => types.includes(a.type));
  if (formats.includes("reel")) {
    if (!has("video", "gif", "image", "screenshot")) {
      gaps.push({ need: "visual", reason: "reel 沒有任何畫面素材", suggestion: "錄 30 秒操作畫面或找官方影片，放進 media/raw/ 後再執行 assets；也可以改做輪播" });
    }
    if (opts.voiceover && !has("audio")) gaps.push({ need: "voiceover", reason: "reel 還沒有旁白錄音", suggestion: "錄好旁白（wav 或 m4a）放進 media/raw/" });
  }
  if (formats.includes("carousel") && !has("image", "screenshot")) {
    gaps.push({ need: "hero-image", reason: "輪播沒有主視覺圖片", suggestion: "放一張主視覺進 media/raw/；沒有的話可以用輪播模板代替" });
  }
  return gaps;
}

const length = (ms: number | null) => (ms === null ? "—" : `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, "0")}`);
const cell = (s: string | null | undefined) => (s ?? "").replace(/\|/g, "／").replace(/\s+/g, " ").trim() || "—";

export function mediaMarkdown(assets: readonly Asset[], gaps: readonly Gap[]): string {
  const rows = assets.map(a => `| ${cell(a.path)} | ${a.type} | ${length(a.duration_ms)} | ${cell(a.source_url ?? "自己放入")} | ${cell(a.fetch_layer ? `${a.origin}（L${a.fetch_layer} ${a.fetch_method ?? ""}）` : a.origin)} | ${cell(a.usage_note)} |`);
  const table = assets.length ? ["| 檔名 | 類型 | 長度 | 來源 | 取得方式 | 使用註記 |", "|---|---|---|---|---|---|", ...rows].join("\n") : "目前沒有素材。";
  const missing = gaps.length ? gaps.map(g => `- **${g.need}**：${g.reason}。建議：${g.suggestion}`).join("\n") : "沒有缺口。";
  return `## 素材\n\n${table}\n\n## 缺口與建議\n\n${missing}`;
}

export async function inventory(topicId: string, opts: { now?: Date; voiceover?: boolean; detect?: SceneDetector } = {}): Promise<InventoryResult> {
  const now = opts.now ?? new Date();
  const [topic] = await sql<{ topic_id: string }[]>`SELECT topic_id FROM topics WHERE topic_id = ${topicId}`;
  if (!topic) throw new OpenTopicError(`找不到 topic ${topicId}`);
  const dir = path.join(workspaceDir(), "topics", topicId);
  const warnings: string[] = [];

  const known = await sql<{ asset_id: string; origin: Origin; source_id: string | null; source_url: string | null; fetch_layer: number | null; fetch_method: string | null; deleted_at: Date | null }[]>`
    SELECT asset_id, origin, source_id, source_url, fetch_layer, fetch_method, deleted_at FROM topic_assets WHERE topic_id = ${topicId}`;
  const byId = new Map(known.map(k => [k.asset_id, k]));
  // What a person wrote about an asset in the last assets.json stays.
  const previous = JSON.parse(await readFile(path.join(dir, "assets.json"), "utf8").catch(() => "{}")) as { assets?: Asset[] };
  const written = new Map((previous.assets ?? []).map(a => [a.asset_id, a]));

  const assets: Asset[] = [];
  const seen = new Set<string>();
  for (const [folder, defaultOrigin] of FOLDERS) {
    for (const file of await filesUnder(path.join(dir, "media", folder))) {
      const type = typeOf(file);
      const rel = path.relative(dir, file).split(path.sep).join("/");
      if (!type) {
        warnings.push(`不認得的檔案類型，已略過：${rel}`);
        continue;
      }
      const hash = await sha256(file);
      const id = `ast_${hash.slice(0, 12)}`;
      if (seen.has(id)) {
        warnings.push(`${rel} 的內容和另一個檔案完全相同，只列一次`);
        continue;
      }
      let probed: Awaited<ReturnType<typeof probe>>;
      try {
        probed = type === "document" ? { duration_ms: null, width: null, height: null, fps: null, has_audio: null } : await probe(file);
      } catch (error) {
        // A file ffprobe cannot read (cut short, damaged) is left out and said so; ffprobe itself missing stops everything.
        if (/找不到 ffprobe/.test((error as Error).message)) throw error;
        warnings.push(`讀不了這個檔案，沒有列進素材（可能已損壞）：${rel}`);
        continue;
      }
      seen.add(id);
      // A still picture has no length (ffprobe reports a single frame as a clip of a few milliseconds).
      const media = type === "image" || type === "screenshot" ? { ...probed, duration_ms: null, fps: null } : probed;
      const k = byId.get(id);
      const before = written.get(id);
      // Ranges already written (and perhaps edited by a person) stay; only a video seen for the first time is looked at for scenes.
      // A recording is one range; a picture or a GIF (one looping clip) has none to choose from.
      let usable: Range[] | null = before?.usable_ranges_ms ?? (type === "audio" && media.duration_ms ? [[0, media.duration_ms]] : null);
      if (!usable && type === "video" && media.duration_ms) {
        try {
          usable = await (opts.detect ?? detectScenes)(file, media.duration_ms);
        } catch (error) {
          usable = [[0, media.duration_ms]];
          warnings.push(`場景偵測失敗，整段視為可用：${rel}（${(error as Error).message.slice(0, 80)}）`);
        }
      }
      assets.push({
        asset_id: id, path: rel, type,
        origin: k?.origin ?? defaultOrigin,
        source_id: k?.source_id ?? null, source_url: k?.source_url ?? null, fetch_layer: k?.fetch_layer ?? null, fetch_method: k?.fetch_method ?? null,
        ...media,
        usable_ranges_ms: usable,
        // A file the preprocessing made is named after the asset it comes from.
        notes: before?.notes ?? (folder === "proc" && /^ast_[0-9a-f]{12}\./.test(path.basename(file)) ? `由 ${path.basename(file).split(".")[0]} 前處理而來` : ""),
        usage_note: before?.usage_note ?? (k?.fetch_layer === 2 ? "官方網頁上的檔案，使用時標註來源" : k?.fetch_layer === 3 ? "第三方來源，確認授權後再使用並標註來源" : ""),
      });
      await sql`
        INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, sha256) VALUES (${topicId}, ${id}, ${rel}, ${type}, ${k?.origin ?? defaultOrigin}, ${hash})
        ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, type = EXCLUDED.type, deleted_at = NULL`;
    }
  }
  const removed = known.filter(k => !seen.has(k.asset_id) && !k.deleted_at).map(k => k.asset_id);
  if (removed.length) await sql`UPDATE topic_assets SET deleted_at = ${now} WHERE topic_id = ${topicId} AND asset_id = ANY(${removed})`;
  const added = assets.map(a => a.asset_id).filter(id => !byId.has(id) || byId.get(id)!.deleted_at);

  const formats = (await readdir(path.join(dir, "contents")).catch(() => [] as string[])).map(n => n.split("-")[0]!).filter(f => (FORMATS as readonly string[]).includes(f));
  // The format gaps, and the ones a download left (a file too big, a page that forbids robots): both go to the person.
  const fetchGaps = await sql<Gap[]>`SELECT need, reason, suggestion FROM topic_fetch_gaps WHERE topic_id = ${topicId} ORDER BY layer, reason`;
  const gaps = [...gapsFor([...new Set(formats)], assets, { voiceover: opts.voiceover }), ...fetchGaps.map(g => ({ need: g.need, reason: g.reason, suggestion: g.suggestion }))];

  await writeAtomic(path.join(dir, "assets.json"), `${JSON.stringify({ schema_version: 1, topic_id: topicId, updated_at: taipeiIso(now), assets, gaps }, null, 2)}\n`);
  const mediaFile = path.join(dir, "media.md");
  const existing = await stat(mediaFile).then(() => readFile(mediaFile, "utf8"), () => null);
  await writeAtomic(mediaFile, existing === null ? `# 素材\n\n${block("media", mediaMarkdown(assets, gaps))}\n` : replaceBlock(existing, "media", mediaMarkdown(assets, gaps)));
  return { topicId, assets, gaps, added, removed, warnings };
}
