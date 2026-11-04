import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { alertText, notifyNewS } from "../alerts.ts";
import type { Candidate } from "../format.ts";

after(closeDb);
beforeEach(async () => { await sql`DELETE FROM candidate_state`; });

const cand = (id: string, over: Partial<Candidate> = {}): Candidate => ({
  id, title: `標題 ${id}`, tier: "S", participantCount: 3, sourceNames: ["甲", "乙", "丙"], firstSeen: "08:00",
  suggestedProgram: "ai-today", suggestedFormats: ["threads"], heatReason: "3 個來源共振", ...over,
});
const list = async (...cs: Candidate[]) => {
  for (const c of cs) await sql`INSERT INTO candidate_state (candidate_id, snapshot) VALUES (${c.id}, ${sql.json(c as never)})`;
};

test("a candidate that reaches S is told once, with title, sources and inbox", async () => {
  const sent: Array<{ title: string; lines: string[] }> = [];
  const send = async (title: string, lines: string[]) => { sent.push({ title, lines }); return "sent" as const; };
  const c = cand("cand_a");
  await list(c);
  assert.deepEqual(await notifyNewS([c], "inbox/2026-11-04.md", send), ["cand_a"]);
  assert.equal(sent.length, 1);
  assert.match(sent[0]!.title, /標題 cand_a/);
  assert.match(sent[0]!.lines.join("\n"), /3 個.*甲、乙、丙/);
  assert.match(sent[0]!.lines.join("\n"), /inbox\/2026-11-04\.md/);
});

test("the next round with the same S candidate tells nothing", async () => {
  let n = 0;
  const send = async () => { n++; return "sent" as const; };
  const c = cand("cand_b");
  await list(c);
  await notifyNewS([c], "f.md", send);
  assert.deepEqual(await notifyNewS([c], "f.md", send), []);
  assert.equal(n, 1);
});

test("only S that is new counts: A, carried, and already-picked candidates are not told", async () => {
  let n = 0;
  const send = async () => { n++; return "sent" as const; };
  const a = cand("cand_a2", { tier: "A" });
  const carried = cand("cand_c", { carried: "昨日" });
  const picked = cand("cand_p");
  await list(a, carried, picked);
  await sql`UPDATE candidate_state SET status = 'picked' WHERE candidate_id = 'cand_p'`;
  assert.deepEqual(await notifyNewS([a, carried, picked], "f.md", send), []);
  assert.equal(n, 0);
});

test("with the channel off it only logs, and the candidate is not told again when it is turned on", async () => {
  let n = 0;
  const send = async () => { n++; return "disabled" as const; };
  const c = cand("cand_d");
  await list(c);
  assert.deepEqual(await notifyNewS([c], "f.md", send), ["cand_d"]);
  assert.deepEqual(await notifyNewS([c], "f.md", async () => { n++; return "sent" as const; }), []);
  assert.equal(n, 1);
});

test("a send that fails is tried again next round", async () => {
  const c = cand("cand_e");
  await list(c);
  const orig = console.error;
  console.error = () => {};
  try {
    assert.deepEqual(await notifyNewS([c], "f.md", async () => { throw new Error("down"); }), []);
  } finally { console.error = orig; }
  assert.deepEqual(await notifyNewS([c], "f.md", async () => "sent"), ["cand_e"]);
});

test("the text names the heat reason when there is one", () => {
  assert.match(alertText(cand("x"), "f.md").lines.join("\n"), /為什麼是 S：3 個來源共振/);
  assert.doesNotMatch(alertText(cand("x", { heatReason: undefined }), "f.md").lines.join("\n"), /為什麼是 S/);
});
