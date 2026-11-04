// Reads one feed with the engine's fetchRss. argv[2] is a JSON file with the source row (config, cursor, …); prints the candidates as JSON.
import { readFileSync } from "node:fs";
import { fetchRss } from "@aihot/backend/sources/rss";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as { source: never; force?: boolean };
try {
  const read = await fetchRss(input.source, { force: input.force });
  console.log(JSON.stringify({ ok: true, ...read }));
} catch (error) {
  console.log(JSON.stringify({ ok: false, error: String((error as Error).message).slice(0, 200), name: (error as Error).constructor.name }));
}
process.exit(0);
