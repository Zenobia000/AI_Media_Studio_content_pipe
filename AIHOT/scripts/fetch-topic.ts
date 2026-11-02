// Reads the text of every source of a topic into sources/<source_id>.md (FET-FR-001, FET-FR-002).
// Output follows IF-020 with --json; exit 3 (blocked) when no source could be read, 2 on bad input.
// node --env-file=.env scripts/fetch-topic.ts <topic_id> [--force] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { fetchTopic } from "@aihot/inbox/fetch";
import { OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { force: { type: "boolean" }, json: { type: "boolean" } } });
try {
  if (positionals.length !== 1) throw new OpenTopicError("用法：fetch-topic <topic_id> [--force] [--json]");
  const r = await fetchTopic(positionals[0]!, { force: values.force });
  if (r.blocked) {
    const why = r.failed.map(f => `${f.url}：${f.reason}`).join("；");
    if (values.json) console.log(JSON.stringify({ ok: false, tool: "fetch", error_code: "E_MISSING_SOURCE", message: `沒有任何來源抓到原文。${why}`, blocked: true, data: r, next: ["請把原文貼進 sources/manual-1.md，再執行 fetch"] }));
    else console.error(`沒有任何來源抓到原文，請把原文貼進 sources/manual-1.md。\n${why}`);
    process.exitCode = 3;
  } else if (values.json) console.log(JSON.stringify({ ok: true, tool: "fetch", data: r, warnings: r.failed.map(f => `${f.url}：${f.reason}`), next: [`assets ${r.topicId}`] }));
  else {
    for (const f of r.fetched) console.log(`${f.official ? "官方來源 " : ""}${f.sourceId}  ${f.method}  ${f.wordCount} 字  ${f.url}`);
    if (r.kept.length) console.log(`已存在、沒有重抓：${r.kept.join("、")}`);
    for (const f of r.failed) console.log(`失敗：${f.url}（${f.reason}）`);
  }
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "fetch", error_code: err.code, message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
