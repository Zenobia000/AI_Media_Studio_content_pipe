// Merges two inbox candidates into one before a topic is opened (COL-FR-021).
// node --env-file=.env scripts/merge-candidates.ts <cand_keep> <cand_merged> [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { mergeCandidates, OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { json: { type: "boolean" } } });
try {
  if (positionals.length !== 2) throw new OpenTopicError("用法：merge-candidates <要保留的候選> <併進去的候選>");
  const r = await mergeCandidates(positionals[0]!, positionals[1]!);
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "merge", data: r, warnings: [], next: ["open-topic " + positionals[0]] }));
  else console.log(r.topicId ? `已併入既有 topic ${r.topicId}` : "已合併，建題時會歸到同一個 topic。");
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "merge", error_code: err.code, message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
