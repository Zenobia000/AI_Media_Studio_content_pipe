// The success rate of each fetch layer over the last days (FET-FR-011), from what the layers really tried.
// node --env-file=.env scripts/fetch-stats.ts [--days 30] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { fetchStats } from "@aihot/inbox/attempts";

const { values } = parseArgs({ options: { json: { type: "boolean" }, days: { type: "string" } } });
const days = values.days === undefined ? 30 : Number(values.days);
try {
  if (!Number.isInteger(days) || days < 1 || days > 366) {
    console.error(`--days 要是 1 到 366 的整數：${values.days}`);
    process.exitCode = 2;
  } else {
    const stats = await fetchStats(days);
    if (values.json) console.log(JSON.stringify({ ok: true, tool: "fetch-stats", data: { days, layers: stats }, warnings: [], next: [] }));
    else if (!stats.length) console.log(`最近 ${days} 天沒有補料紀錄。`);
    else {
      console.log(`最近 ${days} 天各層成功率：`);
      for (const s of stats) console.log(`  ${s.name.padEnd(10)} ${String(s.ok).padStart(4)} / ${String(s.attempts).padEnd(4)} ${(s.rate * 100).toFixed(0)}%`);
    }
  }
} finally {
  await closeDb();
}
