// Runs the analysis job (processArticle) for the articles named on the command line, once each, and prints the outcomes, for the Python port's tests to compare.
import { closeDb } from "@aihot/backend/db";
import { processArticle } from "@aihot/backend/jobs/content";
import { stopBoss } from "@aihot/backend/jobs/queue";

const out = [];
for (const id of process.argv.slice(2)) {
  const r = await processArticle(id);
  out.push({ state: r.state, retryInMinutes: r.retryAt ? Math.round((r.retryAt.getTime() - Date.now()) / 60_000) : null });
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
process.exit(0);
