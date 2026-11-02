// What every fetch layer tried, and whether it worked (FET-FR-011): the figures that say which layer is worth
// its upkeep, and the record of the daily self-test (FET-FR-010).
import { sql } from "@aihot/backend/db";

/** 0 is the source text; 2 the files a page gives; 3 the downloaders (the numbers of docs/spec/03_fetch_assets.md). */
export const LAYER_NAMES: Record<number, string> = { 0: "原文擷取", 1: "L1 官方 API", 2: "L2 網頁檔案", 3: "L3 下載器", 4: "L4 瀏覽器錄影" };

export interface Attempt {
  layer: number;
  url: string;
  ok: boolean;
  reason?: string | null;
}

export async function recordAttempts(attempts: readonly Attempt[], kind: "download" | "selftest" = "download", at: Date = new Date()): Promise<void> {
  for (const a of attempts) {
    await sql`INSERT INTO fetch_attempts (layer, kind, url, ok, reason, attempted_at) VALUES (${a.layer}, ${kind}, ${a.url}, ${a.ok}, ${a.reason ?? null}, ${at})`;
  }
}

export interface LayerStats {
  layer: number;
  name: string;
  attempts: number;
  ok: number;
  /** 0 to 1. */
  rate: number;
}

/** The success rate of each layer over the last `days` days of real downloads (self-tests are not counted). */
export async function fetchStats(days = 30, now: Date = new Date()): Promise<LayerStats[]> {
  const rows = await sql<{ layer: number; attempts: number; ok: number }[]>`
    SELECT layer, count(*)::int AS attempts, count(*) FILTER (WHERE ok)::int AS ok
    FROM fetch_attempts WHERE kind = 'download' AND attempted_at > ${new Date(now.getTime() - days * 86_400_000)} AND attempted_at <= ${now}
    GROUP BY layer ORDER BY layer`;
  return rows.map(r => ({ layer: r.layer, name: LAYER_NAMES[r.layer] ?? `L${r.layer}`, attempts: r.attempts, ok: r.ok, rate: r.attempts ? r.ok / r.attempts : 0 }));
}
