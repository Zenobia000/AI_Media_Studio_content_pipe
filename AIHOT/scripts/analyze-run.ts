// Analyses the articles named on the command line (ids) with the engine's analyzeArticle and prints each outcome, for the Python port's tests to compare.
import { closeDb } from "@aihot/backend/db";
import { analyzeArticle } from "@aihot/backend/editorial/analyze";
import { failureKind } from "@aihot/backend/providers/receipts";
import { exitAfterFlush } from "./flush.ts";

const out = [];
for (const id of process.argv.slice(2)) {
  try {
    const r = await analyzeArticle(id);
    out.push(r && { stale: r.stale, needsBody: r.needsBody ?? false, reused: r.reused, output: r.output, receipts: r.receiptIds.length });
  } catch (error) {
    out.push({ error: failureKind(error), name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
await closeDb();
await exitAfterFlush();
