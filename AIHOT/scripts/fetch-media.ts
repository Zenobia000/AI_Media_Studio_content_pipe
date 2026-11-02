// Downloads the media the pages of a topic give directly (L2, FET-FR-003), then takes stock of the media (assets.json).
// Output follows IF-020 with --json; exit 2 on bad input.
// node --env-file=.env scripts/fetch-media.ts <topic_id> [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { inventory } from "@aihot/inbox/assets";
import { fetchLayer2 } from "@aihot/inbox/layer2";
import { OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { json: { type: "boolean" } } });
try {
  if (positionals.length !== 1) throw new OpenTopicError("用法：fetch-media <topic_id> [--json]");
  const topicId = positionals[0]!;
  const l2 = await fetchLayer2(topicId);
  const stock = await inventory(topicId);
  const data = { topicId, downloaded: l2.downloaded, skipped: l2.skipped, assets: stock.assets.length, gaps: stock.gaps };
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "fetch-media", data, warnings: [...l2.skipped.map(s => `${s.url}：${s.reason}`), ...stock.warnings], next: stock.gaps.length ? ["gather-assets：補缺口"] : [`draft ${topicId}`] }));
  else {
    for (const d of l2.downloaded) console.log(`下載 ${d.kind}  ${d.path}  ${d.url}`);
    for (const s of l2.skipped) console.log(`略過 ${s.url}：${s.reason}`);
    for (const g of stock.gaps) console.log(`缺口 ${g.need}：${g.reason}。建議：${g.suggestion}`);
    console.log(`素材共 ${stock.assets.length} 個`);
  }
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "fetch-media", error_code: err.code, message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
