// calendar/events.md (COL-FR-017): the launch events a person lists. The table is read into the events table; the
// day before an event the inbox gets a preparation candidate, and for a day after one ends a story that names its
// keywords is graded S (heat.ts eventEnded).
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { sql } from "@aihot/backend/db";
import { dayOf, workspaceDir } from "./workspace.ts";

const OFFSET = "+08:00";
const DEFAULT_HOURS = 2;
/** How long after the end a story still counts as about the event. */
export const AFTER_END_MS = 24 * 3600_000;
const DAY_MS = 24 * 3600_000;

export interface CalendarEvent {
  id: string;
  title: string;
  startsAt: Date;
  endsAt: Date;
  url: string | null;
  keywords: string[];
}

/**
 * Reads the table: 日期 | 時間 | 活動 | 關鍵字 | 官方連結. 時間 is "10:00" (two hours long) or "10:00-12:30" in Taipei
 * time; keywords are separated by commas. A row that is empty or has no readable date and time is left out, and
 * says why in `problems`.
 */
export function parseEvents(text: string): { events: CalendarEvent[]; problems: string[] } {
  const events: CalendarEvent[] = [];
  const problems: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim().startsWith("|")) continue;
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
    if (cells.length < 3 || cells[0] === "日期" || /^[-: ]+$/.test(cells[0]!) || cells.every(c => !c)) continue;
    const [date = "", time = "", title = "", kw = "", url = ""] = cells;
    const t = /^(\d{1,2}):(\d{2})(?:\s*[-~–]\s*(\d{1,2}):(\d{2}))?$/.exec(time);
    const startsAt = /^\d{4}-\d{2}-\d{2}$/.test(date) && t ? new Date(`${date}T${t[1]!.padStart(2, "0")}:${t[2]}:00${OFFSET}`) : null;
    if (!title || !startsAt || Number.isNaN(startsAt.getTime())) {
      problems.push(`略過：${line.trim()}（要有 YYYY-MM-DD 日期、HH:MM 時間和活動名稱）`);
      continue;
    }
    let endsAt = new Date(startsAt.getTime() + DEFAULT_HOURS * 3600_000);
    if (t![3]) {
      endsAt = new Date(`${date}T${t![3].padStart(2, "0")}:${t![4]}:00${OFFSET}`);
      if (endsAt <= startsAt) endsAt = new Date(endsAt.getTime() + DAY_MS);
    }
    events.push({
      id: `evt_${createHash("sha1").update(`${date}|${title}`).digest("hex").slice(0, 12)}`,
      title,
      startsAt,
      endsAt,
      url: /^https?:\/\//.test(url) ? url : null,
      keywords: kw.split(/[,，、]/).map(k => k.trim()).filter(Boolean),
    });
  }
  return { events, problems };
}

/** Brings the events table in line with the file: what is listed is there, what was taken out is gone. */
export async function syncEvents(file = path.join(workspaceDir(), "calendar", "events.md")): Promise<{ events: CalendarEvent[]; problems: string[] }> {
  const text = await readFile(file, "utf8").catch(() => null);
  if (text === null) return { events: [], problems: [] };
  const parsed = parseEvents(text);
  for (const e of parsed.events) {
    await sql`
      INSERT INTO events (event_id, title, starts_at, ends_at, url, keywords)
      VALUES (${e.id}, ${e.title}, ${e.startsAt}, ${e.endsAt}, ${e.url}, ${e.keywords})
      ON CONFLICT (event_id) DO UPDATE SET title = EXCLUDED.title, starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at, url = EXCLUDED.url, keywords = EXCLUDED.keywords`;
  }
  const ids = parsed.events.map(e => e.id);
  await sql`DELETE FROM events WHERE event_id <> ALL(${ids})`;
  return parsed;
}

/** Events that ended within the last day: a story naming one of their keywords is about it. */
export const justEnded = (events: CalendarEvent[], now: Date) => events.filter(e => e.endsAt <= now && now.getTime() - e.endsAt.getTime() < AFTER_END_MS);

/** True when the title names a keyword of an event that just ended (case does not matter). */
export function mentionsEndedEvent(title: string, ended: CalendarEvent[]): boolean {
  const t = title.toLowerCase();
  return ended.some(e => e.keywords.some(k => t.includes(k.toLowerCase())));
}

/** The events that start tomorrow (Taipei) and have not started: each gets a preparation candidate today. */
export const startingTomorrow = (events: CalendarEvent[], now: Date) =>
  events.filter(e => e.startsAt > now && dayOf(e.startsAt) === dayOf(new Date(now.getTime() + DAY_MS)));
