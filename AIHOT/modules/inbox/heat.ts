// Heat grading (COL-FR-013): a candidate's signals become a score, a tier S, A or B, the breakdown of the score, and the
// program and formats to suggest. Nothing here is random or reads the clock: the same input and the same configuration
// give the same answer, so a grade can be explained and re-computed. The weights are in heat.config.json.
import { readFile } from "node:fs/promises";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import type { Tier } from "./format.ts";

export type SignalName = "resonance" | "x_velocity" | "hn_velocity" | "repo_growth" | "newsletter" | "source_weight" | "local_gap";

export interface SignalConfig {
  enabled: boolean;
  weight: number;
  cap: number;
}

export interface HeatConfig {
  signals: Record<SignalName, SignalConfig>;
  sourceWeights: Record<string, number>;
  windows: { resonanceHours: number };
  tiers: { S: { minResonance: number; minScore: number }; A: { minResonance: number; minScore: number } };
  mix: Record<Tier, { formats: string[]; note: string }>;
}

export const HEAT_CONFIG_FILE = path.join(import.meta.dirname, "heat.config.json");

export async function loadHeatConfig(file = HEAT_CONFIG_FILE): Promise<HeatConfig> {
  return JSON.parse(await readFile(file, "utf8")) as HeatConfig;
}

/** What is known about one candidate. A signal that is not known is null (or absent) and counts as nothing. */
export interface HeatInput {
  /** Independent sources reporting it within the resonance window. */
  resonanceSources: number;
  hnPointsPerHour?: number | null;
  starsPerHour?: number | null;
  hfLikesPerHour?: number | null;
  hfUpvotes?: number | null;
  newsletterMentions?: number | null;
  /** The best tier among its sources: T1, T1_5, T2, or manual. */
  bestTier?: string | null;
  /** X engagement against the account's median. */
  xVelocity?: number | null;
  /** True when nobody has covered it in Chinese yet. */
  localGap?: boolean | null;
  /** A scheduled launch event that has just ended (T-102). */
  eventEnded?: boolean;
  /** The addresses (articles.identity_key) behind it, to tell a paper from a repository. */
  identityKeys?: readonly string[];
}

export interface Contribution {
  signal: SignalName;
  enabled: boolean;
  /** The measured value, in the signal's own unit. */
  raw: number | null;
  /** 0 to 1 against the cap. */
  value: number;
  weight: number;
  /** The share of the 100 points it gave. */
  points: number;
}

export interface HeatResult {
  /** 0 to 100. */
  score: number;
  tier: Tier;
  breakdown: Contribution[];
  /** Why the tier, in words. */
  reasons: string[];
  program: string;
  formats: string[];
}

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
const round1 = (x: number) => Math.round(x * 10) / 10;

/** The raw value of each signal for an input, or null when it is not known. */
function rawValues(i: HeatInput, config: HeatConfig): Record<SignalName, number | null> {
  const repo = [i.starsPerHour ?? null, i.hfLikesPerHour ?? null, i.hfUpvotes === null || i.hfUpvotes === undefined ? null : i.hfUpvotes / 5].filter((v): v is number => v !== null);
  return {
    resonance: i.resonanceSources,
    x_velocity: i.xVelocity ?? null,
    hn_velocity: i.hnPointsPerHour ?? null,
    repo_growth: repo.length ? Math.max(...repo) : null,
    newsletter: i.newsletterMentions ?? null,
    source_weight: i.bestTier ? (config.sourceWeights[i.bestTier] ?? null) : null,
    local_gap: i.localGap === null || i.localGap === undefined ? null : i.localGap ? 1 : 0,
  };
}

export function programFor(keys: readonly string[] = []): string {
  if (keys.some(k => k.startsWith("arxiv:"))) return "ai-engineering";
  if (keys.some(k => k.startsWith("github:") || k.includes("huggingface.co/"))) return "tool-review";
  return "ai-today";
}

export function scoreHeat(input: HeatInput, config: HeatConfig): HeatResult {
  const raw = rawValues(input, config);
  const names = Object.keys(config.signals) as SignalName[];
  // A signal that is switched off has weight 0 and is left out of the 100 points altogether.
  const total = names.reduce((sum, n) => sum + (config.signals[n].enabled ? config.signals[n].weight : 0), 0);
  const breakdown = names.map((signal): Contribution => {
    const c = config.signals[signal];
    const value = c.enabled && raw[signal] !== null ? clamp01(raw[signal]! / c.cap) : 0;
    const weight = c.enabled ? c.weight : 0;
    return { signal, enabled: c.enabled, raw: raw[signal], value: round1(value * 1000) / 1000, weight, points: total ? round1((100 * weight * value) / total) : 0 };
  });
  const score = Math.round(breakdown.reduce((s, b) => s + b.points, 0));

  const reasons: string[] = [];
  const { S, A } = config.tiers;
  let tier: Tier = "B";
  if (input.eventEnded) reasons.push("預告過的發表會剛結束");
  if (input.resonanceSources >= S.minResonance) reasons.push(`${config.windows.resonanceHours} 小時內 ${input.resonanceSources} 個來源共振`);
  if (score >= S.minScore) reasons.push(`熱度分數 ${score} 達到 S 級（${S.minScore}）`);
  if (reasons.length) tier = "S";
  else {
    if (input.resonanceSources >= A.minResonance) reasons.push(`${config.windows.resonanceHours} 小時內 ${input.resonanceSources} 個來源共振`);
    if (score >= A.minScore) reasons.push(`熱度分數 ${score} 達到 A 級（${A.minScore}）`);
    if (reasons.length) tier = "A";
    else reasons.push(`熱度分數 ${score}，沒有達到 A 級（${A.minScore}）`);
  }
  return { score, tier, breakdown, reasons, program: programFor(input.identityKeys), formats: [...config.mix[tier].formats] };
}

/** "共振 30＋HN 增速 11.5…": the points each signal gave, for the inbox. */
export function describeBreakdown(result: Pick<HeatResult, "score" | "breakdown">): string {
  const labels: Record<SignalName, string> = { resonance: "共振", x_velocity: "X 互動", hn_velocity: "HN 增速", repo_growth: "專案增速", newsletter: "電子報", source_weight: "來源權重", local_gap: "在地缺口" };
  const parts = result.breakdown.filter(b => b.enabled && b.points > 0).map(b => `${labels[b.signal]} ${b.points}`);
  return `${result.score} 分${parts.length ? `（${parts.join("＋")}）` : ""}`;
}

// ── reading the signals of the database ─────────────────────────────────────────────────

interface Point { value: number; at: Date }

/** Value now, and the rise per hour between the last two points (null with fewer than two). */
function latestAndRate(points: readonly Point[]): { latest: number; perHour: number | null } {
  const [last, before] = points;
  const hours = last && before ? (last.at.getTime() - before.at.getTime()) / 3600_000 : 0;
  return { latest: last!.value, perHour: before && hours > 0 ? (last!.value - before.value) / hours : null };
}

export interface HeatTargets {
  /** Stories of the hot ranking. */
  storyIds?: readonly number[];
  /** Articles that stand for themselves (added by hand). */
  articleIds?: readonly string[];
}

export interface LoadedInputs {
  byStory: Map<number, HeatInput>;
  byArticle: Map<string, HeatInput>;
}

/** The inputs of the grading for stories and articles, read as of `now`. */
export async function loadHeatInputs(targets: HeatTargets, config: HeatConfig, now: Date): Promise<LoadedInputs> {
  const byStory = new Map<number, HeatInput>();
  const byArticle = new Map<string, HeatInput>();
  const windowStart = new Date(now.getTime() - config.windows.resonanceHours * 3600_000);

  const members: Array<{ owner: { story: number } | { article: string }; key: string; source: string; tier: string; at: Date | null }> = [];
  if (targets.storyIds?.length) {
    const rows = await sql<{ story_id: number; identity_key: string; source_id: string; tier: string; observed_at: Date }[]>`
      SELECT ss.story_id, a.identity_key, a.source_id, s.tier, ss.observed_at
      FROM story_signals ss JOIN articles a ON a.id = ss.article_id JOIN sources s ON s.id = a.source_id
      WHERE ss.story_id = ANY(${targets.storyIds as number[]}) AND ss.observed_at <= ${now}`;
    for (const r of rows) members.push({ owner: { story: r.story_id }, key: r.identity_key, source: r.source_id, tier: r.tier, at: r.observed_at });
  }
  if (targets.articleIds?.length) {
    const rows = await sql<{ id: string; identity_key: string; source_id: string }[]>`SELECT id, identity_key, source_id FROM articles WHERE id = ANY(${targets.articleIds as string[]})`;
    for (const r of rows) members.push({ owner: { article: r.id }, key: r.identity_key, source: r.source_id, tier: "manual", at: null });
  }
  const keys = [...new Set(members.map(m => m.key))];
  const series = new Map<string, Point[]>();
  if (keys.length) {
    const rows = await sql<{ identity_key: string; metric: string; value: number; observed_at: Date }[]>`
      SELECT identity_key, metric, value, observed_at FROM (
        SELECT identity_key, metric, value, observed_at, row_number() OVER (PARTITION BY identity_key, metric ORDER BY observed_at DESC) AS rn
        FROM signal_points WHERE identity_key = ANY(${keys}) AND observed_at <= ${now}) t WHERE rn <= 2 ORDER BY identity_key, metric, observed_at DESC`;
    for (const r of rows) {
      const k = `${r.identity_key}\u0000${r.metric}`;
      series.set(k, [...(series.get(k) ?? []), { value: r.value, at: r.observed_at }]);
    }
  }
  const best = (a: number | null | undefined, b: number | null) => (b === null ? (a ?? null) : a === null || a === undefined ? b : Math.max(a, b));

  const owners = new Map<string, typeof members>();
  for (const m of members) {
    const id = "story" in m.owner ? `s:${m.owner.story}` : `a:${m.owner.article}`;
    owners.set(id, [...(owners.get(id) ?? []), m]);
  }
  for (const [id, list] of owners) {
    const inWindow = new Set(list.filter(m => m.at && m.at > windowStart).map(m => m.source));
    const input: HeatInput = { resonanceSources: id.startsWith("a:") ? 1 : inWindow.size, identityKeys: [...new Set(list.map(m => m.key))] };
    const tiers = list.map(m => m.tier);
    input.bestTier = ["manual", "T1", "T1_5", "T2"].find(t => tiers.includes(t)) ?? null;
    for (const m of list) {
      const get = (metric: string) => series.get(`${m.key}\u0000${metric}`);
      const hn = get("hn_points");
      const stars = get("gh_stars");
      const likes = get("hf_likes");
      const upvotes = get("hf_upvotes");
      const mentions = get("newsletter_mentions");
      if (hn) input.hnPointsPerHour = best(input.hnPointsPerHour, latestAndRate(hn).perHour);
      if (stars) input.starsPerHour = best(input.starsPerHour, latestAndRate(stars).perHour);
      if (likes) input.hfLikesPerHour = best(input.hfLikesPerHour, latestAndRate(likes).perHour);
      if (upvotes) input.hfUpvotes = best(input.hfUpvotes, latestAndRate(upvotes).latest);
      if (mentions) input.newsletterMentions = best(input.newsletterMentions, latestAndRate(mentions).latest);
    }
    if (id.startsWith("s:")) byStory.set(Number(id.slice(2)), input);
    else byArticle.set(id.slice(2), input);
  }
  return { byStory, byArticle };
}

// ── backtest ────────────────────────────────────────────────────────────────────────────

export interface BacktestLabel {
  /** The public id of the story (a UUID), as in /story/<id>. */
  story: string;
  /** What a person says it was: S, A or B. */
  label: Tier;
  /** When it should have been graded (ISO); now when absent. */
  asOf?: string;
}

export interface BacktestRow {
  story: string;
  label: Tier;
  tier: Tier | null;
  score: number | null;
  found: boolean;
}

export interface BacktestResult {
  rows: BacktestRow[];
  /** Of the stories a person marked S: how many were graded S or A, out of how many that could be found. */
  sHits: number;
  sTotal: number;
  /** 0 to 1; null without any S. */
  sRate: number | null;
}

/** Grades the stories a person labelled, from the data as it was at each label's time, and says how many S ones were found. */
export async function backtestHeat(labels: readonly BacktestLabel[], config: HeatConfig, now: Date = new Date()): Promise<BacktestResult> {
  const rows: BacktestRow[] = [];
  for (const l of labels) {
    const [story] = await sql<{ id: number }[]>`SELECT id FROM stories WHERE public_id::text = ${l.story}`;
    if (!story) {
      rows.push({ story: l.story, label: l.label, tier: null, score: null, found: false });
      continue;
    }
    const at = l.asOf ? new Date(l.asOf) : now;
    const input = (await loadHeatInputs({ storyIds: [story.id] }, config, at)).byStory.get(story.id) ?? { resonanceSources: 0 };
    const result = scoreHeat(input, config);
    rows.push({ story: l.story, label: l.label, tier: result.tier, score: result.score, found: true });
  }
  const s = rows.filter(r => r.label === "S" && r.found);
  const hits = s.filter(r => r.tier === "S" || r.tier === "A").length;
  return { rows, sHits: hits, sTotal: s.length, sRate: s.length ? hits / s.length : null };
}
