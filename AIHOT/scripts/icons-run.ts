// Prints iconCandidates for the HTML snippets of a JSON file (argv[2]: [{ html, base }]) when given one, and refreshes the sources' icons (refreshSourceIcons) otherwise.
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { iconCandidates, refreshSourceIcons } from "@aihot/backend/sources/icons";
import { exitAfterFlush } from "./flush.ts";

if (process.argv[2]) {
  const cases = JSON.parse(readFileSync(process.argv[2], "utf8")) as Array<{ html: string; base: string }>;
  console.log(JSON.stringify(cases.map((c) => iconCandidates(c.html, c.base))));
} else {
  console.log(JSON.stringify(await refreshSourceIcons()));
  await closeDb();
}
await exitAfterFlush();
