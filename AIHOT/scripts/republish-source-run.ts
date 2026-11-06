// Re-derives every published article of the sources named on the command line (ids) with the engine's republishSource, and prints the results.
import { closeDb } from "@aihot/backend/db";
import { republishSource } from "@aihot/backend/publication/publish";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { exitAfterFlush } from "./flush.ts";

const out: Record<string, unknown> = {};
for (const id of process.argv.slice(2)) out[id] = await republishSource(id);
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
await exitAfterFlush();
