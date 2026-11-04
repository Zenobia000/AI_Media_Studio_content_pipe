// The local heat check (COL-FR-016, US-019): for S and A candidates, has the Chinese-speaking side covered it yet? A keyword search
// on Threads answers; few or no matches is a gap worth taking. The search has a daily allowance and needs the platform's approval
// (THREADS_ACCESS_TOKEN): without either, the inbox says "未檢查" and why, and nothing else changes.
import { credential } from "@aihot/backend/config";
import { sql } from "@aihot/backend/db";
import type { Candidate } from "./format.ts";
import { needsTranslation } from "./titles.ts";

/** Posts that make a story "covered" in Chinese. */
export const COVERED_MIN = 3;
/** Searches a day the check may spend (the platform allows 2,200 a day; the rest is left for other uses). */
export const DAILY_BUDGET = 200;
const DAY_MS = 24 * 3600_000;

export class QuotaError extends Error {
  constructor() {
    super("搜尋額度用完");
  }
}
export class DeniedError extends Error {}

/** Posts found for the words; throws QuotaError when the allowance is gone and DeniedError when the access is not granted. */
export type Search = (query: string) => Promise<number>;

export function threadsSearch(token: string, fetcher: typeof fetch = fetch): Search {
  return async query => {
    const url = new URL("https://graph.threads.net/v1.0/keyword_search");
    url.search = new URLSearchParams({ q: query, search_type: "RECENT", fields: "id", access_token: token }).toString();
    const res = await fetcher(url, { signal: AbortSignal.timeout(20_000) });
    if (res.status === 429) throw new QuotaError();
    if (res.status === 400 || res.status === 401 || res.status === 403) throw new DeniedError(`Threads 搜尋被拒絕（HTTP ${res.status}），權限可能還沒通過審核`);
    if (!res.ok) throw new Error(`Threads 搜尋失敗（HTTP ${res.status}）`);
    return ((await res.json()) as { data?: unknown[] }).data?.length ?? 0;
  };
}

export const defaultSearch = (): Search | null => {
  const token = credential("integrations", "THREADS_ACCESS_TOKEN");
  return token ? threadsSearch(token) : null;
};

export function describeLocal(row: { status: string; matches: number | null; reason: string | null }): string {
  if (row.status === "covered") return `中文圈已有 ${row.matches} 則相關貼文`;
  if (row.status === "gap") return `中文圈還沒人報導（找到 ${row.matches} 則，缺口）`;
  return `未檢查（${row.reason ?? "原因不明"}）`;
}

export interface LocalOptions {
  /** Null when there is no access set up. */
  search?: Search | null;
  now?: Date;
  budget?: number;
}

/** Checks the S and A candidates not checked in the last day, within the allowance, and puts the answer on every candidate that has one. */
export async function withLocalHeat(listed: readonly Candidate[], opts: LocalOptions = {}): Promise<Candidate[]> {
  const search = opts.search === undefined ? defaultSearch() : opts.search;
  const now = opts.now ?? new Date();
  const budget = opts.budget ?? DAILY_BUDGET;
  const wanted = listed.filter(c => (c.tier === "S" || c.tier === "A") && !c.manual);
  if (wanted.length) {
    const rows = await sql<{ candidate_id: string; status: string; checked_at: Date }[]>`SELECT candidate_id, status, checked_at FROM local_heat`;
    const recent = new Set(rows.filter(r => r.status !== "unchecked" && now.getTime() - r.checked_at.getTime() < DAY_MS).map(r => r.candidate_id));
    let spent = rows.filter(r => r.status !== "unchecked" && now.getTime() - r.checked_at.getTime() < DAY_MS).length;
    let stop: string | null = search ? null : "還沒設定 Threads 搜尋權限（THREADS_ACCESS_TOKEN）";
    const save = (id: string, status: string, matches: number | null, reason: string | null) => sql`
      INSERT INTO local_heat (candidate_id, status, matches, reason, checked_at) VALUES (${id}, ${status}, ${matches}, ${reason}, ${now})
      ON CONFLICT (candidate_id) DO UPDATE SET status = EXCLUDED.status, matches = EXCLUDED.matches, reason = EXCLUDED.reason, checked_at = EXCLUDED.checked_at`;
    for (const c of wanted) {
      if (recent.has(c.id)) continue;
      if (needsTranslation(c.title)) { await save(c.id, "unchecked", null, "還沒有中文標題，無法用中文搜尋"); continue; }
      if (!stop && spent >= budget) stop = "今天的搜尋額度用完";
      if (stop) { await save(c.id, "unchecked", null, stop); continue; }
      try {
        const matches = await search!(c.title);
        spent++;
        await save(c.id, matches >= COVERED_MIN ? "covered" : "gap", matches, null);
      } catch (err) {
        stop = err instanceof QuotaError ? "今天的搜尋額度用完" : err instanceof DeniedError ? err.message : `搜尋失敗：${(err as Error).message.slice(0, 80)}`;
        await save(c.id, "unchecked", null, stop);
        // A failure other than the allowance or the access is tried again on the next candidate.
        if (!(err instanceof QuotaError || err instanceof DeniedError)) stop = null;
      }
    }
  }
  if (!listed.length) return [];
  const rows = await sql<{ candidate_id: string; status: string; matches: number | null; reason: string | null }[]>`
    SELECT candidate_id, status, matches, reason FROM local_heat WHERE candidate_id = ANY(${listed.map(c => c.id)})`;
  const byId = new Map(rows.map(r => [r.candidate_id, r]));
  return listed.map(c => (byId.has(c.id) && (c.tier === "S" || c.tier === "A") ? { ...c, local: describeLocal(byId.get(c.id)!) } : c));
}
