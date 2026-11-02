// SPK-FET-1: the 59 samples through the extractor T-023 ships (modules/inbox/source-extract.ts): the engine's own
// fetcher with its declared User-Agent, the sites' own interfaces, Readability; no Jina (no key). Writes e2e.json.
// DATABASE_URL=postgres://127.0.0.1:5432/x node spikes/fet1/e2e.ts
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { extractSource, ExtractFailure } from "../../AIHOT/modules/inbox/source-extract.ts";

const here = import.meta.dirname;
const samples = JSON.parse(readFileSync(path.join(here, "samples.json"), "utf8")) as Array<{ group: string; from: string; url: string }>;
const out: Array<{ group: string; from: string; url: string; ok: boolean; method?: string; chars?: number; reason?: string }> = [];
for (const s of samples) {
  try {
    const x = await extractSource(s.url);
    out.push({ ...s, ok: true, method: x.method, chars: x.markdown.length });
  } catch (error) {
    if (!(error instanceof ExtractFailure)) throw error;
    out.push({ ...s, ok: false, reason: error.message });
  }
}
writeFileSync(path.join(here, "e2e.json"), JSON.stringify(out, null, 2));
for (const g of [...new Set(out.map(o => o.group))]) {
  const rows = out.filter(o => o.group === g);
  console.log(`${g}: ${rows.filter(r => r.ok).length}/${rows.length}`);
}
console.log(`全部: ${out.filter(r => r.ok).length}/${out.length}`);
for (const r of out.filter(r => !r.ok)) console.log(`FAIL ${r.group} | ${r.url} | ${r.reason}`);
