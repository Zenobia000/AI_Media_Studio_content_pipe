// Stores the materials listed in a JSON file (argv[2]) one after the other with the engine's upsertMaterial, and prints each result, for the
// Python port's tests to compare. Dates are ISO strings in the file.
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { stopBoss } from "@aihot/backend/jobs/queue";

const date = (v: unknown) => (typeof v === "string" ? new Date(v) : v === null ? null : undefined);
const items = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as Array<Record<string, any>>;
const results = [];
for (const it of items) {
  results.push(await upsertMaterial({ ...it, publishedAt: date(it.publishedAt), sourceUpdatedAt: date(it.sourceUpdatedAt), discoveredAt: date(it.discoveredAt) } as never));
}
console.log(JSON.stringify(results));
await stopBoss();
await closeDb();
