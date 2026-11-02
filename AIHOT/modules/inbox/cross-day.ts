// Which candidates today's inbox lists (COL-FR-015). A candidate that was not ticked the day before and is
// within 48 hours since it was first listed is brought along ("昨日"); after that it is skipped. A ticked one
// stays until it has been opened as a topic. Skipped and opened ones are not listed again.
import type { Candidate } from "./format.ts";

export type Status = "new" | "picked" | "opened" | "skipped";

export interface StateRow {
  id: string;
  status: Status;
  firstListedAt: Date;
  /** The candidate as it was last listed, so a day it has dropped out of the ranking it can still be written. */
  snapshot: Candidate;
}

export const CARRY_MS = 48 * 3600_000;

export interface Plan {
  listed: Candidate[];
  /** Candidates whose 48 hours are over without a tick. */
  skip: string[];
}

/** `day` turns an instant into its inbox date; `today` is the inbox being written. */
export function planInbox(fresh: readonly Candidate[], rows: readonly StateRow[], now: Date, today: string, day: (d: Date) => string): Plan {
  const byId = new Map(rows.map(r => [r.id, r]));
  const expired = (r: StateRow) => r.status === "new" && now.getTime() - r.firstListedAt.getTime() > CARRY_MS;
  const gone = (id: string) => {
    const r = byId.get(id);
    return !!r && (r.status === "skipped" || r.status === "opened" || expired(r));
  };
  const carriedFrom = (r: StateRow): Candidate["carried"] => (day(r.firstListedAt) === today ? undefined : r.status === "picked" ? "已勾選" : "昨日");

  const listed: Candidate[] = fresh.filter(c => !gone(c.id)).map(c => {
    const r = byId.get(c.id);
    return r && carriedFrom(r) ? { ...c, carried: carriedFrom(r) } : c;
  });
  const shown = new Set(listed.map(c => c.id));
  for (const r of rows) {
    if (shown.has(r.id) || gone(r.id)) continue;
    if (r.status === "new" || r.status === "picked") listed.push({ ...r.snapshot, carried: carriedFrom(r) });
  }
  return { listed, skip: rows.filter(expired).map(r => r.id) };
}
