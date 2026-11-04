// Reads each page of a JSON file (argv[2]: [{ html, url, rules? }]) with the engine's article reader (content/extract.ts readable) and prints the results.
import { readFileSync } from "node:fs";
import { readable } from "@aihot/backend/content/extract";

const pages = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as Array<{ html: string; url: string; rules?: never }>;
const out = pages.map((page) => {
  try {
    const got = readable(page.html, page.url, page.rules);
    return got ? { ...got, publishedAt: got.publishedAt?.toISOString() ?? null } : null;
  } catch (error) {
    return { error: String((error as Error).message).slice(0, 200) };
  }
});
console.log(JSON.stringify(out));
process.exit(0);
