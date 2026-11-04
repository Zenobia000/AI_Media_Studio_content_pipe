import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import type { Candidate } from "../format.ts";
import { runChecks, type Deps } from "../doctor.ts";
import { DeniedError, QuotaError, threadsSearch, withLocalHeat, type Search } from "../local-heat.ts";

after(closeDb);
beforeEach(async () => { await sql`DELETE FROM local_heat`; });

const cand = (id: string, over: Partial<Candidate> = {}): Candidate => ({
  id, title: `${id} 發布新模型`, tier: "S", participantCount: 3, sourceNames: ["甲"], firstSeen: "08:00", suggestedProgram: "ai-today", suggestedFormats: ["threads"], ...over,
});
const now = new Date("2026-11-04T08:00:00Z");

test("S and A candidates get a 在地 answer: covered with enough posts, a gap with few", async () => {
  const seen: string[] = [];
  const search: Search = async q => { seen.push(q); return q.startsWith("cand_hot") ? 12 : 1; };
  const out = await withLocalHeat([cand("cand_hot"), cand("cand_new", { tier: "A" }), cand("cand_b", { tier: "B" })], { search, now });
  assert.match(out[0]!.local!, /中文圈已有 12 則/);
  assert.match(out[1]!.local!, /還沒人報導.*缺口/);
  assert.equal(out[2]!.local, undefined, "B is not checked");
  assert.deepEqual(seen.sort(), ["cand_hot 發布新模型", "cand_new 發布新模型"]);
});

test("a candidate checked within a day is not searched again", async () => {
  let n = 0;
  const search: Search = async () => { n++; return 5; };
  await withLocalHeat([cand("cand_once")], { search, now });
  await withLocalHeat([cand("cand_once")], { search, now: new Date(now.getTime() + 3600_000) });
  assert.equal(n, 1);
  await withLocalHeat([cand("cand_once")], { search, now: new Date(now.getTime() + 25 * 3600_000) });
  assert.equal(n, 2);
});

test("with the allowance used up the inbox says 未檢查 and does not fail", async () => {
  let n = 0;
  const search: Search = async () => { n++; return 5; };
  const out = await withLocalHeat([cand("cand_1"), cand("cand_2"), cand("cand_3")], { search, now, budget: 1 });
  assert.equal(n, 1);
  assert.match(out[0]!.local!, /已有/);
  assert.match(out[1]!.local!, /未檢查（今天的搜尋額度用完）/);
  assert.match(out[2]!.local!, /未檢查/);
  const quota = await withLocalHeat([cand("cand_q")], { search: async () => { throw new QuotaError(); }, now: new Date(now.getTime() + 48 * 3600_000) });
  assert.match(quota[0]!.local!, /未檢查（搜尋額度用完|今天的搜尋額度用完）/);
});

test("without access it says why and searches nothing; a refusal stops the rest of the round", async () => {
  const none = await withLocalHeat([cand("cand_n")], { search: null, now });
  assert.match(none[0]!.local!, /未檢查（還沒設定 Threads 搜尋權限/);

  let n = 0;
  const denied: Search = async () => { n++; throw new DeniedError("Threads 搜尋被拒絕（HTTP 403），權限可能還沒通過審核"); };
  const out = await withLocalHeat([cand("cand_d1"), cand("cand_d2")], { search: denied, now });
  assert.equal(n, 1);
  assert.match(out[1]!.local!, /被拒絕/);
});

test("a candidate with only a foreign title is not searched in Chinese", async () => {
  let n = 0;
  const out = await withLocalHeat([cand("cand_en", { title: "Acme releases a model" })], { search: async () => { n++; return 9; }, now });
  assert.equal(n, 0);
  assert.match(out[0]!.local!, /未檢查（還沒有中文標題/);
});

test("the Threads search maps its answers: posts counted, 429 is the allowance, 403 is the access", async () => {
  const reply = (status: number, body: unknown = {}) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  assert.equal(await threadsSearch("t", reply(200, { data: [{ id: "1" }, { id: "2" }] }))("x"), 2);
  await assert.rejects(threadsSearch("t", reply(429))("x"), QuotaError);
  await assert.rejects(threadsSearch("t", reply(403))("x"), DeniedError);
  await assert.rejects(threadsSearch("t", reply(500))("x"), /HTTP 500/);
});

test("doctor says what the local check needs when there is no token", async () => {
  const deps = (env: NodeJS.ProcessEnv): Deps => ({ exec: async () => ({ ok: true, out: "x" }), env, reach: async () => true, database: async () => null, nodeVersion: "24.22.1", workspace: "/w" });
  const missing = (await runChecks(deps({ DATABASE_URL: "x" }))).find(c => c.name === "THREADS_ACCESS_TOKEN")!;
  assert.equal(missing.status, "warn");
  assert.match(missing.fix!, /threads_keyword_search/);
  assert.equal((await runChecks(deps({ DATABASE_URL: "x", THREADS_ACCESS_TOKEN: "t" }))).find(c => c.name === "THREADS_ACCESS_TOKEN")!.status, "ok");
  void tag;
});
