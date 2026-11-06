// Groups the articles named in the JSON file argv[2] ([{ id, signalOnly?, regroup? }]) one after the other with the engine's groupArticle, and prints each outcome,
// for the Python port's tests to compare. Then the hourly link step when asked ({ links: true } as the last element).
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { linkRelatedStories } from "@aihot/backend/events/consolidate";
import { groupArticle } from "@aihot/backend/events/group";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { failureKind } from "@aihot/backend/providers/receipts";
import { exitAfterFlush } from "./flush.ts";

const specs: Array<{ id?: string; signalOnly?: boolean; regroup?: boolean; links?: boolean }> = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
for (const spec of specs) {
  try {
    if (spec.links) out.push(await linkRelatedStories());
    else out.push(await groupArticle(spec.id!, { signalOnly: spec.signalOnly, regroup: spec.regroup }));
  } catch (error) {
    out.push({ error: failureKind(error), name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
await exitAfterFlush();
