// Takes stock of the media of a topic: assets.json (IF-005) and media.md (FET-FR-007, FET-FR-009).
// Output follows IF-020 with --json; exit 2 on bad input.
// node --env-file=.env scripts/assets-topic.ts <topic_id> [--voiceover] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { inventory } from "@aihot/inbox/assets";
import { OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { voiceover: { type: "boolean" }, json: { type: "boolean" } } });
try {
  if (positionals.length !== 1) throw new OpenTopicError("用法：assets-topic <topic_id> [--voiceover] [--json]");
  const r = await inventory(positionals[0]!, { voiceover: values.voiceover });
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "assets", data: { ...r, assets: r.assets.length }, warnings: r.warnings, next: r.gaps.length ? ["gather-assets：補缺口"] : [`draft ${r.topicId}`] }));
  else {
    console.log(`素材 ${r.assets.length} 個（新增 ${r.added.length}、移除 ${r.removed.length}）`);
    for (const g of r.gaps) console.log(`缺口 ${g.need}：${g.reason}。建議：${g.suggestion}`);
    for (const w of r.warnings) console.log(`注意：${w}`);
  }
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "assets", error_code: err.code, message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
