// Page change monitoring (COL-FR-010): pages without a feed (changelogs, pricing) are watched by a self-hosted
// changedetection.io; each change becomes an inbox candidate "<page> 有更新" with a summary of the difference.
// Polling its REST API, so the service being down costs this one source and nothing else.
import { credential } from "@aihot/backend/config";
import { sql } from "@aihot/backend/db";
import { identityKeyForUrl } from "@aihot/backend/lib/url";
import { addSource } from "./manual.ts";

/** Changes older than this are not brought in (the first run does not flood the inbox with history). */
const RECENT_MS = 24 * 3600_000;
const SUMMARY_CHARS = 600;

export type WatchGet = (path: string) => Promise<{ status: number; text: string }>;

interface Watch {
  url: string;
  title?: string | null;
  last_changed?: number;
}

export function apiGet(base: string, key: string): WatchGet {
  return async (path) => {
    const res = await fetch(new URL(path, base.endsWith("/") ? base : `${base}/`), { headers: { "x-api-key": key }, signal: AbortSignal.timeout(20_000) });
    return { status: res.status, text: await res.text() };
  };
}

/**
 * The lines the change added or removed, short enough for a note. changedetection.io's text difference marks them
 * "(changed)", "(into)", "(added)" and "(removed)"; the "+" and "-" lines of a unified diff are read the same way.
 */
export function summarize(diff: string): string {
  const marked = (l: string) => /^\((?:changed|into|added|removed)\)\s*\S/.test(l) || (/^[+\-]\s*\S/.test(l) && !/^(\+\+\+|---)/.test(l));
  const lines = diff.split(/\r?\n/).map(l => l.trim()).filter(marked);
  const text = lines.join("\n");
  return text.length > SUMMARY_CHARS ? `${text.slice(0, SUMMARY_CHARS)}…` : text;
}

async function alreadyListed(url: string): Promise<boolean> {
  const key = identityKeyForUrl(url);
  if (!key) return false;
  const rows = await sql`SELECT 1 FROM manual_candidates m JOIN articles a ON a.id = m.article_id WHERE a.identity_key = ${key}`;
  return rows.length > 0;
}

export async function pollWatches(get: WatchGet, now = new Date()): Promise<{ added: number; skipped: number; error?: string }> {
  let watches: Record<string, Watch>;
  try {
    const res = await get("api/v1/watch");
    if (res.status !== 200) return { added: 0, skipped: 0, error: `changedetection 回應 ${res.status}` };
    watches = JSON.parse(res.text);
  } catch (err) {
    return { added: 0, skipped: 0, error: `連不上 changedetection：${(err as Error).message}` };
  }
  let added = 0;
  let skipped = 0;
  for (const [uuid, w] of Object.entries(watches)) {
    const changed = w.last_changed ?? 0;
    if (!changed || now.getTime() - changed * 1000 > RECENT_MS || !/^https?:\/\//.test(w.url)) {
      skipped++;
      continue;
    }
    try {
      const link = new URL(w.url);
      link.searchParams.set("_changed", String(changed));
      // An earlier poll brought this change in: adding it again would only repeat its note.
      if (await alreadyListed(link.toString())) {
        skipped++;
        continue;
      }
      const history = JSON.parse((await get(`api/v1/watch/${uuid}/history`)).text) as Record<string, string>;
      const stamps = Object.keys(history).map(Number).sort((a, b) => a - b);
      // The first snapshot is the page as it was found, not a change.
      if (stamps.length < 2) {
        skipped++;
        continue;
      }
      const diff = await get(`api/v1/watch/${uuid}/difference/${stamps.at(-2)}/${stamps.at(-1)}?format=text`);
      const name = w.title?.trim() || new URL(w.url).hostname;
      await addSource(link.toString(), { title: `${name} 有更新`, note: `[變動監測] ${w.url}\n${diff.status === 200 ? summarize(diff.text) : ""}`.trim() });
      added++;
    } catch {
      skipped++;
    }
  }
  return { added, skipped };
}

/** The scheduled run: nothing to do when the service is not configured. */
export async function runWatchPoll() {
  const base = credential("collectors", "CHANGEDETECTION_URL");
  const key = credential("collectors", "CHANGEDETECTION_API_KEY");
  if (!base || !key) return { skipped: "CHANGEDETECTION_URL／CHANGEDETECTION_API_KEY 沒設定" };
  return pollWatches(apiGet(base, key));
}
