// Downloads the media of a topic: what its pages give directly (L2, FET-FR-003), then, for the sources that gave no video,
// what the downloaders can fetch (L3, FET-FR-004); then takes stock of the media (assets.json).
// Output follows IF-020 with --json; exit 2 on bad input.
// node --env-file=.env scripts/fetch-media.ts <topic_id> [--layers 2,3,4] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { inventory } from "@aihot/inbox/assets";
import { fetchLayer2 } from "@aihot/inbox/layer2";
import { fetchLayer3 } from "@aihot/inbox/layer3";
import { fetchLayer4 } from "@aihot/inbox/layer4";
import { OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { json: { type: "boolean" }, layers: { type: "string" } } });
const layers = new Set((values.layers ?? "2,3,4").split(",").map(s => s.trim()));
try {
  if (positionals.length !== 1) throw new OpenTopicError("用法：fetch-media <topic_id> [--layers 2,3,4] [--json]");
  for (const l of layers) if (l !== "2" && l !== "3" && l !== "4") throw new OpenTopicError(`--layers 只能是 2、3、4：${l}`);
  const topicId = positionals[0]!;
  const l2 = layers.has("2") ? await fetchLayer2(topicId) : { downloaded: [], skipped: [] };
  const haveVideo = new Set(l2.downloaded.filter(d => d.kind === "video").map(d => d.sourceId));
  const l3 = layers.has("3") ? await fetchLayer3(topicId, { haveVideo }) : { downloaded: [], failed: [], skipped: [] };
  const haveVideo4 = new Set([...haveVideo, ...l3.downloaded.filter(d => d.kind === "video").map(d => d.sourceId)]);
  const l4 = layers.has("4") ? await fetchLayer4(topicId, { haveVideo: haveVideo4 }) : { recorded: [], skipped: [] };
  const stock = await inventory(topicId);
  const data = { topicId, downloaded: [...l2.downloaded, ...l3.downloaded], recorded: l4.recorded, skipped: l2.skipped, notRecorded: l4.skipped, failed: l3.failed, assets: stock.assets.length, gaps: stock.gaps };
  const warnings = [...l2.skipped.map(s => `${s.url}：${s.reason}`), ...l3.failed.map(f => `${f.url}：${f.message}`), ...l4.skipped.filter(s => s.failed).map(s => `${s.url}：${s.reason}`), ...stock.warnings];
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "fetch-media", data, warnings, next: stock.gaps.length ? ["gather-assets：補缺口"] : [`draft ${topicId}`] }));
  else {
    for (const d of [...l2.downloaded, ...l3.downloaded]) console.log(`下載 ${d.kind}  ${d.path}  ${d.url}`);
    for (const r of l4.recorded) console.log(`錄影 video  ${r.path}  ${r.url}`);
    for (const s of l4.skipped.filter(s => s.failed)) console.log(`錄影失敗 ${s.url}：${s.reason}`);
    for (const s of l2.skipped) console.log(`略過 ${s.url}：${s.reason}`);
    for (const f of l3.failed) console.log(`失敗 ${f.url}：${f.message}`);
    if (l3.skipped.length) console.log(`L2 已取得影片，沒有再下載：${l3.skipped.join("、")}`);
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
