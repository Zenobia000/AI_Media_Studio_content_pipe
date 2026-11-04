// Runs each case of a JSON file (argv[2]) through the engine's chatJson against whatever LLM_BASE_URL names, and prints the outcomes, for the Python port's tests.
import { readFileSync } from "node:fs";
import { z } from "zod";
import { closeDb } from "@aihot/backend/db";
import { chatJson } from "@aihot/backend/providers/llm";
import { failureKind } from "@aihot/backend/providers/receipts";

const cases = JSON.parse(readFileSync(process.argv[2]!, "utf8")) as Array<Record<string, any>>;
const schema = z.object({ answer: z.string() });
const out = [];
for (const c of cases) {
  try {
    const r = await chatJson({ model: "default", purpose: "test_purpose", promptVersion: "p1", schema, subject: c.subject, system: c.system ?? "You answer in JSON.", user: c.user,
      temperature: c.temperature, maxTokens: c.maxTokens, attemptTag: c.attemptTag, timeoutMs: c.timeoutMs });
    out.push({ ok: true, data: r.data, reused: r.reused, model: r.model, usage: r.usage });
  } catch (error) {
    out.push({ ok: false, kind: failureKind(error), name: (error as Error).constructor.name, message: String((error as Error).message).slice(0, 120) });
  }
}
console.log(JSON.stringify(out));
await closeDb();
process.exit(0);
