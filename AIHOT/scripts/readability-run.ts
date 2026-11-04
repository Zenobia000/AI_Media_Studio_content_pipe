// Runs Readability on each page of a JSON file (argv[2]: [{ html, charThreshold? }]) the way the engine's extraction does, and prints the articles as
// JSON, for the Python port's tests to compare.
import { readFileSync } from "node:fs";
import { Readability } from "@mozilla/readability";
import { parseHTML } from "linkedom";

const pages = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as Array<{ html: string; charThreshold?: number }>;
const out = pages.map((page) => {
  try {
    const { document } = parseHTML(page.html);
    const article = new Readability(document as never, { charThreshold: page.charThreshold ?? 200, keepClasses: false }).parse();
    return article ? { ...article } : null;
  } catch (error) {
    return { error: String((error as Error).message).slice(0, 200) };
  }
});
console.log(JSON.stringify(out));
