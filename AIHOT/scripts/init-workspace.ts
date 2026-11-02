// Makes the workspace (CORE-FR-001): folders, templates, the workspace CLAUDE.md and the skills links.
// Only what is missing is made; running it again changes nothing. Output follows IF-020 with --json.
// node scripts/init-workspace.ts [path]      path defaults to ./newsroom
import path from "node:path";
import { parseArgs } from "node:util";
import { initWorkspace } from "@aihot/inbox/init";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { json: { type: "boolean" } } });
const root = path.resolve(positionals[0] ?? "newsroom");
try {
  const r = await initWorkspace(root);
  const next = [`設定環境變數 NEWSROOM_HOME=${root}`, "node scripts/migrate.ts（建立資料表）", "編輯 voice.md，寫下口吻", "node scripts/doctor.ts 檢查環境"];
  if (values.json) console.log(JSON.stringify({ ok: true, tool: "init", data: r, warnings: r.warnings, next }));
  else {
    console.log(`工作區：${root}`);
    console.log(r.created.length ? `新建 ${r.created.length} 項：${r.created.join("、")}` : "沒有需要新建的，已是完整的工作區。");
    if (r.existing.length && r.created.length) console.log(`原本就有，沒有動：${r.existing.length} 項`);
    for (const w of r.warnings) console.log(`注意：${w}`);
    console.log(`\n下一步：\n${next.map(n => `- ${n}`).join("\n")}`);
  }
} catch (error) {
  const message = (error as Error).message;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "init", error_code: "E_INPUT", message, blocked: false }));
  else console.error(`建立工作區失敗：${message}`);
  process.exitCode = 1;
}
