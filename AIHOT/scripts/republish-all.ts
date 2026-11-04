// Re-derives every article's publication, in id order, with the engine's own publishArticle. The Python port's tests run this and the Python
// version on copies of one database and compare the results.
import { closeDb, sql } from "@aihot/backend/db";
import { publishArticle } from "@aihot/backend/publication/publish";
import { stopBoss } from "@aihot/backend/jobs/queue";

const ids = await sql<{ id: string }[]>`SELECT id FROM articles ORDER BY id`;
const results: Record<string, unknown> = {};
for (const { id } of ids) results[id] = await publishArticle(id);
console.log(JSON.stringify(results));
await stopBoss();
await closeDb();
