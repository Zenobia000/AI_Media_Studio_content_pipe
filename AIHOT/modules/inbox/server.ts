// Writes today's inbox/YYYY-MM-DD.md (IF-001) from the public hot ranking, twice an hour: the hot stories, the
// links added by hand, and what the days before left (cross-day.ts).
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import type { ServerModule } from "@aihot/backend/modules";
import { config } from "@aihot/backend/config";
import { sql } from "@aihot/backend/db";
import { latestHotRanking } from "@aihot/backend/publication/hot";
import { CARRY_MS, planInbox, type StateRow } from "./cross-day.ts";
import { ManagedBlockError, parseAnnotations, renderFile, tierOf, type Annotation, type Candidate } from "./format.ts";

const TIME_ZONE = "Asia/Taipei";
const DAY_MS = 24 * 3600_000;
/** Earlier inbox files read for ticks and notes: a candidate lives 48 hours, a ticked one is read for as long as it is carried. */
const LOOKBACK_DAYS = 14;
const inboxDir = () => process.env.INBOX_DIR ?? path.join(config.dataDir, "workspace", "inbox");
const clock = (d: Date, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("sv-SE", { timeZone: TIME_ZONE, ...opts }).format(d);
const dayOf = (d: Date) => clock(d, { dateStyle: "short" });

/** What the person wrote in the inbox files of the last days, oldest first so a later day overrides an earlier one. */
async function readEarlier(dir: string, today: string, now: Date): Promise<Map<string, Annotation>> {
  const from = dayOf(new Date(now.getTime() - LOOKBACK_DAYS * DAY_MS));
  const names = (await readdir(dir).catch(() => [] as string[])).filter(n => /^\d{4}-\d{2}-\d{2}\.md$/.test(n)).sort();
  const notes = new Map<string, Annotation>();
  for (const name of names) {
    const day = name.slice(0, 10);
    if (day < from || day >= today) continue;
    for (const [id, a] of parseAnnotations(await readFile(path.join(dir, name), "utf8"))) notes.set(id, a);
  }
  return notes;
}

export async function writeInbox(now = new Date()): Promise<{ file: string; candidates: number } | null> {
  const dir = inboxDir();
  const date = dayOf(now);
  const file = path.join(dir, `${date}.md`);
  const ranking = await latestHotRanking();
  const manual = await sql<{ article_id: string; title: string; notes: string; program: string | null; formats: string[]; added_at: Date }[]>`
    SELECT m.article_id, a.title, m.notes, m.program, m.formats, m.added_at
    FROM manual_candidates m JOIN articles a ON a.id = m.article_id
    WHERE m.added_at > ${new Date(now.getTime() - CARRY_MS)} ORDER BY m.added_at DESC`;
  const fresh: Candidate[] = [
    ...manual.map((m): Candidate => ({
      id: `cand_${m.article_id}`,
      title: m.title,
      tier: "S",
      participantCount: 1,
      sourceNames: ["手動"],
      firstSeen: clock(m.added_at, { hour: "2-digit", minute: "2-digit" }),
      suggestedProgram: m.program ?? "ai-today",
      suggestedFormats: m.formats.length ? m.formats : ["threads"],
      manual: { note: m.notes, program: m.program ?? "", formats: m.formats.join(", ") },
    })),
    ...(ranking?.entries ?? []).map((e): Candidate => ({
      id: `cand_${e.storyPublicId}`,
      title: e.title,
      tier: tierOf(e.participantCount),
      participantCount: e.participantCount,
      sourceNames: e.sourceNames,
      firstSeen: clock(new Date(e.firstReportAt), { hour: "2-digit", minute: "2-digit" }),
      suggestedProgram: "ai-today",
      suggestedFormats: ["threads"],
    })),
  ];

  const existing = await readFile(file, "utf8").catch(() => null);
  const earlier = await readEarlier(dir, date, now);
  // A tick in any file is a pick; a candidate stays picked once picked.
  const ticked = [...earlier, ...(existing ? parseAnnotations(existing) : [])].filter(([, a]) => a.checked).map(([id]) => id);
  if (ticked.length) await sql`UPDATE candidate_state SET status = 'picked', updated_at = now() WHERE candidate_id = ANY(${ticked}) AND status = 'new'`;

  const rows = (await sql<{ candidate_id: string; status: StateRow["status"]; first_listed_at: Date; snapshot: Candidate }[]>`
    SELECT candidate_id, status, first_listed_at, snapshot FROM candidate_state`)
    .map((r): StateRow => ({ id: r.candidate_id, status: r.status, firstListedAt: r.first_listed_at, snapshot: r.snapshot }));
  const plan = planInbox(fresh, rows, now, date, dayOf);
  if (!plan.listed.length && !ranking) return null;

  const text = renderFile(date, plan.listed, existing, earlier);
  await mkdir(dir, { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, text, "utf8");
  await rename(tmp, file);

  const freshIds = new Set(fresh.map(c => c.id));
  for (const c of plan.listed.filter(c => freshIds.has(c.id))) {
    const { carried: _carried, ...snapshot } = c;
    await sql`
      INSERT INTO candidate_state (candidate_id, first_listed_at, snapshot) VALUES (${c.id}, ${now}, ${sql.json(snapshot as never)})
      ON CONFLICT (candidate_id) DO UPDATE SET snapshot = EXCLUDED.snapshot, updated_at = now()`;
  }
  if (plan.skip.length) await sql`UPDATE candidate_state SET status = 'skipped', updated_at = now() WHERE candidate_id = ANY(${plan.skip}) AND status = 'new'`;
  return { file, candidates: plan.listed.length };
}

export const server: ServerModule = {
  name: "inbox",
  schedules: [{
    name: "inbox-write",
    cron: "*/30 * * * *",
    missed: "skip",
    run: async () => {
      try {
        return await writeInbox();
      } catch (err) {
        if (err instanceof ManagedBlockError) return { error: err.message };
        throw err;
      }
    },
  }],
};
