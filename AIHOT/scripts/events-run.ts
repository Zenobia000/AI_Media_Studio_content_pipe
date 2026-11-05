// Event helpers for the Python port's tests. argv[2] is a JSON file: { op, ... }.
//   digest: { ids: number[], rewrite?: boolean } → composeStoryDigest for each story
//   merge:  { pairs: [from, into][] } → mergeStoryInto for each pair
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { composeStoryDigest } from "@aihot/backend/events/digest";
import { mergeStoryInto } from "@aihot/backend/events/merge";
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
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
process.exit(0);
