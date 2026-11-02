// SPK-FET-1: turns results.json into the numbers of docs/spikes/SPK-FET-1.md.
// node spikes/fet1/report.ts
import { readFileSync } from "node:fs";
import path from "node:path";

interface Run { ok: boolean; chars: number; ms: number; noise: number; date: string | null; error?: string }
interface Row { group: string; from: string; url: string; feedDate: string | null; A: Run; B: Run; C?: Run }
const rows = JSON.parse(readFileSync(path.join(import.meta.dirname, "results.json"), "utf8")) as Row[];

const pct = (n: number, d: number) => (d ? `${((100 * n) / d).toFixed(0)}%` : "—");
const median = (xs: number[]) => (xs.length ? [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]! : 0);
const day = (s: string | null) => (s && Number.isFinite(Date.parse(s)) ? Date.parse(s) : null);

type Pick = (r: Row) => Run | undefined;
const methods: Array<[string, Pick]> = [["A Readability", r => r.A], ["B Jina Reader", r => r.B], ["C 站方 API", r => r.C]];

function table(title: string, subset: Row[]) {
  console.log(`\n### ${title}（${subset.length} 篇）\n`);
  // No completeness column: "relative to the longest version" rewards Jina's page chrome (checked by hand on iThome, where
  // Jina returned 16,926 characters around a 910-character article). Completeness needs a human-marked body.
  console.log("| 方案 | 成功率（≥200 字） | 雜訊行比例（平均） | 日期正確率（可比對者） | 耗時中位數 |");
  console.log("|---|---|---|---|---|");
  for (const [name, pick] of methods) {
    const rs = subset.map(r => ({ r, run: pick(r) })).filter((x): x is { r: Row; run: Run } => !!x.run);
    if (!rs.length) continue;
    const ok = rs.filter(x => x.run.ok);
    const dated = rs.filter(x => day(x.r.feedDate) !== null && day(x.run.date) !== null);
    const dateOk = dated.filter(x => Math.abs(day(x.run.date)! - day(x.r.feedDate)!) <= 86_400_000);
    const noise = ok.length ? ok.reduce((s, x) => s + x.run.noise, 0) / ok.length : 0;
    console.log(`| ${name} | ${pct(ok.length, rs.length)}（${ok.length}/${rs.length}） | ${(100 * noise).toFixed(1)}% | ${dated.length ? `${pct(dateOk.length, dated.length)}（${dateOk.length}/${dated.length}）` : "—"} | ${median(rs.map(x => x.run.ms))} ms |`);
  }
}

table("全部", rows);
for (const g of [...new Set(rows.map(r => r.group))]) table(g, rows.filter(r => r.group === g));

// The recommended order: the site's own API where there is one, then Readability, then Jina.
const chain = (r: Row) => !!(r.C?.ok || r.A.ok || r.B.ok);
const readOnly = (r: Row) => !!(r.C?.ok || r.A.ok);
console.log(`\n### 建議順序的覆蓋率\n`);
console.log(`- 站方 API → Readability：${pct(rows.filter(readOnly).length, rows.length)}（${rows.filter(readOnly).length}/${rows.length}）`);
console.log(`- 站方 API → Readability → Jina：${pct(rows.filter(chain).length, rows.length)}（${rows.filter(chain).length}/${rows.length}）`);
console.log(`\n### 三個方案都失敗的樣本\n`);
for (const r of rows.filter(r => !chain(r))) console.log(`- ${r.group}｜${r.from}｜${r.url}｜A:${r.A.error ?? `${r.A.chars} 字`}｜B:${r.B.error ?? `${r.B.chars} 字`}`);
console.log(`\n### Readability 失敗、Jina 救回的樣本\n`);
for (const r of rows.filter(r => !r.A.ok && !r.C?.ok && r.B.ok)) console.log(`- ${r.group}｜${r.from}｜${r.url}｜A:${r.A.error ?? `${r.A.chars} 字`}｜B:${r.B.chars} 字`);
