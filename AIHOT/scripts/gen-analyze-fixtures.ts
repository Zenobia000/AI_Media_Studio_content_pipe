// Runs the analysis' pure helpers on fixed articles and prints JSON, for the Python port's tests (tests/engine/analyze_fixtures.json).
//   DATABASE_URL=postgres://x node scripts/gen-analyze-fixtures.ts > ../tests/engine/analyze_fixtures.json
import { buildMaterial } from "@aihot/backend/editorial/input";
import {
  ANALYZE_PROMPT_VERSION, buildScoreInput, normalizeStructure, PROMPT_VERSIONS, SCORE_SYSTEM, SELECTION_PROMPT_VERSION, selectionThreshold, scoreInputTime, STRUCTURE_SYSTEM,
  MEND_SYSTEM, normalizeAnalysis, scoresSelect, shownScore, awaitsMaterial,
} from "@aihot/backend/editorial/analyze";
import {
  buildArticlePrompt, buildLongTweetPrompt, buildShortTweetPrompt, cleanArticleTextForLLM, compactAnswerFirstSummary, finalizeCopy, isShortTweet, matchEntityIds, missingEvidence,
  needsShortTweetTranslation, parseTranslateOutput, PREFILTER_SYSTEM, prefilterUser, renderContext, translateInputOf, UNDERSTAND_SYSTEM, understandUser,
} from "@aihot/backend/editorial/writing";
import { normalizeTags } from "@aihot/backend/editorial/vocabulary";

const LONG = "OpenAI announced GPT-5.5 today, a model that improves reasoning. ".repeat(12);
const base = { id: "a1", revision: 2, url: "https://openai.com/blog/gpt-5-5", author: "Staff", discoveredAt: null, media: [], translationZh: null };
const source = (over: Record<string, unknown> = {}) => ({ name: "OpenAI News", kind: "rss", tier: "T1", selectionClass: null, firstParty: true, tags: ["official"], ownerEntityId: "openai", fetchesBody: true, ...over });
const articles: Record<string, any>[] = [
  { ...base, title: "Introducing GPT-5.5", publishedAt: "2026-10-30T08:15:00.000Z", publicationTime: "2026-10-30T08:15:00.000Z", bodyText: LONG, excerpt: "short", bodyStatus: "ok", xPost: null, source: source() },
  { ...base, id: "a2", title: "A day-only article", publishedAt: "2026-10-29T00:00:00.000Z", publicationTime: "2026-10-29", bodyText: null, excerpt: "Only a feed summary of the story, with some words.", bodyStatus: "pending", xPost: null, source: source({ kind: "web_list", tier: "T2", firstParty: false, fetchesBody: false, ownerEntityId: null, tags: [] }) },
  { ...base, id: "a3", title: "Only a title", publishedAt: null, publicationTime: null, bodyText: null, excerpt: null, bodyStatus: "unconfirmed", xPost: null, source: source({ tier: "T2" }) },
  { ...base, id: "a4", title: "中文文章標題：模型發布", url: "https://example.cn/a", publishedAt: "2026-10-28T03:00:00.000Z", bodyText: "這是一篇中文文章，內容很長，".repeat(60) + "百度發布了新模型。", excerpt: null, bodyStatus: "ok", xPost: null, source: source({ name: "某站", tier: "T2", firstParty: false }), translationZh: "譯文" },
  { ...base, id: "a5", title: "Short tweet", url: "https://x.com/sama/status/1", author: null, publishedAt: "2026-10-30T09:00:00.000Z", publicationTime: "2026-10-30T09:00:00.000Z", bodyText: null, excerpt: null, bodyStatus: "ok",
    xPost: { authorName: "Sam", handle: "sama", text: "we shipped a new model today, it is much faster #ai @openai https://t.co/x", quoted: { handle: "gdb", text: "great work team", media: [] }, media: [{ kind: "image", url: "https://pbs.twimg.com/a.jpg" }] },
    source: source({ name: "Sam Altman", kind: "x_search", tier: "T1_5", ownerEntityId: "openai" }) },
  { ...base, id: "a6", title: "Long tweet", url: "https://x.com/karpathy/status/2", author: null, publishedAt: "2026-10-30T10:00:00.000Z", publicationTime: "2026-10-30T10:00:00.000Z", bodyText: null, excerpt: null, bodyStatus: "ok",
    xPost: { authorName: "Andrej", handle: "karpathy", text: "A long thread about training. ".repeat(40), quoted: null, media: [{ kind: "video", url: "https://v" }] }, source: source({ name: "Andrej", kind: "x_search", tier: "T2", firstParty: false, ownerEntityId: null }) },
  { ...base, id: "a7", title: "Link only", url: "https://x.com/a/status/3", author: null, publishedAt: "2026-10-30T11:00:00.000Z", publicationTime: "2026-10-30T11:00:00.000Z", bodyText: null, excerpt: null, bodyStatus: "pending",
    xPost: { authorName: "A", handle: "a", text: "https://x.com/i/article/123456", quoted: null, media: [] }, source: source({ kind: "x_search", tier: "T2", firstParty: false }) },
  { ...base, id: "a8", title: "Anthropic Claude and Google Gemini compared", publishedAt: "2026-10-27T12:30:45.123Z", publicationTime: "2026-10-27T12:30:45.123Z", bodyText: "Claude from Anthropic and Gemini from Google. ".repeat(30), excerpt: null, bodyStatus: "ok", xPost: null, url: "https://www.techcrunch.com/x", source: source({ name: "TechCrunch", tier: "T2", firstParty: false, ownerEntityId: null }) },
];
const hydrate = (a: any) => ({ ...a, publishedAt: a.publishedAt ? new Date(a.publishedAt) : null });
const today = new Date().toISOString().slice(0, 10);
const guard = (fn: () => string) => { try { return fn(); } catch (e) { return `ERR ${(e as Error).message}`; } };
const per = articles.map((raw) => {
  const a = hydrate(raw);
  const t = translateInputOf(a);
  return {
    id: a.id, prefilterUser: prefilterUser(a), understandUser: understandUser(a), renderContext: renderContext(a), material: buildMaterial(a), score: buildScoreInput(a),
    article: guard(() => buildArticlePrompt(t)), short: guard(() => buildShortTweetPrompt(t)), long: guard(() => buildLongTweetPrompt(t)), missing: missingEvidence(a), awaits: awaitsMaterial(a),
    translateInput: { ...t, publishedAt: t.publishedAt?.toISOString() ?? null }, threshold: selectionThreshold(a.source),
  };
});
const copies: Array<[number, { titleZh: string; summaryZh: string }]> = [
  [0, { titleZh: "OpenAI 發布 GPT-5.5", summaryZh: "OpenAI 今天發布了 GPT-5.5，推理能力更強。" }],
  [0, { titleZh: "Anthropic 發布新模型", summaryZh: "Google 與 Anthropic 都提到了。" }],
  [7, { titleZh: "Claude 與 Gemini 對比", summaryZh: "第一句。第二句。".repeat(40) }],
  [7, { titleZh: "x", summaryZh: "很短。" }],
  [4, { titleZh: "新模型", summaryZh: "我們今天發布了新模型，速度快很多。" }],
  [3, { titleZh: "OpenAI 的模型", summaryZh: "摘要，提到 OpenAI。" + "很長的句子，".repeat(60) }],
];
const parse = ["title_zh: 標題\nsummary_zh: 摘要一\n摘要二\nbody_zh: 正文一\n\n正文二\n", "標題行\n摘要行一\n摘要行二", "title_zh: 只有標題", "title_zh: 標題\n只有未標記的摘要\n來源：echo", "", "單行", "summary_zh：全形冒號\nbody_zh：正文\n【時間錨點】x"];
const tagInputs = [["模型發布", "openai", "#AI"], "a, b，c", null, ["Claude", "agent", "agent"], []];
const structureCases = [
  { data: { scope: "single", category: null, tags: ["x"], subjects: ["OpenAI ", "nope"], fact: { title: "發布", subject: "OpenAI", evidence: "improves reasoning", conditions: [{ text: "t", quote: "improves reasoning" }, { quote: "not in text" }, null] } }, article: 0 },
  { data: { scope: "composite", category: null, tags: [], subjects: [], fact: { title: "x", evidence: "x", conditions: [] } }, article: 0 },
  { data: { scope: "single", category: null, tags: [], subjects: [], fact: { title: "x", evidence: "x", conditions: [] } }, article: 2 },
  { data: { scope: "single", category: null, tags: [], subjects: [], fact: null }, article: 3 },
];
const runs = [
  { prefilter: { label: "PASS" }, scores: { model: "m", threshold: 6, values: [7, 6], receiptIds: [], reused: true }, writing: { kind: "understand", titleZh: " 標題  x ", summaryZh: " 摘要 ", reasonZh: "理由" }, structure: { category: null, tags: ["模型發布"], subjects: ["openai"], scope: "single", fact: null } },
  { prefilter: { label: "BLOCK" }, scores: null, writing: null, structure: null },
  { prefilter: { label: "PASS" }, scores: { model: "m", threshold: 6, values: [3, 4], receiptIds: [], reused: true }, writing: { kind: "summarize", titleZh: "", summaryZh: "x", reasonZh: null }, structure: null },
  { prefilter: { label: "PASS" }, scores: { model: "m", threshold: 6, values: [9], receiptIds: [], reused: true, refused: false }, writing: { kind: "verbatim", titleZh: "t", summaryZh: "", reasonZh: null }, structure: null },
  { prefilter: { label: "UNKNOWN" }, scores: { model: "m", threshold: 6, values: [5, 5], receiptIds: [], reused: true, refused: true }, writing: { kind: "summarize", titleZh: "t", summaryZh: "s", reasonZh: null }, structure: null },
];
console.log(JSON.stringify({
  today, articles, per, versions: PROMPT_VERSIONS, analyzeVersion: ANALYZE_PROMPT_VERSION, selectionVersion: SELECTION_PROMPT_VERSION,
  systems: { prefilter: PREFILTER_SYSTEM, understand: UNDERSTAND_SYSTEM, score: SCORE_SYSTEM, structure: STRUCTURE_SYSTEM, mend: MEND_SYSTEM },
  copies: copies.map(([i, c]) => { const t = translateInputOf(hydrate(articles[i])); return { article: i, copy: c, out: finalizeCopy(t, c) }; }),
  parse: parse.map((p) => ({ in: p, out: parseTranslateOutput(p) })),
  tags: tagInputs.map((t) => ({ in: t, out: normalizeTags(t) })),
  structure: structureCases.map((c) => ({ ...c, out: normalizeStructure(c.data as never, hydrate(articles[c.article])) })),
  runs: runs.map((r) => ({ run: r, out: normalizeAnalysis(r as never), shown: shownScore(r.scores as never), select: scoresSelect(r.scores as never) })),
  scoreTimes: ["2026-10-30T08:15:00.000Z", "2026-10-30T08:15:00.500Z", "2026-12-31T23:59:59.999Z"].map((t) => ({ in: t, out: scoreInputTime(new Date(t)) })),
  clean: ["<p>Hello <b>world</b> https://example.com/2025/x &amp; more&nbsp;text</p><script>x()</script>", "plain\n\n\n\nlines  and\ttabs", ""].map((t) => ({ in: t, out: cleanArticleTextForLLM(t) })),
  compact: ["短。", "第一句很長，".repeat(40) + "。第二句。", "句子。".repeat(80), "沒有標點".repeat(60)].map((t) => ({ in: t, out: compactAnswerFirstSummary(t) })),
  short: ["hi", "x".repeat(600), "中".repeat(90), "中".repeat(120)].map((t) => ({ in: t, short: isShortTweet(t), needs: needsShortTweetTranslation(t) })),
  match: [["OpenAI released GPT-5.5", "nothing"], ["Anthropic Claude"], [null, ""]].map((t) => ({ in: t, out: matchEntityIds(t as never) })),
}));
process.exit(0);
