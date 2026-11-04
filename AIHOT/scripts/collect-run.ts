// Collects the sources named on the command line (ids) with the engine's collectSource, one after the other, and prints each result.
import { closeDb } from "@aihot/backend/db";
import { collectSource } from "@aihot/backend/sources/collect";
import { stopBoss } from "@aihot/backend/jobs/queue";

const results = [];
for (const id of process.argv.slice(2)) results.push(await collectSource(id));
console.log(JSON.stringify(results));
await stopBoss();
await closeDb();
