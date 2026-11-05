// Notification helpers for the Python port's tests. argv[2] is a JSON file: { calls: [name, ...args][] } with pushSelected (id), resolveDelivery (id, input, actor),
// markStaleDeliveries, sendAlert (title, lines), wakeAgent (finding with ISO dates), flushAgentEvents, agentTransportFindings (ms), handoffs (key, ISO), pure formatters.
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { ensureContentTargets, markStaleDeliveries, resolveDelivery } from "@aihot/backend/notify/deliver";
import * as feishu from "@aihot/backend/notify/feishu";
import * as agent from "@aihot/backend/notify/agent";
import { pushSelected } from "@aihot/backend/notify/selected";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const dated = (f: any) => ({ ...f, since: f.since ? new Date(f.since) : undefined });
const out: unknown[] = [];
if (input.calls.some((c: string[]) => c[0] === "pushSelected")) await ensureContentTargets();
for (const [name, ...a] of input.calls) {
  try {
    if (name === "pushSelected") out.push(await pushSelected(a[0], a[1] ? new Date(a[1]) : undefined));
    else if (name === "resolveDelivery") out.push((await resolveDelivery(a[0], a[1], a[2])) ?? null);
    else if (name === "markStaleDeliveries") out.push(await markStaleDeliveries());
    else if (name === "sendAlert") out.push(await feishu.sendAlert(a[0], a[1]));
    else if (name === "wakeAgent") out.push(await agent.wakeAgent(dated(a[0]), a[1]));
    else if (name === "wakeDigest") out.push(await agent.wakeDigest(a[0], a[1]));
    else if (name === "flushAgentEvents") out.push((await agent.flushAgentEvents()) ?? null);
    else if (name === "agentTransportFindings") out.push(await agent.agentTransportFindings(a[0]));
    else if (name === "handoffs") out.push(await agent.handoffs(a[0], new Date(a[1])));
    else if (name === "formatAlert") out.push(feishu.formatAlert(dated(a[0]), new Date(a[1]), a[2], a[3]));
    else if (name === "formatReturn") out.push(feishu.formatReturn(a[0], a[1]));
    else if (name === "formatRecovery") out.push(feishu.formatRecovery(a[0], new Date(a[1]), a[2]));
    else if (name === "duration") out.push(feishu.duration(a[0]));
  } catch (error) {
    out.push({ error: (error as Error).message, name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
if (!input.calls.every((c: string[]) => ["duration", "formatAlert", "formatReturn", "formatRecovery"].includes(c[0]))) {
  await stopBoss();
  await closeDb();
}
process.exit(0);
