// Reads one JSON source with the engine's fetchJsonList. argv[2] is a JSON file with the source row; prints the candidates as JSON.
import { readFileSync } from "node:fs";
import { fetchJsonList } from "@aihot/backend/sources/json-list";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as { source: never };
try {
  console.log(JSON.stringify({ ok: true, candidates: await fetchJsonList(input.source) }));
} catch (error) {
  console.log(JSON.stringify({ ok: false, error: String((error as Error).message).slice(0, 200), name: (error as Error).constructor.name }));
}
process.exit(0);
