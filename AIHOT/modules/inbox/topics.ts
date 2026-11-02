// open-topic (COL-FR-020, COL-FR-021): the candidates a person ticked become one topic with one content per format.
// Running it again changes nothing that exists; a format asked for later adds only that content. Several candidates
// merged into one go into a single topic whose sources are the sources of all of them.
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import { normalizeUrl } from "@aihot/backend/lib/url";
import { loadStoryDetail, resolveStory } from "@aihot/backend/publication/stories";
import type { Candidate } from "./format.ts";
import { dayOf, inboxDir, readAnnotations, taipeiIso, workspaceDir, writeAtomic } from "./workspace.ts";

export const PROGRAMS = ["ai-today", "tool-review", "ai-engineering"] as const;
export const FORMATS = ["threads", "carousel", "reel"] as const;
const MAX_SOURCES = 20;
const SLUG_MAX = 40;

/** A bad argument, an unknown candidate or candidates that cannot share a topic: the person's input is wrong (E_INPUT, exit 2). */
export class OpenTopicError extends Error {
  readonly code = "E_INPUT";
  constructor(message: string) {
    super(message);
  }
}

export interface OpenTopicInput {
  candidateIds: string[];
  /** Else the program the person wrote in the inbox, else the candidate's suggestion. */
  program?: string;
  /** Likewise for the formats. */
  formats?: string[];
  /** One sentence of angle for every content created; else the person's inbox note. */
  angle?: string;
  /** False: only join the candidates to the topic and leave its contents as they are (a merge into an opened topic). */
  contents?: boolean;
  now?: Date;
}

export interface OpenTopicResult {
  topicId: string;
  created: boolean;
  candidateIds: string[];
  sourceIds: string[];
  contentsCreated: string[];
  contentsExisting: string[];
  warnings: string[];
}

interface StateRow { candidate_id: string; status: string; first_listed_at: Date; snapshot: Candidate; merged_into: string | null }

/** `YYYY-MM-DD-<slug>`'s slug: ASCII, lower case, joined with "-", at most 40 characters. */
export function slugOf(title: string): string {
  const slug = title.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, SLUG_MAX).replace(/-+$/, "");
  return slug || "topic";
}

/** `src_` + the first 12 hex of the SHA-1 of the normalized address: the same address always gives the same id. */
export function sourceIdOf(url: string): string {
  return `src_${createHash("sha1").update(normalizeUrl(url) ?? url).digest("hex").slice(0, 12)}`;
}

const yamlList = (items: readonly string[]) => `[${items.join(", ")}]`;
const oneLine = (s: string) => s.replace(/\s+/g, " ").trim();

async function exists(p: string): Promise<boolean> {
  return stat(p).then(() => true, () => false);
}

async function loadStates(ids: readonly string[]): Promise<StateRow[]> {
  return sql<StateRow[]>`SELECT candidate_id, status, first_listed_at, snapshot, merged_into FROM candidate_state WHERE candidate_id = ANY(${ids as string[]})`;
}

/** The given candidates, the ones merged into them or that they were merged into, and those a note says to merge ("合併到 cand_…"). */
async function expand(ids: readonly string[], noteOf: (id: string) => string, warnings: string[]): Promise<StateRow[]> {
  const found = new Map<string, StateRow>();
  let frontier = [...new Set(ids)];
  while (frontier.length) {
    const rows = await loadStates(frontier.filter(id => !found.has(id)));
    const missing = frontier.filter(id => !found.has(id) && !rows.some(r => r.candidate_id === id));
    for (const id of missing) {
      if (ids.includes(id)) throw new OpenTopicError(`找不到候選 ${id}；先讓 inbox 產生過一次，或確認編號沒有打錯`);
      warnings.push(`備註指到的候選 ${id} 不存在，已略過`);
    }
    const next = new Set<string>();
    for (const r of rows) {
      if (r.status === "skipped") throw new OpenTopicError(`候選 ${r.candidate_id} 已經超過 48 小時被略過`);
      found.set(r.candidate_id, r);
      if (r.merged_into) next.add(r.merged_into);
      for (const [, target] of noteOf(r.candidate_id).matchAll(/合併到\s*(cand_[A-Za-z0-9]+)/g)) next.add(target!);
    }
    for (const r of await sql<{ candidate_id: string }[]>`SELECT candidate_id FROM candidate_state WHERE merged_into = ANY(${[...found.keys()]})`) next.add(r.candidate_id);
    frontier = [...next].filter(id => !found.has(id));
  }
  return [...found.values()];
}

/** The articles behind a candidate: the article itself, or the reports of the story. */
async function articleIdsOf(candidateId: string): Promise<string[]> {
  const key = candidateId.replace(/^cand_/, "");
  const [own] = await sql<{ id: string }[]>`SELECT id FROM articles WHERE id = ${key}`;
  if (own) return [own.id];
  const story = await resolveStory(key);
  if (story.kind !== "found") return [];
  const detail = await loadStoryDetail(story.storyId);
  return [...new Set([...(detail?.officialReports ?? []), ...(detail?.timeline ?? [])].map(r => r.id))].slice(0, MAX_SOURCES);
}

async function sourcesOf(candidateIds: readonly string[]): Promise<Array<{ id: string; url: string }>> {
  const sources = new Map<string, string>();
  for (const candidateId of candidateIds) {
    const articleIds = await articleIdsOf(candidateId);
    if (!articleIds.length) continue;
    for (const a of await sql<{ url: string }[]>`SELECT url FROM articles WHERE id = ANY(${articleIds})`) sources.set(sourceIdOf(a.url), a.url);
  }
  return [...sources].map(([id, url]) => ({ id, url }));
}

async function newTopicId(title: string, created: Date): Promise<string> {
  const base = `${dayOf(created)}-${slugOf(title)}`;
  for (let n = 1; ; n++) {
    const id = n === 1 ? base : `${base}-${n}`;
    const [taken] = await sql<{ topic_id: string }[]>`SELECT topic_id FROM topics WHERE topic_id = ${id}`;
    if (!taken && !(await exists(path.join(workspaceDir(), "topics", id)))) return id;
  }
}

const statusText = (candidates: readonly string[], sources: readonly string[]) => `候選：${candidates.join("、")}\n來源：${sources.join("、") || "（尚無）"}`;
const block = (name: string, body: string) => `<!-- newsroom:begin ${name} -->\n${body}\n<!-- newsroom:end ${name} -->`;
const replaceBlock = (text: string, name: string, body: string) => {
  const begin = `<!-- newsroom:begin ${name} -->`;
  const end = `<!-- newsroom:end ${name} -->`;
  const a = text.indexOf(begin);
  const b = text.indexOf(end);
  return a < 0 || b < a ? `${text.trimEnd()}\n\n${block(name, body)}\n` : text.slice(0, a) + block(name, body) + text.slice(b + end.length);
};

export async function openTopic(input: OpenTopicInput): Promise<OpenTopicResult> {
  const now = input.now ?? new Date();
  const warnings: string[] = [];
  if (!input.candidateIds.length) throw new OpenTopicError("至少要給一個候選編號");
  if (input.program && !(PROGRAMS as readonly string[]).includes(input.program)) throw new OpenTopicError(`節目只能是 ${PROGRAMS.join("、")}：${input.program}`);
  for (const f of input.formats ?? []) if (!(FORMATS as readonly string[]).includes(f)) throw new OpenTopicError(`格式只能是 ${FORMATS.join("、")}：${f}`);

  const annotations = await readAnnotations(inboxDir(), "0000-00-00", "9999-99-99");
  const rows = await expand(input.candidateIds, id => annotations.get(id)?.note ?? "", warnings);
  const all = [...input.candidateIds, ...rows.map(r => r.candidate_id).filter(id => !input.candidateIds.includes(id))];
  const primary = rows.find(r => r.candidate_id === input.candidateIds[0])!;

  const topics = await sql<{ topic_id: string }[]>`SELECT DISTINCT topic_id FROM topic_candidates WHERE candidate_id = ANY(${all})`;
  if (topics.length > 1) throw new OpenTopicError(`這些候選已經分屬不同的 topic：${topics.map(t => t.topic_id).join("、")}`);

  const fromInbox = (pick: (a: { program: string; formats: string }) => string) => all.map(id => annotations.get(id)).map(a => (a ? pick(a) : "")).find(Boolean) ?? "";
  const program = input.program ?? (fromInbox(a => a.program) || primary.snapshot.suggestedProgram);
  if (!(PROGRAMS as readonly string[]).includes(program)) throw new OpenTopicError(`節目只能是 ${PROGRAMS.join("、")}：${program}`);
  const formats = input.formats?.length ? input.formats : [...new Set(all.flatMap(id => (annotations.get(id)?.formats ?? "").split(/[,，]/).map(f => f.trim()).filter(Boolean)))];
  const wanted = formats.length ? formats : primary.snapshot.suggestedFormats;
  for (const f of wanted) if (!(FORMATS as readonly string[]).includes(f)) throw new OpenTopicError(`格式只能是 ${FORMATS.join("、")}：${f}`);
  const notes = all.map(id => ({ id, text: oneLine(annotations.get(id)?.note ?? "") })).filter(n => n.text);
  const angle = input.angle ?? notes[0]?.text.slice(0, 80) ?? "";

  const found = await sourcesOf(all);
  const existing = topics[0]?.topic_id ?? null;
  const topicId = existing ?? (await newTopicId(primary.snapshot.title, now));
  const created = !existing;
  const firstListed = new Date(Math.min(...rows.map(r => r.first_listed_at.getTime())));

  await sql.begin(async tx => {
    await tx`INSERT INTO topics (topic_id, title, program, tier, created_at) VALUES (${topicId}, ${primary.snapshot.title}, ${program}, ${primary.snapshot.tier}, ${now}) ON CONFLICT (topic_id) DO NOTHING`;
    for (const id of all) await tx`INSERT INTO topic_candidates (candidate_id, topic_id) VALUES (${id}, ${topicId}) ON CONFLICT (candidate_id) DO NOTHING`;
    for (const s of found) await tx`INSERT INTO topic_sources (topic_id, source_id, url) VALUES (${topicId}, ${s.id}, ${s.url}) ON CONFLICT (topic_id, source_id) DO UPDATE SET url = COALESCE(topic_sources.url, EXCLUDED.url)`;
  });

  const topic = (await sql<{ title: string; program: string; tier: string | null; created_at: Date }[]>`SELECT title, program, tier, created_at FROM topics WHERE topic_id = ${topicId}`)[0]!;
  const allCandidates = (await sql<{ candidate_id: string }[]>`SELECT candidate_id FROM topic_candidates WHERE topic_id = ${topicId} ORDER BY candidate_id`).map(r => r.candidate_id);
  const allSources = (await sql<{ source_id: string }[]>`SELECT source_id FROM topic_sources WHERE topic_id = ${topicId} ORDER BY source_id`).map(r => r.source_id);

  const dir = path.join(workspaceDir(), "topics", topicId);
  await mkdir(path.join(dir, "contents"), { recursive: true });
  const statusBody = statusText(allCandidates, allSources);
  const topicFile = path.join(dir, "topic.md");
  if (await exists(topicFile)) await writeAtomic(topicFile, replaceBlock(await readFile(topicFile, "utf8"), "status", statusBody));
  else {
    const front = ["---", "schema_version: 1", `id: ${topicId}`, `title: ${JSON.stringify(topic.title)}`, `program: ${topic.program}`, `tier: ${topic.tier ?? "null"}`,
      `candidate_ids: ${yamlList(allCandidates)}`, `source_ids: ${yamlList(allSources)}`, `first_seen: ${taipeiIso(firstListed)}`, `created_at: ${taipeiIso(topic.created_at)}`, "---"].join("\n");
    await writeAtomic(topicFile, `${front}\n\n# ${topic.title}\n\n${block("status", statusBody)}\n`);
  }

  const notesFile = path.join(dir, "notes.md");
  let notesText = (await exists(notesFile)) ? await readFile(notesFile, "utf8") : "# 備註\n";
  for (const n of notes) if (!notesText.includes(`${n.id}：`)) notesText = `${notesText.trimEnd()}\n\n- ${n.id}：${n.text}\n`;
  await writeAtomic(notesFile, notesText);

  const contentsCreated: string[] = [];
  const contentsExisting: string[] = [];
  const present = (await readdir(path.join(dir, "contents")).catch(() => [] as string[]));
  for (const format of input.contents === false ? [] : wanted) {
    const have = present.find(n => n.startsWith(`${format}-`));
    if (have) {
      contentsExisting.push(have);
      continue;
    }
    const contentId = `${format}-01`;
    const front = ["---", "schema_version: 1", `id: ${contentId}`, `topic_id: ${topicId}`, `program: ${topic.program}`, `format: ${format}`, "selected_revision: null", `angle: ${JSON.stringify(angle)}`, "wave: 1", "---"].join("\n");
    await writeAtomic(path.join(dir, "contents", contentId, "content.md"), `${front}\n\n${block("status", "狀態：new")}\n`);
    contentsCreated.push(contentId);
    present.push(contentId);
  }

  await sql`UPDATE candidate_state SET status = 'opened', updated_at = now() WHERE candidate_id = ANY(${all}) AND status <> 'skipped'`;
  return { topicId, created, candidateIds: allCandidates, sourceIds: allSources, contentsCreated, contentsExisting, warnings };
}

/** Rewrites the status block of topic.md after the sources of a topic changed; the rest of the file is the person's. */
export async function refreshStatus(topicId: string): Promise<void> {
  const file = path.join(workspaceDir(), "topics", topicId, "topic.md");
  if (!(await exists(file))) return;
  const candidates = (await sql<{ candidate_id: string }[]>`SELECT candidate_id FROM topic_candidates WHERE topic_id = ${topicId} ORDER BY candidate_id`).map(r => r.candidate_id);
  const sources = (await sql<{ source_id: string }[]>`SELECT source_id FROM topic_sources WHERE topic_id = ${topicId} ORDER BY source_id`).map(r => r.source_id);
  await writeAtomic(file, replaceBlock(await readFile(file, "utf8"), "status", statusText(candidates, sources)));
}

/** Marks two candidates as one before a topic is opened; if either already has a topic, the other joins it. */
export async function mergeCandidates(target: string, other: string): Promise<{ topicId: string | null }> {
  const rows = await loadStates([target, other]);
  for (const id of [target, other]) if (!rows.some(r => r.candidate_id === id)) throw new OpenTopicError(`找不到候選 ${id}`);
  const topics = await sql<{ topic_id: string }[]>`SELECT DISTINCT topic_id FROM topic_candidates WHERE candidate_id IN (${target}, ${other})`;
  if (topics.length) return { topicId: (await openTopic({ candidateIds: [target, other], contents: false })).topicId };
  await sql`UPDATE candidate_state SET merged_into = ${target}, updated_at = now() WHERE candidate_id = ${other}`;
  return { topicId: null };
}
