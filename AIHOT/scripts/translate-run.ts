// Translation helpers for the Python port's tests. argv[2] is a JSON file: { op, ... }.
//   shield:    { html } → { html, tokens, links } of the block as the model sees it
//   unshield:  { translated, html } → the block put back (or null), shielding `html` first
//   segments:  { html } → the text of each translatable block of a body
//   rebase:    { previous, next, translated } → the translated wording on the new layout, or { mismatch: message }
//   article:   { ids } → translateArticle for each; pending: {} → translatePending; quotes: {} → translateQuotes
import { readFileSync } from "node:fs";
import * as cheerio from "cheerio";
import { closeDb } from "@aihot/backend/db";
import { rebaseTranslation, PresentationMismatch } from "@aihot/backend/content/presentation";
import { segmentsOf, shield, translateArticle, translatePending, translateQuotes, unshield } from "@aihot/backend/editorial/translate";
import { failureKind } from "@aihot/backend/providers/receipts";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
let out: unknown;
if (input.op === "shield") {
  out = shield(input.html);
} else if (input.op === "unshield") {
  out = unshield(input.translated, shield(input.html));
} else if (input.op === "segments") {
  const $ = cheerio.load(input.html, null, false);
  out = segmentsOf($).map((el) => $(el).html());
} else if (input.op === "rebase") {
  try {
    out = { html: rebaseTranslation(input.previous, input.next, input.translated) };
  } catch (error) {
    if (!(error instanceof PresentationMismatch)) throw error;
    out = { mismatch: error.message };
  }
} else {
  if (input.op === "article") {
    const results = [];
    for (const id of input.ids) {
      try {
        results.push(await translateArticle(id));
      } catch (error) {
        results.push({ error: failureKind(error), name: (error as Error).constructor.name });
      }
    }
    out = results;
  } else if (input.op === "pending") {
    out = await translatePending();
  } else {
    out = { stored: await translateQuotes() };
  }
  await closeDb();
}
console.log(JSON.stringify(out));
process.exit(0);
