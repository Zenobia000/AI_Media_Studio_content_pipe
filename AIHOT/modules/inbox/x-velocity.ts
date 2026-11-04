// How loud an X post is against its own account (COL-FR-009, T-072): its engagement divided by the median of the
// account's earlier posts, so a big account's usual numbers do not count as news. The counts are the ones the search
// answered with when the post was collected (articles.raw); no extra paid call is made.
import { sql } from "@aihot/backend/db";

/** Posts of the account needed before its median means anything. */
export const MIN_HISTORY = 5;
/** Earlier posts of the account the median is taken over. */
const HISTORY = 30;

/** Likes plus twice the reposts; null when the post carries neither count. */
export function engagementOf(raw: unknown): number | null {
  if (!raw || typeof raw !== "object") return null;
  const { favorite, retweet } = raw as { favorite?: unknown; retweet?: unknown };
  const like = typeof favorite === "number" ? favorite : null;
  const repost = typeof retweet === "number" ? retweet : null;
  return like === null && repost === null ? null : (like ?? 0) + 2 * (repost ?? 0);
}

export function median(values: readonly number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/** The post's engagement in multiples of the account's median; null with too little history or no counts. */
export function velocityOf(engagement: number | null, history: readonly number[]): number | null {
  if (engagement === null || history.length < MIN_HISTORY) return null;
  return engagement / Math.max(median(history), 1);
}

/** Velocity of each X post (identity key `x:<id>`) among the keys; posts it cannot grade are left out. */
export async function xVelocities(identityKeys: readonly string[]): Promise<Map<string, number>> {
  const keys = identityKeys.filter(k => k.startsWith("x:"));
  const out = new Map<string, number>();
  if (!keys.length) return out;
  const targets = await sql<{ identity_key: string; author: string | null; raw: unknown; discovered_at: Date }[]>`
    SELECT identity_key, author, raw, discovered_at FROM articles WHERE identity_key = ANY(${keys as string[]}) AND author IS NOT NULL`;
  const authors = [...new Set(targets.map(t => t.author!))];
  if (!authors.length) return out;
  const posts = await sql<{ identity_key: string; author: string; raw: unknown; discovered_at: Date }[]>`
    SELECT identity_key, author, raw, discovered_at FROM (
      SELECT identity_key, author, raw, discovered_at, row_number() OVER (PARTITION BY author ORDER BY discovered_at DESC) AS rn
      FROM articles WHERE author = ANY(${authors}) AND identity_key LIKE 'x:%') t WHERE rn <= ${HISTORY + keys.length}`;
  for (const t of targets) {
    const history = posts
      .filter(p => p.author === t.author && p.identity_key !== t.identity_key && p.discovered_at <= t.discovered_at)
      .slice(0, HISTORY).map(p => engagementOf(p.raw)).filter((v): v is number => v !== null);
    const v = velocityOf(engagementOf(t.raw), history);
    if (v !== null) out.set(t.identity_key, v);
  }
  return out;
}
