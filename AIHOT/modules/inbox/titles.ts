// Chinese titles for candidates (COL-FR-012): the triage-inbox skill translates, this keeps the result. A candidate is
// translated once; without a stored title the inbox shows the original, so switching this off changes nothing else.
import { sql } from "@aihot/backend/db";
import type { Candidate } from "./format.ts";

const HAN = /[一-鿿]/;
export const needsTranslation = (title: string) => !HAN.test(title);

export class EmptyTitleError extends Error {
  constructor() {
    super("中文標題不能是空的");
  }
}

/** Stores the title; a candidate that already has one keeps it unless `force`. Returns whether it was written. */
export async function setTitleZh(candidateId: string, titleZh: string, force = false): Promise<boolean> {
  const title = titleZh.trim();
  if (!title) throw new EmptyTitleError();
  const rows = await sql`
    INSERT INTO candidate_titles (candidate_id, title_zh) VALUES (${candidateId}, ${title})
    ON CONFLICT (candidate_id) DO UPDATE SET title_zh = EXCLUDED.title_zh, set_at = now() WHERE ${force}
    RETURNING candidate_id`;
  return rows.length > 0;
}

/** The candidates with the Chinese title in place of the original. */
export async function withTitlesZh<T extends Pick<Candidate, "id" | "title">>(candidates: T[]): Promise<T[]> {
  if (!candidates.length) return candidates;
  const rows = await sql<{ candidate_id: string; title_zh: string }[]>`SELECT candidate_id, title_zh FROM candidate_titles WHERE candidate_id = ANY(${candidates.map(c => c.id)})`;
  const zh = new Map(rows.map(r => [r.candidate_id, r.title_zh]));
  return candidates.map(c => (zh.has(c.id) ? { ...c, title: zh.get(c.id)! } : c));
}

/** The listed candidates still in a foreign language and without a stored title. */
export async function pendingTitles(): Promise<Array<{ id: string; title: string }>> {
  const rows = await sql<{ candidate_id: string; snapshot: { title: string } }[]>`
    SELECT s.candidate_id, s.snapshot FROM candidate_state s LEFT JOIN candidate_titles t USING (candidate_id)
    WHERE t.candidate_id IS NULL AND s.status IN ('new', 'picked') ORDER BY s.first_listed_at DESC`;
  return rows.filter(r => needsTranslation(r.snapshot.title)).map(r => ({ id: r.candidate_id, title: r.snapshot.title }));
}
