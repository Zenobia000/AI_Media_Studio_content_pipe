// preprocess (REEL-FR-003, SUB-FR-001): the media of a topic made fit to cut together. Video and GIF become 30 fps
// constant-frame-rate, yuv420p, H.264 (so picture and sound stay in step); a recording becomes 48 kHz WAV with the
// silence taken off both ends and its loudness set to -14 LUFS. The result goes to media/proc/ named after the original
// (its asset id), what is already there is not made again, and the original is never touched (media/raw is read-only).
import { execFile } from "node:child_process";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { inventory, type Asset } from "./assets.ts";
import { ffmpeg } from "./ffmpeg.ts";
import { OpenTopicError } from "./topics.ts";
import { workspaceDir } from "./workspace.ts";

const run = promisify(execFile);

export const TARGET_FPS = 30;
export const DEFAULT_LUFS = -14;
const TRUE_PEAK = -1.5;
const LRA = 11;
const SILENCE = "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB:detection=peak";
const TRIM_BOTH_ENDS = `${SILENCE},areverse,${SILENCE},areverse`;

/** The integrated loudness (LUFS) of a file, measured the way broadcasters do (EBU R128). */
export async function measureLufs(file: string): Promise<number> {
  const report = await ffmpeg(["-nostats", "-i", file, "-af", "ebur128=peak=true", "-f", "null", "-"]);
  const summary = report.slice(report.lastIndexOf("Integrated loudness"));
  const match = /I:\s+(-?\d+(?:\.\d+)?)\s+LUFS/.exec(summary);
  if (!match) throw new Error("量不到響度（這個檔案可能沒有聲音）");
  return Number(match[1]);
}

/** Whether the video stream already is H.264, yuv420p, 30 fps constant (frame rate and average agree) with even sides. */
async function conforms(file: string): Promise<boolean> {
  try {
    const { stdout } = await run("ffprobe", ["-v", "error", "-select_streams", "v:0", "-print_format", "json", "-show_entries", "stream=codec_name,pix_fmt,r_frame_rate,avg_frame_rate,width,height", file]);
    const v = (JSON.parse(stdout) as { streams?: Array<Record<string, string | number>> }).streams?.[0];
    return !!v && v.codec_name === "h264" && v.pix_fmt === "yuv420p" && v.r_frame_rate === `${TARGET_FPS}/1` && v.avg_frame_rate === `${TARGET_FPS}/1` && Number(v.width) % 2 === 0 && Number(v.height) % 2 === 0;
  } catch {
    return false;
  }
}

/** A video or GIF as 30 fps CFR yuv420p H.264. Both sides are made even, which yuv420p needs. */
export async function normalizeVideo(input: string, output: string): Promise<void> {
  // Footage that already is what we want is wrapped as it is (seconds, not minutes, and no loss from encoding twice).
  if (await conforms(input)) {
    await ffmpeg(["-i", input, "-c", "copy", "-movflags", "+faststart", output]);
    return;
  }
  await ffmpeg(["-i", input, "-vf", `fps=${TARGET_FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p`, "-r", String(TARGET_FPS), "-fps_mode", "cfr",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", output]);
}

/** A recording as 48 kHz 16-bit WAV, silence off both ends, two-pass loudness normalisation to `lufs`. */
export async function normalizeNarration(input: string, output: string, lufs = DEFAULT_LUFS): Promise<void> {
  const norm = `loudnorm=I=${lufs}:TP=${TRUE_PEAK}:LRA=${LRA}`;
  const first = await ffmpeg(["-i", input, "-af", `${TRIM_BOTH_ENDS},${norm}:print_format=json`, "-f", "null", "-"]);
  const json = /\{[^{}]*"input_i"[^{}]*\}/.exec(first)?.[0];
  if (!json) throw new Error("量不到響度（這個檔案可能沒有聲音）");
  const m = JSON.parse(json) as Record<string, string>;
  const measured = `measured_I=${m.input_i}:measured_TP=${m.input_tp}:measured_LRA=${m.input_lra}:measured_thresh=${m.input_thresh}:offset=${m.target_offset}`;
  await ffmpeg(["-i", input, "-af", `${TRIM_BOTH_ENDS},${norm}:${measured}:linear=true`, "-ar", "48000", "-c:a", "pcm_s16le", output]);
}

export interface PreprocessResult {
  topicId: string;
  made: Array<{ from: string; path: string; kind: "video" | "narration" }>;
  /** Already made earlier. */
  kept: string[];
  failed: Array<{ from: string; reason: string }>;
}

export interface PreprocessOptions {
  video?: boolean;
  audio?: boolean;
  lufs?: number;
}

const exists = (p: string) => stat(p).then(() => true, () => false);

/** Normalises every video, GIF and recording of media/raw and media/gen that has not been normalised yet. */
export async function preprocess(topicId: string, opts: PreprocessOptions = {}): Promise<PreprocessResult> {
  const stock = await inventory(topicId);
  const dir = path.join(workspaceDir(), "topics", topicId);
  const procDir = path.join(dir, "media", "proc");
  const result: PreprocessResult = { topicId, made: [], kept: [], failed: [] };
  const wanted = (a: Asset): "video" | "narration" | null => {
    if (!/^media\/(raw|gen)\//.test(a.path)) return null;
    if ((a.type === "video" || a.type === "gif") && opts.video !== false) return "video";
    if (a.type === "audio" && opts.audio !== false) return "narration";
    return null;
  };
  for (const asset of stock.assets) {
    const kind = wanted(asset);
    if (!kind) continue;
    const out = path.join(procDir, `${asset.asset_id}.${kind === "video" ? "mp4" : "wav"}`);
    if (await exists(out)) {
      result.kept.push(asset.path);
      continue;
    }
    await mkdir(procDir, { recursive: true });
    const part = path.join(procDir, `.${asset.asset_id}.part.${kind === "video" ? "mp4" : "wav"}`);
    try {
      if (kind === "video") await normalizeVideo(path.join(dir, asset.path), part);
      else await normalizeNarration(path.join(dir, asset.path), part, opts.lufs);
      await rename(part, out);
      result.made.push({ from: asset.path, path: path.relative(dir, out).split(path.sep).join("/"), kind });
    } catch (error) {
      if (error instanceof OpenTopicError) throw error;
      await rm(part, { force: true });
      result.failed.push({ from: asset.path, reason: (error as Error).message });
    }
  }
  return result;
}
