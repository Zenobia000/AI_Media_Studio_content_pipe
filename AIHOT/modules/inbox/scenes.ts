// Usable ranges (FET-FR-008): where a video changes scene, so a draft can pick a part of it instead of all of it. The
// ranges are written to usable_ranges_ms; when the detection fails the whole video is usable and a warning says so.
import { ffmpeg } from "./ffmpeg.ts";

/** How different two frames must be (0 to 1) to count as a cut. */
export const SCENE_THRESHOLD = 0.3;
/** A part shorter than this is a flash, not a scene: it joins the one before it. */
export const MIN_SCENE_MS = 500;

export type Range = [startMs: number, endMs: number];
export type SceneDetector = (file: string, durationMs: number) => Promise<Range[]>;

/** The scenes of a video as [start, end] in milliseconds, covering it from 0 to its length. */
export const detectScenes: SceneDetector = async (file, durationMs) => {
  const report = await ffmpeg(["-i", file, "-an", "-vf", `select='gt(scene,${SCENE_THRESHOLD})',showinfo`, "-f", "null", "-"]);
  const cuts = [...report.matchAll(/pts_time:(\d+(?:\.\d+)?)/g)].map(m => Math.round(Number(m[1]) * 1000));
  return rangesFromCuts(cuts, durationMs);
};

/** Cuts (ms) to the ranges between them; a cut too close to the previous one or to the end is dropped. */
export function rangesFromCuts(cuts: readonly number[], durationMs: number): Range[] {
  const kept: number[] = [];
  let last = 0;
  for (const cut of [...cuts].sort((a, b) => a - b)) {
    if (cut - last >= MIN_SCENE_MS && durationMs - cut >= MIN_SCENE_MS) {
      kept.push(cut);
      last = cut;
    }
  }
  const edges = [0, ...kept, durationMs];
  return edges.slice(0, -1).map((start, i): Range => [start, edges[i + 1]!]);
}
