// Operations helpers for the Python port's tests. argv[2] is a JSON file: { calls: [name, ...args][] } with
// dailyRetention (an ISO instant), recoverStaleWork, releaseReceipt (id, input, actor).
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { dailyRetention } from "@aihot/backend/operations/retention";
import { recoverStaleWork, releaseReceipt } from "@aihot/backend/operations/recover";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
for (const [name, ...args] of input.calls) {
  try {
    if (name === "dailyRetention") out.push(await dailyRetention(new Date(args[0])));
    else if (name === "recoverStaleWork") out.push(await recoverStaleWork());
    else out.push((await releaseReceipt(args[0], args[1], args[2])) ?? null);
  } catch (error) {
    out.push({ error: (error as Error).message, name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
process.exit(0);
