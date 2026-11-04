// The S-tier alert (COL-FR-018): a candidate that reaches S for the first time is told once, with its title, how many
// independent sources carry it and where to find it in the inbox. Delivery is sendAlert (off unless FEISHU_INTERNAL_ENABLED,
// then it only logs); the candidate is marked either way, so turning the channel on later does not replay old news.
import { sql } from "@aihot/backend/db";
import { sendAlert } from "@aihot/backend/notify/feishu";
import type { Candidate } from "./format.ts";

export type Send = (title: string, lines: string[]) => Promise<"sent" | "disabled">;

export function alertText(c: Candidate, inboxFile: string): { title: string; lines: string[] } {
  return {
    title: `🔥 S 級選題：${c.title}`,
    lines: [
      `共振來源 ${c.participantCount} 個：${c.sourceNames.join("、")}`,
      ...(c.heatReason ? [`為什麼是 S：${c.heatReason}`] : []),
      `建議：${c.suggestedProgram}／${c.suggestedFormats.join("、")}`,
      `inbox：${inboxFile}（${c.id}）`,
    ],
  };
}

/** Tells each S candidate not told before, once. Returns the ids told (or logged, with the channel off). */
export async function notifyNewS(listed: readonly Candidate[], inboxFile: string, send: Send = sendAlert): Promise<string[]> {
  const hot = listed.filter(c => c.tier === "S" && !c.carried);
  if (!hot.length) return [];
  // Claim first so two overlapping rounds cannot both tell it; a failed send gives the claim back.
  const claimed = await sql<{ candidate_id: string }[]>`
    UPDATE candidate_state SET s_notified_at = now()
    WHERE candidate_id = ANY(${hot.map(c => c.id)}) AND s_notified_at IS NULL AND status = 'new'
    RETURNING candidate_id`;
  const told: string[] = [];
  for (const { candidate_id } of claimed) {
    const c = hot.find(x => x.id === candidate_id)!;
    try {
      const { title, lines } = alertText(c, inboxFile);
      await send(title, lines);
      told.push(candidate_id);
    } catch (err) {
      await sql`UPDATE candidate_state SET s_notified_at = NULL WHERE candidate_id = ${candidate_id}`;
      console.error(JSON.stringify({ level: "warn", msg: "S-tier alert not sent; retried next round", candidate: candidate_id, error: String(err) }));
    }
  }
  return told;
}
