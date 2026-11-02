// Writes today's inbox/YYYY-MM-DD.md (IF-001) from the public hot ranking, twice an hour: the hot stories, the
// links added by hand, and what the days before left (cross-day.ts).
import { readFile } from "node:fs/promises";
import path from "node:path";
import type { ServerModule } from "@aihot/backend/modules";
import { credential } from "@aihot/backend/config";
import { sql } from "@aihot/backend/db";
import { latestHotRanking } from "@aihot/backend/publication/hot";
import { CARRY_MS, planInbox, type StateRow } from "./cross-day.ts";
import { ManagedBlockError, parseAnnotations, renderFile, withHealth, type Candidate, type FailingSource, type Tier } from "./format.ts";
import { justEnded, mentionsEndedEvent, startingTomorrow, syncEvents } from "./events.ts";
import { describeBreakdown, loadHeatConfig, loadHeatInputs, scoreHeat, type HeatResult } from "./heat.ts";
import { runWatchPoll } from "./watch.ts";
import { withTitlesZh } from "./titles.ts";
import { splitNewsletters } from "./newsletters.ts";
import { loadTargets, runSelftest } from "./selftest.ts";
import { collectSignals, describeSignals, latestSignals } from "./signals.ts";
import { clock, dayOf, inboxDir, readAnnotations, writeAtomic } from "./workspace.ts";

const DAY_MS = 24 * 3600_000;
/** Consecutive failed collections before a source is listed in the inbox. */
const FAIL_LISTED = 3;
/** Earlier inbox files read for ticks and notes: a candidate lives 48 hours, a ticked one is read for as long as it is carried. */
const LOOKBACK_DAYS = 14;


export async function writeInbox(now = new Date()): Promise<{ file: string; candidates: number } | null> {
  const dir = inboxDir();
  const date = dayOf(now);
  const file = path.join(dir, `${date}.md`);
  const ranking = await latestHotRanking();
  const manual = await sql<{ article_id: string; title: string; notes: string; program: string | null; formats: string[]; added_at: Date }[]>`
    SELECT m.article_id, a.title, m.notes, m.program, m.formats, m.added_at
    FROM manual_candidates m JOIN articles a ON a.id = m.article_id
    WHERE m.added_at > ${new Date(now.getTime() - CARRY_MS)} ORDER BY m.added_at DESC`;
  // What people are saying about each (Hacker News points, stars, likes), from the signal snapshots.
  const signals = await latestSignals({ storyIds: (ranking?.entries ?? []).map(e => e.storyId), articleIds: manual.map(m => m.article_id) });
  // How hot each is (heat.config.json): the tier, the score and why, and what to suggest.
  const config = await loadHeatConfig();
  const inputs = await loadHeatInputs({ storyIds: (ranking?.entries ?? []).map(e => e.storyId), articleIds: manual.map(m => m.article_id) }, config, now);
  // The launch events in calendar/events.md: a story that names one that has just ended is S (COL-FR-017).
  const { events } = await syncEvents();
  const ended = justEnded(events, now);
  const heatOf = (input: ReturnType<typeof inputs.byStory.get>, title: string): HeatResult =>
    scoreHeat({ resonanceSources: 0, ...input, eventEnded: mentionsEndedEvent(title, ended) }, config);
  const heat = (h: HeatResult) => ({ tier: h.tier, score: h.score, heat: describeBreakdown(h), heatReason: h.reasons.join("、") });
  const listed: Candidate[] = [
    ...manual.map((m): Candidate => ({
      id: `cand_${m.article_id}`,
      title: m.title,
      ...heat(heatOf(inputs.byArticle.get(m.article_id), m.title)),
      participantCount: 1,
      sourceNames: ["手動"],
      firstSeen: clock(m.added_at, { hour: "2-digit", minute: "2-digit" }),
      suggestedProgram: m.program ?? heatOf(inputs.byArticle.get(m.article_id), m.title).program,
      suggestedFormats: m.formats.length ? m.formats : heatOf(inputs.byArticle.get(m.article_id), m.title).formats,
      manual: { note: m.notes, program: m.program ?? "", formats: m.formats.join(", ") },
      signals: describeSignals(signals.byArticle.get(m.article_id) ?? {}),
    })),
    ...(ranking?.entries ?? []).map((e): Candidate => ({
      id: `cand_${e.storyPublicId}`,
      title: e.title,
      ...heat(heatOf(inputs.byStory.get(e.storyId), e.title)),
      participantCount: e.participantCount,
      sourceNames: e.sourceNames,
      firstSeen: clock(new Date(e.firstReportAt), { hour: "2-digit", minute: "2-digit" }),
      suggestedProgram: heatOf(inputs.byStory.get(e.storyId), e.title).program,
      suggestedFormats: heatOf(inputs.byStory.get(e.storyId), e.title).formats,
      signals: describeSignals(signals.byStory.get(e.storyId) ?? {}),
    })),
    // The day before a launch event: a candidate to prepare it (background, the last generation's specs).
    ...startingTomorrow(events, now).map((e): Candidate => ({
      id: `cand_${e.id}`,
      title: `準備：${e.title}`,
      tier: "A",
      heatReason: `明天 ${clock(e.startsAt, { hour: "2-digit", minute: "2-digit" })} 發表會`,
      participantCount: 1,
      sourceNames: ["發表會行事曆"],
      firstSeen: clock(now, { hour: "2-digit", minute: "2-digit" }),
      suggestedProgram: "ai-today",
      suggestedFormats: ["threads"],
      manual: { note: [e.url, e.keywords.length ? `關鍵字：${e.keywords.join("、")}` : ""].filter(Boolean).join("　"), program: "", formats: "" },
    })),
  ];
  const fresh = await withTitlesZh(listed);
  // The hottest first within a tier.
  const order: Record<Tier, number> = { S: 0, A: 1, B: 2 };
  fresh.sort((a, b) => order[a.tier] - order[b.tier] || (b.score ?? 0) - (a.score ?? 0) || a.title.localeCompare(b.title));

  const existing = await readFile(file, "utf8").catch(() => null);
  const earlier = await readAnnotations(dir, dayOf(new Date(now.getTime() - LOOKBACK_DAYS * DAY_MS)), date);
  // A tick in any file is a pick; a candidate stays picked once picked.
  const ticked = [...earlier, ...(existing ? parseAnnotations(existing) : [])].filter(([, a]) => a.checked).map(([id]) => id);
  if (ticked.length) await sql`UPDATE candidate_state SET status = 'picked', updated_at = now() WHERE candidate_id = ANY(${ticked}) AND status = 'new'`;

  const rows = (await sql<{ candidate_id: string; status: StateRow["status"]; first_listed_at: Date; snapshot: Candidate }[]>`
    SELECT candidate_id, status, first_listed_at, snapshot FROM candidate_state`)
    .map((r): StateRow => ({ id: r.candidate_id, status: r.status, firstListedAt: r.first_listed_at, snapshot: r.snapshot }));
  const plan = planInbox(fresh, rows, now, date, dayOf);

  // A source that failed its last three collections is listed at the foot (COL-FR-004).
  const failing = (await sql<{ name: string; fail_count: number; last_error: string | null; last_ok_at: Date | null }[]>`
    SELECT name, fail_count, last_error, last_ok_at FROM sources WHERE enabled AND fail_count >= ${FAIL_LISTED} ORDER BY fail_count DESC, name`)
    .map((s): FailingSource => ({ name: s.name, failCount: s.fail_count, lastError: s.last_error, lastOkAt: s.last_ok_at ? `${dayOf(s.last_ok_at)} ${clock(s.last_ok_at, { hour: "2-digit", minute: "2-digit" })}` : null }));
  // With nothing to list, every source healthy and no inbox yet, there is nothing to write; an existing one is brought up to date.
  if (!plan.listed.length && !ranking && !failing.length && existing === null) return null;
  const text = withHealth(renderFile(date, plan.listed, existing, earlier), failing);
  await writeAtomic(file, text);

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
    name: "fetch-selftest",
    cron: "40 6 * * *",
    missed: "skip",
    run: async () => {
      const checks = await runSelftest(await loadTargets());
      return { failed: checks.filter(c => c.status === "fail").map(c => `${c.name} ${c.url ?? ""}：${c.detail}`), warned: checks.filter(c => c.status === "warn").map(c => `${c.name}：${c.detail}`) };
    },
  }, {
    name: "newsletter-split",
    cron: "20,50 * * * *",
    missed: "skip",
    run: () => splitNewsletters(),
  }, {
    name: "signals-snapshot",
    cron: "5 * * * *",
    missed: "skip",
    run: () => collectSignals({ githubToken: credential("collectors", "GITHUB_TOKEN") }),
  }, {
    name: "page-watch",
    cron: "*/10 * * * *",
    missed: "skip",
    run: () => runWatchPoll(),
  }, {
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
