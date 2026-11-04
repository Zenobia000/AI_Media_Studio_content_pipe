// Builds a deterministic dataset for the backend-port parity tests with the engine's own functions (as the TypeScript tests do):
// published articles across sources, categories and times, selected ones, events with several reports, an X post, a hot ranking and
// daily, weekly and monthly reports. Run against a migrated, otherwise empty database.
import { randomUUID } from "node:crypto";
import { closeDb, sql } from "@aihot/backend/db";
import { upsertMaterial } from "@aihot/backend/content/materials";
import { publishArticle } from "@aihot/backend/publication/publish";
import { computeHotRanking, snapshotHeat } from "@aihot/backend/events/hot";
import { beijingDate, isoWeekLabel } from "@aihot/contracts/time";

const NOW = Date.now();
const H = 3600_000;
const CATEGORIES = ["ai-models", "ai-products", "industry", "paper", "tip", "opinion"];
const SOURCES = [
  { id: "par-openai", name: "OpenAI News", tier: "T1", first: true },
  { id: "par-anthropic", name: "Anthropic News", tier: "T1", first: true },
  { id: "par-techcrunch", name: "TechCrunch AI", tier: "T2", first: false },
  { id: "par-verge", name: "The Verge AI", tier: "T2", first: false },
  { id: "par-hn", name: "Hacker News", tier: "T2", first: false },
  { id: "par-ithome", name: "iThome", tier: "T2", first: false },
];
for (const s of SOURCES) {
  await sql`INSERT INTO sources (id, name, kind, tier, first_party, participation_mode, next_fetch_at) VALUES (${s.id}, ${s.name}, 'rss', ${s.tier}, ${s.first}, 'editorial', '2100-01-01') ON CONFLICT DO NOTHING`;
}

let n = 0;
async function article(opts: { source: string; hoursAgo: number; category: string; score: number | null; selected: boolean; story?: number; role?: string; title?: string; xPost?: object; tags?: string[] }) {
  n += 1;
  const at = new Date(NOW - opts.hoursAgo * H);
  const title = opts.title ?? `Parity article ${n}: ${opts.category} news`;
  const { articleId } = await upsertMaterial({
    sourceId: opts.source, url: `https://example.org/parity/${n}`, title, bodyText: `Body of article ${n}. `.repeat(20), bodyHtml: `<p>Body of article ${n}.</p>`,
    bodyStatus: "ok", via: "fetch", publishedAt: at, discoveredAt: at, language: "en", ...(opts.xPost ? { xPost: opts.xPost as never } : {}),
  });
  await sql`INSERT INTO analyses (article_id, input_revision, origin, relevance, category, tags, title_zh, summary_zh, reason_zh, score, selected, output)
            VALUES (${articleId}, 1, 'rule', 'pass', ${opts.category}, ${opts.tags ?? ["AI", `tag${n % 4}`, ...(n % 5 === 0 ? ["entity:openai"] : []), ...(n % 7 === 0 ? ["Agent"] : [])]}, ${`中文標題 ${n}`}, ${`這是第 ${n} 篇文章的公開摘要。`}, ${`入選理由 ${n}`},
                    ${opts.score}, ${opts.selected}, ${sql.json({ scope: "single" } as never)})`;
  if (opts.story !== undefined) {
    const [fact] = await sql<{ id: number }[]>`INSERT INTO facts (public_id, story_id, title) VALUES (${randomUUID()}, ${opts.story}, ${title}) RETURNING id`;
    await sql`INSERT INTO fact_articles (fact_id, article_id, role) VALUES (${fact!.id}, ${articleId}, ${opts.role ?? "report"})`;
    await sql`INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) VALUES (${opts.story}, ${articleId}, ${`source:${opts.source}`}, ${opts.source}, 'editorial', ${at})`;
  }
  await publishArticle(articleId, { releasedAt: at });
  return articleId;
}

const story = async (title: string) => (await sql<{ id: number }[]>`INSERT INTO stories (public_id, title) VALUES (${randomUUID()}, ${title}) RETURNING id`)[0]!.id;

// Five events, each reported by several sources; the first is selected.
const events: number[] = [];
for (let e = 0; e < 5; e++) {
  const s = await story(`Event ${e + 1}`);
  events.push(s);
  const category = CATEGORIES[e % CATEGORIES.length]!;
  await article({ source: SOURCES[0]!.id, hoursAgo: 2 + e, category, score: 90 - e, selected: e < 3, story: s, role: "primary", title: `Event ${e + 1} announced` });
  await article({ source: SOURCES[2]!.id, hoursAgo: 3 + e, category, score: 70 - e, selected: false, story: s });
  await article({ source: SOURCES[3]!.id, hoursAgo: 4 + e, category, score: 60 - e, selected: false, story: s });
}
// Standalone articles across categories and ages (some outside the 24-hour window, one unscored, one below the browse floor).
for (let i = 0; i < 24; i++) {
  await article({ source: SOURCES[i % SOURCES.length]!.id, hoursAgo: 1 + i * 5, category: CATEGORIES[i % CATEGORIES.length]!, score: i === 7 ? null : i === 9 ? 5 : 40 + (i * 7) % 55, selected: i % 5 === 0 });
}
// An X post with media and a quoted post.
await article({
  source: SOURCES[4]!.id, hoursAgo: 1.5, category: "ai-products", score: 75, selected: true, title: "A post on X about a new model",
  xPost: {
    tweetId: "1900000000000000001", authorName: "Someone", handle: "someone", avatarUrl: "https://pbs.example/a_normal.jpg", text: "A new model is out, and it is good.", lang: "en", replyTo: null,
    media: [{ kind: "image", url: "https://pbs.example/m.jpg", width: 800, height: 600, poster: null }],
    quoted: { authorName: "Other", handle: "other", text: "Quoted text.", url: "https://x.com/other/status/1", media: [] },
  },
});

await snapshotHeat(new Date());
await computeHotRanking(new Date());

// Reports, written the way the engine's tests write them (content as the composer saves it).
const today = beijingDate(NOW);
const ids = (await sql<{ id: string; title: string }[]>`SELECT p.article_id AS id, p.title FROM publications p WHERE p.selected ORDER BY p.sort_at DESC LIMIT 4`);
const ref = (r: { id: string; title: string }) => ({ itemId: r.id, title: r.title, sourceName: "OpenAI News", sourceId: "par-openai", firstParty: true, sourceUrl: "https://example.org" });
for (const [kind, key] of [["weekly", isoWeekLabel(beijingDate(NOW - 7 * 24 * H))], ["monthly", beijingDate(NOW - 40 * 24 * H).slice(0, 7)]] as const) {
  const content = { kind, title: `Parity ${kind} ${key}`, overview: "overview", themes: [{ heading: "Theme", summary: "Summary", storyRefs: ids.map(ref) }] };
  await sql`INSERT INTO reports (kind, key, window_start, window_end, content, generated_at, origin) VALUES (${kind}, ${key}, now() - interval '8 days', now() - interval '1 day', ${sql.json(content as never)}, now(), 'imported')`;
}
const daily = { kind: "daily", title: `Parity daily ${today}`, overview: "daily overview", themes: [{ heading: "Today", summary: "Today summary", storyRefs: ids.slice(0, 2).map(ref) }] };
await sql`INSERT INTO reports (kind, key, window_start, window_end, content, generated_at, origin) VALUES ('daily', ${beijingDate(NOW - 24 * H)}, now() - interval '2 days', now() - interval '1 day', ${sql.json(daily as never)}, now(), 'imported')`;

const counts = await sql`SELECT (SELECT count(*) FROM publications) AS publications, (SELECT count(*) FROM publications WHERE selected) AS selected, (SELECT count(*) FROM stories) AS stories, (SELECT count(*) FROM reports) AS reports, (SELECT count(*) FROM hot_rankings) AS hot`;
console.log(JSON.stringify(counts[0]));
await closeDb();
