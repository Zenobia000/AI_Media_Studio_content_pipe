// Runs publication-date corrections with the engine's own functions, for the parity test. argv[2] is a JSON list of steps
// { name, id, publishedAt, requestId, tamper? }: each previews the correction, then applies it with the preview's version and hash
// (tamper "hash" sends a wrong hash). Prints one JSON line: for every step the plan (or the error) and the result (or the error).
import { closeDb } from "@aihot/backend/db";
import { correctPublicationDate, previewPublicationDateCorrection } from "@aihot/backend/admin/content";
import { getBoss, stopBoss } from "@aihot/backend/jobs/queue";
import { exitAfterFlush } from "./flush.ts";

interface Step { name: string; id: string; publishedAt: string | null; requestId: string; tamper?: string }
const fail = (e: unknown) => ({ error: e instanceof Error ? e.message : String(e), kind: e instanceof Error ? e.constructor.name : "other" });

await getBoss();
const out: Record<string, unknown>[] = [];
for (const step of JSON.parse(process.argv[2]) as Step[]) {
  const entry: Record<string, unknown> = { name: step.name };
  let plan;
  try {
    plan = await previewPublicationDateCorrection(step.id, step.publishedAt);
    entry.plan = plan;
  } catch (e) {
    entry.plan = fail(e);
  }
  if (plan) {
    try {
      entry.result = await correctPublicationDate(step.id, { version: plan.version, hash: step.tamper === "hash" ? "0".repeat(64) : plan.hash,
        publishedAt: step.publishedAt, requestId: step.requestId, reason: "parity check" }, "parity");
    } catch (e) {
      entry.result = fail(e);
    }
  }
  out.push(entry);
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
await exitAfterFlush();
