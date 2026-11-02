// fetch (FET-FR-001, FET-FR-002): the text of every source of a topic as sources/<source_id>.md (IF-004), and the
// official source behind a report (an official blog, a repository, a paper) added to the topic when the report links to it.
// Nothing is blocked while one source has text; a topic with none is blocked and says how a person can paste it.
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import { normalizeUrl, siteIdentityForUrl } from "@aihot/backend/lib/url";
import { extractSource, ExtractFailure, wordCount, type Extracted, type Get } from "./source-extract.ts";
import { OpenTopicError, refreshStatus, sourceIdOf } from "./topics.ts";
import { taipeiIso, workspaceDir, writeAtomic } from "./workspace.ts";

const MAX_OFFICIAL = 3;

export interface FetchResult {
  topicId: string;
  fetched: Array<{ sourceId: string; url: string; method: string; wordCount: number; official: boolean }>;
  /** Sources that already had their text and were not read again. */
  kept: string[];
  failed: Array<{ sourceId: string; url: string; reason: string }>;
  /** The topic has no source text at all: a person has to paste it (E_MISSING_SOURCE). */
  blocked: boolean;
}

/** Two labels from the right: "blogs.nvidia.com" and "nvidia.com" are one site. */
const registrable = (host: string) => host.replace(/^www\./, "").split(".").slice(-2).join(".");
const hostOf = (url: string) => {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
};

/** The sites the engine reads as first-hand (its T1 and T1.5 feeds): a report linking to one of them points at the official source. */
async function officialSites(): Promise<Set<string>> {
  const rows = await sql<{ feed: string | null }[]>`SELECT config->>'feedUrl' AS feed FROM sources WHERE tier IN ('T1', 'T1_5') AND kind = 'rss'`;
  return new Set(rows.flatMap(r => (r.feed ? [registrable(hostOf(r.feed))] : [])).filter(h => h && h !== "github.com"));
}

/** Up to three addresses in a report that are official: a repository, a paper, or a page of an official site. */
export function officialLinks(links: readonly string[], reportUrl: string, sites: ReadonlySet<string>): string[] {
  const self = registrable(hostOf(reportUrl));
  if (sites.has(self)) return [];
  const found = new Map<string, string>();
  for (const link of links) {
    const url = normalizeUrl(link);
    if (!url || url === normalizeUrl(reportUrl)) continue;
    const site = siteIdentityForUrl(url);
    const host = registrable(hostOf(url));
    const official = site?.startsWith("github:") || site?.startsWith("arxiv:") || (sites.has(host) && host !== self && new URL(url).pathname.length > 1);
    // Identity from the normalized address; the page is read at the address the report gave (a site may need its "www").
    if (official && !found.has(sourceIdOf(url))) found.set(sourceIdOf(url), link);
    if (found.size >= MAX_OFFICIAL) break;
  }
  return [...found.values()];
}

const quote = (value: string | null) => (value === null ? "null" : JSON.stringify(value));

async function writeSource(dir: string, sourceId: string, url: string, x: Extracted, now: Date): Promise<void> {
  const front = ["---", "schema_version: 1", `source_id: ${sourceId}`, `url: ${url}`, `canonical_url: ${normalizeUrl(url) ?? url}`, `kind: ${x.kind}`,
    `publisher: ${quote(x.publisher)}`, `author: ${quote(x.author)}`, `published_at: ${x.publishedAt ?? "null"}`, `fetched_at: ${taipeiIso(now)}`,
    `fetch_method: ${x.method}`, `language: ${x.language}`, `word_count: ${wordCount(x.markdown)}`, "---"].join("\n");
  await writeAtomic(path.join(dir, `${sourceId}.md`), `${front}\n\n${x.markdown}\n`);
}

async function hasFile(file: string): Promise<boolean> {
  return stat(file).then(() => true, () => false);
}

export async function fetchTopic(topicId: string, opts: { get?: Get; force?: boolean; now?: Date } = {}): Promise<FetchResult> {
  const now = opts.now ?? new Date();
  const [topic] = await sql<{ topic_id: string }[]>`SELECT topic_id FROM topics WHERE topic_id = ${topicId}`;
  if (!topic) throw new OpenTopicError(`找不到 topic ${topicId}`);
  const dir = path.join(workspaceDir(), "topics", topicId, "sources");
  const result: FetchResult = { topicId, fetched: [], kept: [], failed: [], blocked: false };
  const sites = await officialSites();
  const rows = await sql<{ source_id: string; url: string | null }[]>`SELECT source_id, url FROM topic_sources WHERE topic_id = ${topicId} ORDER BY source_id`;
  const links: Array<{ report: string; links: string[] }> = [];

  const read = async (sourceId: string, url: string, official: boolean): Promise<boolean> => {
    if (!opts.force && (await hasFile(path.join(dir, `${sourceId}.md`)))) {
      result.kept.push(sourceId);
      return true;
    }
    try {
      const x = await extractSource(url, { get: opts.get, subject: `topic:${topicId}` });
      await writeSource(dir, sourceId, url, x, now);
      result.fetched.push({ sourceId, url, method: x.method, wordCount: wordCount(x.markdown), official });
      if (!official && x.kind === "article") links.push({ report: url, links: x.links });
      return true;
    } catch (error) {
      if (!(error instanceof ExtractFailure)) throw error;
      result.failed.push({ sourceId, url, reason: error.message });
      return false;
    }
  };

  for (const r of rows) if (r.url) await read(r.source_id, r.url, false);

  // FET-FR-002: the official source a report links to joins the topic.
  const known = new Set(rows.map(r => r.source_id));
  for (const l of links) {
    for (const url of officialLinks(l.links, l.report, sites)) {
      const sourceId = sourceIdOf(url);
      if (known.has(sourceId)) continue;
      known.add(sourceId);
      if (!(await read(sourceId, url, true))) continue;
      await sql`INSERT INTO topic_sources (topic_id, source_id, url) VALUES (${topicId}, ${sourceId}, ${url}) ON CONFLICT (topic_id, source_id) DO NOTHING`;
    }
  }

  const files = (await readdir(dir).catch(() => [] as string[])).filter(n => n.endsWith(".md"));
  result.blocked = files.length === 0;
  if (result.failed.length) {
    const notesFile = path.join(workspaceDir(), "topics", topicId, "notes.md");
    let notes = (await hasFile(notesFile)) ? await readFile(notesFile, "utf8") : "# 備註\n";
    for (const f of result.failed) {
      if (notes.includes(f.url)) continue;
      notes = `${notes.trimEnd()}\n\n- 原文擷取失敗：${f.url}（${f.reason}）。可以把原文貼進 sources/manual-1.md 後再執行 fetch。\n`;
    }
    await writeAtomic(notesFile, notes);
  }
  await refreshStatus(topicId);
  return result;
}
