// Report helpers for the Python port's tests. argv[2] is a JSON file: { op, calls }.
//   pure: calls [name, ...args] of reports/compose.ts: dueDaily/dueWeekly/dueMonthly (ISO instants), grounded, fitted, periodPrompt, dailyMetrics
//   run:  calls [name, ...args] against the database: composeDaily, composeWeekly, composeMonthly, composeDueReports (an ISO instant, a limit)
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import * as compose from "@aihot/backend/reports/compose";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { exitAfterFlush } from "./flush.ts";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
for (const [name, ...args] of input.calls) {
  try {
    if (input.op === "pure") {
      const a = name.startsWith("due") ? [new Date(args[0])] : args;
      out.push((compose as any)[name](...a));
    } else if (name === "composeDueReports") out.push(await compose.composeDueReports(new Date(args[0]), args[1]));
    else out.push((await (compose as any)[name](...args)) ?? null);
  } catch (error) {
    out.push({ error: (error as Error).message });
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
await exitAfterFlush();
