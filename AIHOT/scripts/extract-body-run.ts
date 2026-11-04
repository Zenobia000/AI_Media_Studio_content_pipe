// Fetches and stores the bodies of the articles named on the command line (ids) with the engine's extractArticleBody, and prints each outcome.
import { closeDb } from "@aihot/backend/db";
import { extractArticleBody } from "@aihot/backend/content/extract";
import { stopBoss } from "@aihot/backend/jobs/queue";

const results = [];
for (const id of process.argv.slice(2)) {
  try {
    results.push(await extractArticleBody(id));
  } catch (error) {
    results.push(`error: ${(error as Error).message}`);
  }
}
console.log(JSON.stringify(results));
await stopBoss();
await closeDb();
