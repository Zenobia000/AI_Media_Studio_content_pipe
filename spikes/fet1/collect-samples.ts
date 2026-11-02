// SPK-FET-1: builds samples.json from live sources, once. The file is committed so the comparison can be rerun on the same pages.
// node spikes/fet1/collect-samples.ts
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const here = import.meta.dirname;
const sources = (JSON.parse(readFileSync(path.join(here, "../../AIHOT/industry/sources.json"), "utf8")) as { sources: Array<{ id: string; name: string; kind: string; tier: string; tags: string[]; config: { feedUrl?: string } }> }).sources;

type Sample = { group: string; from: string; url: string; feedDate: string | null };
const out: Sample[] = [];
const seen = new Set<string>();
const add = (s: Sample) => {
  if (seen.has(s.url)) return;
  seen.add(s.url);
  out.push(s);
};

async function get(url: string): Promise<string> {
  const res = await fetch(url, { headers: { "user-agent": "MediaHOT-spike/1.0" }, signal: AbortSignal.timeout(20_000) });
  return res.text();
}

function items(xml: string): Array<{ url: string; date: string | null }> {
  const blocks = xml.match(/<(item|entry)[\s>][\s\S]*?<\/\1>/g) ?? [];
  return blocks.flatMap(b => {
    const atom = /<link[^>]+rel="alternate"[^>]*href="([^"]+)"/.exec(b) ?? /<link[^>]+href="([^"]+)"[^>]*rel="alternate"/.exec(b);
    const rss = /<link>\s*(?:<!\[CDATA\[)?\s*(https?:[^<\]\s]+)/.exec(b);
    const url = (atom?.[1] ?? rss?.[1])?.replace(/&amp;/g, "&");
    const date = /<(?:pubDate|published|updated|dc:date)>\s*([^<]+?)\s*</.exec(b)?.[1] ?? null;
    return url ? [{ url, date }] : [];
  });
}

const groups: Array<[string, (s: (typeof sources)[number]) => boolean, number]> = [
  ["官方 blog／研究", s => s.kind === "rss" && s.tier === "T1" && !s.id.startsWith("gh-"), 2],
  ["科技媒體（英文）", s => s.kind === "rss" && s.tier === "T2" && s.tags.some(t => ["媒体", "个人"].includes(t)), 2],
  ["中文媒體", s => s.tags.includes("繁中"), 5],
  ["電子報", s => s.tags.includes("電子報"), 2],
];
for (const [group, pick, per] of groups) {
  for (const s of sources.filter(pick)) {
    try {
      for (const it of items(await get(s.config.feedUrl!)).slice(0, per)) add({ group, from: s.name, url: it.url, feedDate: it.date });
    } catch (error) {
      console.error(`skip ${s.name}: ${(error as Error).message}`);
    }
  }
}

// GitHub repositories and arXiv papers: the repositories the release feeds watch, and the papers on Hugging Face's list today.
for (const s of sources.filter(s => s.id.startsWith("gh-")).slice(0, 5)) add({ group: "GitHub", from: s.name, url: s.config.feedUrl!.replace(/\/releases\.atom$/, ""), feedDate: null });
for (const p of (JSON.parse(await get("https://huggingface.co/api/daily_papers")) as Array<{ paper: { id: string } }>).slice(0, 4)) add({ group: "arXiv", from: "Hugging Face Daily Papers", url: `https://arxiv.org/abs/${p.paper.id}`, feedDate: null });

writeFileSync(path.join(here, "samples.json"), JSON.stringify(out, null, 2));
console.log(out.length, "samples");
for (const g of [...new Set(out.map(o => o.group))]) console.log(g, out.filter(o => o.group === g).length);
