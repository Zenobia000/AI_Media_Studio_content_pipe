// Grades the stories a person labelled and says how many of the S ones the grading finds (T-075: at least 80% as S or A).
// The labels file is a list: [{ "story": "<public id>", "label": "S", "asOf": "2026-10-20T08:00:00+08:00" }, ...]
// node --env-file=.env scripts/backtest-heat.ts <labels.json> [--json]
import { readFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { backtestHeat, loadHeatConfig, type BacktestLabel } from "@aihot/inbox/heat";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { json: { type: "boolean" } } });
try {
  if (positionals.length !== 1) {
    console.error("用法：backtest-heat <labels.json> [--json]");
    process.exitCode = 2;
  } else {
    const labels = JSON.parse(await readFile(positionals[0]!, "utf8")) as BacktestLabel[];
    const r = await backtestHeat(labels, await loadHeatConfig());
    if (values.json) console.log(JSON.stringify({ ok: true, tool: "backtest-heat", data: r, warnings: r.rows.filter(x => !x.found).map(x => `找不到事件 ${x.story}`), next: [] }));
    else {
      for (const row of r.rows) console.log(`${row.found ? `${row.label} → ${row.tier}（${row.score} 分）` : `${row.label} → 找不到`}  ${row.story}`);
      console.log(r.sRate === null ? "\n沒有人工標記的 S 級事件，無法回測。" : `\n人工標記的 S 級：${r.sHits}／${r.sTotal} 被判為 S 或 A（${Math.round(r.sRate * 100)}%，目標 ≥ 80%）`);
    }
  }
} finally {
  await closeDb();
}
