// Opens a topic from ticked inbox candidates (COL-FR-020). Output follows IF-020 with --json; exit 2 on bad input.
// node --env-file=.env scripts/open-topic.ts <cand_id>... [--program ai-today] [--formats threads,reel] [--angle "..."] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { openTopic, OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { program: { type: "string" }, formats: { type: "string" }, angle: { type: "string" }, json: { type: "boolean" } },
});
try {
  const r = await openTopic({
    candidateIds: positionals, program: values.program, angle: values.angle,
    formats: values.formats?.split(",").map(f => f.trim()).filter(Boolean),
  });
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "open-topic", data: r, warnings: r.warnings, next: [`fetch ${r.topicId}`] }));
  else {
    console.log(`${r.created ? "已建立" : "沿用"} topic ${r.topicId}`);
    if (r.contentsCreated.length) console.log(`新增 content：${r.contentsCreated.join("、")}`);
    if (r.contentsExisting.length) console.log(`已存在、沒有重建：${r.contentsExisting.join("、")}`);
    for (const w of r.warnings) console.log(`注意：${w}`);
  }
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "open-topic", error_code: err.code, message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
