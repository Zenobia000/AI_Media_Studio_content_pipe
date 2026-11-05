// Event helpers for the Python port's tests. argv[2] is a JSON file: { op, ... }.
//   digest: { ids: number[], rewrite?: boolean } → composeStoryDigest for each story
//   merge:  { pairs: [from, into][] } → mergeStoryInto for each pair
//   corrections: { calls: [name, ...args][] } → events/corrections.ts: detachFromFact, moveToFact, mergeStories, rewriteStoryDigest, requestRegroup
//   relate: { calls: [name, ...args][] } → the pure functions of events/relate.ts, with `at` fields turned into dates
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { composeStoryDigest } from "@aihot/backend/events/digest";
import { mergeStoryInto } from "@aihot/backend/events/merge";
import * as corrections from "@aihot/backend/events/corrections";
import * as relate from "@aihot/backend/events/relate";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { failureKind } from "@aihot/backend/providers/receipts";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
if (input.op === "digest") {
  for (const id of input.ids) {
    try {
      out.push(await composeStoryDigest(id, { rewrite: input.rewrite === true }));
    } catch (error) {
      out.push({ error: failureKind(error), name: (error as Error).constructor.name });
    }
  }
} else if (input.op === "merge") {
  for (const [from, into] of input.pairs) {
    try {
      out.push(await mergeStoryInto(from, into, input.reason ?? "test", input.actor ?? "editor"));
    } catch (error) {
      out.push({ error: (error as Error).message });
    }
  }
} else if (input.op === "corrections") {
  for (const [name, ...args] of input.calls) {
    try {
      out.push((await (corrections as any)[name](...args)) ?? null);
    } catch (error) {
      out.push({ error: (error as Error).message, name: (error as Error).constructor.name });
    }
  }
} else if (input.op === "relate") {
  const dated = (v: any): any => (Array.isArray(v) ? v.map(dated) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, k === "at" && typeof x === "string" ? new Date(x) : dated(x)])) : v);
  const entries = (m: Map<number, unknown>) => [...m.entries()];
  for (const [name, ...args] of input.calls) {
    const a = dated(args);
    if (name === "verdictsByFact") out.push(entries(relate.verdictsByFact(a[0], a[1])));
    else if (name === "batchSchema") { const r = relate.BatchSchema.refine((v) => relate.completeDecisions(v.decisions, a[1]), "x").safeParse(a[0]); out.push(r.success ? r.data : null); }
    else if (name === "pairSchema") { const r = relate.PairSchema.safeParse(a[0]); out.push(r.success ? r.data : null); }
    else if (name === "signalSchema") { const r = relate.SignalSchema.refine((v) => relate.completeDecisions(v.decisions, a[1]), "x").safeParse(a[0]); out.push(r.success ? r.data : null); }
    else if (name === "rules") {
      const verdicts = new Map<number, any>(a[1].map(([k, v]: [number, unknown]) => [k, v]));
      out.push({ same: relate.sameOccurrence(a[0], verdicts).map((c) => c.factId), dev: relate.storyForDevelopment(a[0], verdicts)?.factId ?? null, signal: relate.signalTarget(a[0], verdicts)?.factId ?? null });
    } else out.push((relate as any)[name](...a));
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
process.exit(0);
