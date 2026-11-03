// 各信源最近幾天的抓取成功率（T-040），來自 fetch_runs。
// node --env-file=.env scripts/source-stats.ts [--days 14] [--json]
import { parseArgs } from "node:util";
import { closeDb, sql } from "@aihot/backend/db";

const { values } = parseArgs({ options: { json: { type: "boolean" }, days: { type: "string" } } });
const days = values.days === undefined ? 14 : Number(values.days);
try {
  if (!Number.isInteger(days) || days < 1 || days > 366) {
    console.error(`--days 要是 1 到 366 的整數，收到：${values.days}`);
    process.exitCode = 2;
  } else {
    const rows = await sql<{ id: string; name: string; ok: number; failed: number; days_seen: number; last_error: string | null }[]>`
      SELECT s.id, s.name,
             count(*) FILTER (WHERE r.status = 'ok')::int AS ok,
             count(*) FILTER (WHERE r.status = 'failed')::int AS failed,
             count(DISTINCT r.started_at::date)::int AS days_seen,
             (array_agg(r.error ORDER BY r.started_at DESC) FILTER (WHERE r.status = 'failed'))[1] AS last_error
        FROM sources s LEFT JOIN fetch_runs r ON r.source_id = s.id AND r.started_at > now() - make_interval(days => ${days})
       GROUP BY s.id, s.name ORDER BY s.id`;
    const stats = rows.map((r) => ({ ...r, rate: r.ok + r.failed ? r.ok / (r.ok + r.failed) : null }));
    if (values.json) console.log(JSON.stringify({ ok: true, tool: "source-stats", data: { days, sources: stats }, warnings: [], next: [] }));
    else {
      console.log(`最近 ${days} 天各信源的抓取成功率：`);
      for (const s of stats) {
        const rate = s.rate === null ? "無紀錄" : `${(s.rate * 100).toFixed(0)}%`;
        console.log(`  ${String(s.id).padEnd(28)} ${String(s.ok).padStart(5)} 成功 ${String(s.failed).padStart(4)} 失敗 ${rate.padStart(6)}  有紀錄 ${s.days_seen} 天${s.last_error ? `  最近錯誤：${String(s.last_error).slice(0, 60)}` : ""}`);
      }
    }
  }
} finally {
  await closeDb();
}
