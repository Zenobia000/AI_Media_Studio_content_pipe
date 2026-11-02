// Writes today's inbox/YYYY-MM-DD.md (IF-001) from the public hot ranking, twice an hour.
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import type { ServerModule } from "@aihot/backend/modules";
import { config } from "@aihot/backend/config";
import { latestHotRanking } from "@aihot/backend/publication/hot";
import { ManagedBlockError, renderFile, tierOf, type Candidate } from "./format.ts";

const TIME_ZONE = "Asia/Taipei";
const inboxDir = () => process.env.INBOX_DIR ?? path.join(config.dataDir, "workspace", "inbox");

export async function writeInbox(now = new Date()): Promise<{ file: string; candidates: number } | null> {
  const ranking = await latestHotRanking();
  if (!ranking) return null;
  const clock = (d: Date, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("sv-SE", { timeZone: TIME_ZONE, ...opts }).format(d);
  const date = clock(now, { dateStyle: "short" });
  const candidates: Candidate[] = ranking.entries.map(e => ({
    id: `cand_${e.storyPublicId}`,
    title: e.title,
    tier: tierOf(e.participantCount),
    participantCount: e.participantCount,
    sourceNames: e.sourceNames,
    firstSeen: clock(new Date(e.firstReportAt), { hour: "2-digit", minute: "2-digit" }),
    suggestedProgram: "ai-today",
    suggestedFormats: ["threads"],
  }));
  const file = path.join(inboxDir(), `${date}.md`);
  const existing = await readFile(file, "utf8").catch(() => null);
  const text = renderFile(date, candidates, existing);
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, text, "utf8");
  await rename(tmp, file);
  return { file, candidates: candidates.length };
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
