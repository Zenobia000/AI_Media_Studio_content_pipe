// Operations helpers for the Python port's tests. argv[2] is a JSON file: { calls: [name, ...args][] } with
// dailyRetention (an ISO instant), recoverStaleWork, releaseReceipt (id, input, actor).
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { ensureQueue, QUEUES, stopBoss } from "@aihot/backend/jobs/queue";
import { checkAlerts, collectFindings, dismissFollowUp, holdAlert, restoreFollowUp, sendDigest } from "@aihot/backend/operations/alerts";
import { runBackup } from "@aihot/backend/operations/backup";
import { checkWorkerHeartbeat } from "@aihot/backend/operations/watch";
import { sourceHealthWeekly } from "@aihot/backend/operations/reports";
import { dailyRetention } from "@aihot/backend/operations/retention";
import { recoverStaleWork, releaseReceipt } from "@aihot/backend/operations/recover";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
await ensureQueue(QUEUES.group); // the queue's tables exist, as they do where the worker has run
for (const [name, ...args] of input.calls) {
  try {
    if (name === "dailyRetention") out.push(await dailyRetention(new Date(args[0])));
    else if (name === "recoverStaleWork") out.push(await recoverStaleWork());
    else if (name === "checkAlerts") out.push(await checkAlerts(args[0]));
    else if (name === "sendDigest") out.push(await sendDigest(args[0]));
    else if (name === "collectFindings") out.push((await collectFindings(args[0])).map((f) => ({ key: f.key, level: f.level, title: f.title, impact: f.impact ?? null, heals: f.heals ?? null, action: f.action ?? null, detail: f.detail ?? null, owner: f.owner ?? null })));
    else if (name === "sourceHealthWeekly") out.push(await sourceHealthWeekly(args[0]));
    else if (name === "holdAlert") out.push(await holdAlert(args[0], args[1], new Date(args[2]), args[3], args[4]));
    else if (name === "dismissFollowUp") out.push(await dismissFollowUp(args[0], args[1], args[2], args[3], args[4]));
    else if (name === "restoreFollowUp") out.push(await restoreFollowUp(args[0], args[1], args[2]));
    else if (name === "runBackup") out.push(await runBackup(new Date(args[0])));
    else if (name === "checkWorkerHeartbeat") out.push((await checkWorkerHeartbeat()) ?? null);
    else out.push((await releaseReceipt(args[0], args[1], args[2])) ?? null);
  } catch (error) {
    out.push({ error: (error as Error).message, name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
process.exit(0);
