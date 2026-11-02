// Heat signals as time series (COL-FR-005, 006, 008): Hacker News points and comments, GitHub stars, Hugging Face likes
// and downloads, one value per metric per hour for an address, keyed like articles.identity_key so a signal belongs to
// the article it is about whichever source brought that article. The heat grading (T-075) reads the growth from them.
import { sql } from "@aihot/backend/db";
import { identityKeyForUrl, siteIdentityForUrl } from "@aihot/backend/lib/url";
import { defaultGet, ExtractFailure, type Get } from "./source-extract.ts";

export interface Point {
  identityKey: string;
  metric: string;
  value: number;
}

export interface SignalReport {
  /** Points recorded from each source, or why that source gave none. */
  hn: number | string;
  hf: number | string;
  github: number | string;
}

const HOUR_MS = 3600_000;
const hourOf = (d: Date) => new Date(Math.floor(d.getTime() / HOUR_MS) * HOUR_MS);

async function json<T>(get: Get, url: string, headers: Record<string, string> = {}): Promise<T> {
  const page = await get(url, { accept: "application/json", ...headers });
  if (page.status !== 200) throw new ExtractFailure(`HTTP ${page.status}`);
  return JSON.parse(page.text) as T;
}

/** Front-page and recent AI stories: points and comments. */
export async function hnPoints(get: Get): Promise<Point[]> {
  const urls = ["https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60", "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&numericFilters=points%3E20&hitsPerPage=60"];
  const seen = new Map<string, { url: string; points: number; comments: number }>();
  for (const url of urls) {
    const { hits } = await json<{ hits: Array<{ objectID: string; url?: string; points?: number; num_comments?: number }> }>(get, url);
    for (const h of hits) if (h.url && typeof h.points === "number") seen.set(h.objectID, { url: h.url, points: h.points, comments: h.num_comments ?? 0 });
  }
  return [...seen.values()].flatMap(h => {
    const key = identityKeyForUrl(h.url);
    return key ? [{ identityKey: key, metric: "hn_points", value: h.points }, { identityKey: key, metric: "hn_comments", value: h.comments }] : [];
  });
}

/** Today's papers (upvotes) and the trending models (likes, downloads). */
export async function hfPoints(get: Get): Promise<Point[]> {
  const papers = await json<Array<{ paper: { id: string; upvotes?: number } }>>(get, "https://huggingface.co/api/daily_papers");
  const models = await json<Array<{ id: string; likes?: number; downloads?: number }>>(get, "https://huggingface.co/api/models?sort=trendingScore&limit=30");
  return [
    ...papers.flatMap(p => (typeof p.paper.upvotes === "number" ? [{ identityKey: `arxiv:${p.paper.id.replace(/v\d+$/, "")}`, metric: "hf_upvotes", value: p.paper.upvotes }] : [])),
    ...models.flatMap(m => {
      const key = identityKeyForUrl(`https://huggingface.co/${m.id}`);
      return key ? [{ identityKey: key, metric: "hf_likes", value: m.likes ?? 0 }, { identityKey: key, metric: "hf_downloads", value: m.downloads ?? 0 }] : [];
    }),
  ];
}

/** Stars of the repositories the release feeds watch. */
export async function githubStars(get: Get, token: string | null): Promise<Point[]> {
  const feeds = await sql<{ feed: string }[]>`SELECT config->>'feedUrl' AS feed FROM sources WHERE enabled AND id LIKE 'gh-%' AND config->>'feedUrl' IS NOT NULL`;
  const auth: Record<string, string> = token ? { authorization: `Bearer ${token}` } : {};
  const out: Point[] = [];
  for (const { feed } of feeds) {
    const repo = /github\.com\/([\w.-]+)\/([\w.-]+)\/releases\.atom/.exec(feed);
    const key = repo ? siteIdentityForUrl(`https://github.com/${repo[1]}/${repo[2]}`) : null;
    if (!repo || !key) continue;
    try {
      const info = await json<{ stargazers_count?: number }>(get, `https://api.github.com/repos/${repo[1]}/${repo[2]}`, { accept: "application/vnd.github+json", ...auth });
      if (typeof info.stargazers_count === "number") out.push({ identityKey: key, metric: "gh_stars", value: info.stargazers_count });
    } catch {
      // One repository that does not answer leaves the others.
    }
  }
  return out;
}

async function store(points: readonly Point[], at: Date): Promise<void> {
  for (const p of points) {
    await sql`
      INSERT INTO signal_points (identity_key, metric, value, observed_at) VALUES (${p.identityKey}, ${p.metric}, ${p.value}, ${at})
      ON CONFLICT (identity_key, metric, observed_at) DO UPDATE SET value = EXCLUDED.value`;
  }
}

/** One snapshot of every signal source. A source that does not answer is reported and does not stop the others. */
export async function collectSignals(opts: { get?: Get; now?: Date; githubToken?: string | null } = {}): Promise<SignalReport> {
  const get = opts.get ?? defaultGet;
  const at = hourOf(opts.now ?? new Date());
  const report: SignalReport = { hn: 0, hf: 0, github: 0 };
  const run = async (name: keyof SignalReport, read: () => Promise<Point[]>) => {
    try {
      const points = await read();
      await store(points, at);
      report[name] = points.length;
    } catch (error) {
      report[name] = (error as Error).message.slice(0, 120);
    }
  };
  await run("hn", () => hnPoints(get));
  await run("hf", () => hfPoints(get));
  await run("github", () => githubStars(get, opts.githubToken ?? null));
  return report;
}

const LABELS: Array<[string, (v: number) => string]> = [
  ["hn_points", v => `HN ${v} 分`],
  ["hn_comments", v => `${v} 則留言`],
  ["gh_stars", v => `GitHub ★${v}`],
  ["hf_upvotes", v => `HF ${v} 讚`],
  ["hf_likes", v => `HF ${v} 讚`],
];

/** "HN 412 分、120 則留言" for the latest values of an item, empty when there are none. */
export function describeSignals(latest: Readonly<Record<string, number>>): string {
  return LABELS.flatMap(([metric, label]) => (typeof latest[metric] === "number" ? [label(latest[metric]!)] : [])).join("、");
}

/** The highest latest value of each metric among the articles of each story (storyIds) and of single articles (articleIds). */
export async function latestSignals(opts: { storyIds?: readonly number[]; articleIds?: readonly string[] }): Promise<{ byStory: Map<number, Record<string, number>>; byArticle: Map<string, Record<string, number>> }> {
  const byStory = new Map<number, Record<string, number>>();
  const byArticle = new Map<string, Record<string, number>>();
  const latest = sql`(SELECT DISTINCT ON (identity_key, metric) identity_key, metric, value FROM signal_points ORDER BY identity_key, metric, observed_at DESC)`;
  if (opts.storyIds?.length) {
    const rows = await sql<{ story_id: number; metric: string; value: number }[]>`
      SELECT ss.story_id, sp.metric, max(sp.value) AS value
      FROM story_signals ss JOIN articles a ON a.id = ss.article_id JOIN ${latest} sp ON sp.identity_key = a.identity_key
      WHERE ss.story_id = ANY(${opts.storyIds as number[]}) GROUP BY ss.story_id, sp.metric`;
    for (const r of rows) byStory.set(r.story_id, { ...byStory.get(r.story_id), [r.metric]: r.value });
  }
  if (opts.articleIds?.length) {
    const rows = await sql<{ id: string; metric: string; value: number }[]>`
      SELECT a.id, sp.metric, sp.value FROM articles a JOIN ${latest} sp ON sp.identity_key = a.identity_key WHERE a.id = ANY(${opts.articleIds as string[]})`;
    for (const r of rows) byArticle.set(r.id, { ...byArticle.get(r.id), [r.metric]: r.value });
  }
  return { byStory, byArticle };
}

/** Growth per hour of a metric between its last two points, or null with fewer than two. */
export async function growthPerHour(identityKey: string, metric: string): Promise<number | null> {
  const rows = await sql<{ value: number; observed_at: Date }[]>`
    SELECT value, observed_at FROM signal_points WHERE identity_key = ${identityKey} AND metric = ${metric} ORDER BY observed_at DESC LIMIT 2`;
  if (rows.length < 2) return null;
  const hours = (rows[0]!.observed_at.getTime() - rows[1]!.observed_at.getTime()) / HOUR_MS;
  return hours > 0 ? (rows[0]!.value - rows[1]!.value) / hours : null;
}
