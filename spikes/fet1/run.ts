// SPK-FET-1: compares body extraction on samples.json and writes results.json.
//   A  AIHOT's Readability pipeline on the page's HTML (content/extract.ts readable())
//   B  Jina Reader, free tier, no key (what AIHOT falls back to)
//   C  the site's own API, for the groups that have one (GitHub README, arXiv abstract)
// Crawl4AI was not run: it needs Python or Docker and a browser, which this machine does not have set up for it.
// DATABASE_URL=postgres://127.0.0.1:5432/x node spikes/fet1/run.ts
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { readable } from "../../AIHOT/packages/backend/src/content/extract.ts";

const here = import.meta.dirname;
interface Sample { group: string; from: string; url: string; feedDate: string | null }
const samples = JSON.parse(readFileSync(path.join(here, "samples.json"), "utf8")) as Sample[];
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const MIN_CHARS = 200;
const NOISE = /cookie|subscribe|sign up|newsletter|share this|related (posts|articles)|read more|privacy policy|all rights reserved|©|訂閱|分享|版權所有|相關(文章|閱讀)|點擊/i;

interface Run { ok: boolean; chars: number; ms: number; noise: number; date: string | null; error?: string }
const noiseOf = (text: string) => {
  const lines = text.split(/\n+/).map(l => l.trim()).filter(Boolean);
  return lines.length ? lines.filter(l => l.length < 120 && NOISE.test(l)).length / lines.length : 0;
};
async function timed(fn: () => Promise<{ text: string; date?: string | null }>): Promise<Run> {
  const t = performance.now();
  try {
    const r = await fn();
    return { ok: r.text.length >= MIN_CHARS, chars: r.text.length, ms: Math.round(performance.now() - t), noise: noiseOf(r.text), date: r.date ?? null };
  } catch (error) {
    return { ok: false, chars: 0, ms: Math.round(performance.now() - t), noise: 0, date: null, error: (error as Error).message.slice(0, 80) };
  }
}
const get = (url: string, headers: Record<string, string> = {}) =>
  fetch(url, { headers: { "user-agent": UA, "accept-language": "zh-TW,en;q=0.8", ...headers }, redirect: "follow", signal: AbortSignal.timeout(25_000) });

const readability = (s: Sample) => timed(async () => {
  const res = await get(s.url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const got = readable(await res.text(), res.url);
  if (!got) throw new Error("no body found");
  return { text: got.text, date: got.publishedAt?.toISOString() ?? null };
});
const jina = (s: Sample) => timed(async () => {
  const res = await get(`https://r.jina.ai/${s.url}`, { accept: "text/plain" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const text = await res.text();
  return { text: text.replace(/^(Title|URL Source|Published Time|Markdown Content):.*$/gm, ""), date: /^Published Time:\s*(.+)$/m.exec(text)?.[1] ?? null };
});
const api = (s: Sample) => timed(async () => {
  if (s.group === "GitHub") {
    const [, owner, repo] = new URL(s.url).pathname.split("/");
    const res = await get(`https://api.github.com/repos/${owner}/${repo}/readme`, { accept: "application/vnd.github.raw+json" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { text: await res.text() };
  }
  const id = new URL(s.url).pathname.replace("/abs/", "");
  const xml = await (await get(`https://export.arxiv.org/api/query?id_list=${id}`)).text();
  const summary = /<summary>([\s\S]*?)<\/summary>/.exec(xml)?.[1]?.trim();
  if (!summary) throw new Error("no abstract");
  return { text: `${/<title>([\s\S]*?)<\/title>/.exec(xml.slice(xml.indexOf("<entry>")))?.[1] ?? ""}\n\n${summary}`, date: /<published>(.+?)<\/published>/.exec(xml)?.[1] ?? null };
});

const results: Array<Sample & { A: Run; B: Run; C?: Run }> = [];
for (const [i, s] of samples.entries()) {
  const A = await readability(s);
  const B = await jina(s);
  const C = s.group === "GitHub" || s.group === "arXiv" ? await api(s) : undefined;
  results.push({ ...s, A, B, ...(C ? { C } : {}) });
  console.log(`${i + 1}/${samples.length} ${s.group} A=${A.ok ? A.chars : A.error} B=${B.ok ? B.chars : B.error}${C ? ` C=${C.ok ? C.chars : C.error}` : ""}`);
  await new Promise(r => setTimeout(r, 3500)); // Jina's free tier allows about 20 requests a minute
}
writeFileSync(path.join(here, "results.json"), JSON.stringify(results, null, 2));
