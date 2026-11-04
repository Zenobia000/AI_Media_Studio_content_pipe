// Runs the collectors' pure helpers on fixed inputs and prints JSON, for the Python port's tests (tests/engine/source_fixtures.json).
//   node scripts/gen-source-fixtures.ts > ../tests/engine/source_fixtures.json
import { getPath, renderTemplate, toDate } from "@aihot/backend/sources/json-list";
import { admitListing, filterPublicationWindow, noiseFiltered } from "@aihot/backend/sources/filters";
import { unsupportedConfig } from "@aihot/backend/sources/config-keys";
import { parseLooseDate, readPublicationDate, readRelativeDate } from "@aihot/backend/sources/dates";

const dates = [
  "2026-09-26", "2026-09-26 10:00", "2026-09-26T10:00:00", "2026-09-26T10:00:00Z", "2026-09-26T10:00:00+08:00", "2026-09-26T10:00:00.123Z", "2026-09-26 10:00:00 +0000",
  "2026/09/26", "2026.9.26", "2026年9月26日", "2026年9月26日 10:30", "2026年9月26日10:30", "20260926103000", "2026-02-30", "2026-13-01", "2026-09-26 25:00",
  "9/26/2026", "09/26/2026 3:45 PM", "26/09/2026", "Sep 26, 2026", "September 26, 2026", "Sep 26 2026", "Sept 26, 2026", "26 Sep 2026", "26 September 2026", "26 Sep 26",
  "Sep 26, 2026 at 10:30", "Sep 26, 2026 10:30 PM", "Sep 26, 2026 10:30 PM GMT", "Sep 26, 2026 10:30 EST", "Sep 26, 2026 10:30 CST", "Sat, 26 Sep 2026 10:30:00 GMT",
  "Sat, 26 Sep 2026 10:30:00 +0200", "26 Sep 2026 10:00 UTC", "Feb 30, 2026", "Foo 26, 2026", "Published Sep 26th, 2026", "1st Oct 2026", "Oct 1st, 2026 08:00 AM", "Oct 1, 2026 13:00 PM",
  "10:00Z", "", "   ", "not a date", "2026", "2026-09", "Updated: 2026-09-26 08:15 by someone", "2026-09-26 08:15:30.500", "12/31/2026 11:59 PM PST", "Mon, 05 Oct 2026 08:00:00 -0400",
  "2026-09-26T10:00:00-05:00", "2026-09-26 10:00 PM", "2026-09-26 00:00 AM",
];
const offsets = ["+08:00", "+00:00", "-05:00"];
const relative = ["20小時前", "2天前", "昨天", "前天", "今天 15:46", "昨天 08:05", "3 hours ago", "16h ago", "5 minutes ago", "just now", "剛剛", "版面之外·今天 15:46", "47小時前", "1 day ago", "yesterday", "3天", "兩天前", "", "999分鐘前"];
const NOW = new Date("2026-11-04T09:30:15.250Z");
const out: Record<string, unknown> = {
  parseLooseDate: offsets.flatMap((o) => dates.map((d) => ({ in: d, offset: o, out: parseLooseDate(d, o)?.toISOString() ?? null }))),
  readPublicationDate: dates.map((d) => ({ in: d, out: ((r) => ({ at: r.publishedAt?.toISOString() ?? null, time: r.publicationTime }))(readPublicationDate(d)) })),
  readRelativeDate: offsets.flatMap((o) => relative.map((d) => ({ in: d, offset: o, out: ((r) => ({ at: r.publishedAt?.toISOString() ?? null, time: r.publicationTime }))(readRelativeDate(d, NOW, o)) }))),
  now: NOW.toISOString(),
};

const doc = { a: { b: [{ c: "x" }, { c: 5 }], n: 1.5, big: 12345678901234567890, z: 0, e: "", t: true, nul: null }, "k.1": 2, list: [1, [2, 3]] };
const paths = ["", "a", "a.b", "a.b.0", "a.b.1.c", "a.b.2", "a.b.length", "a.n", "a.big", "a.z", "a.e", "a.t", "a.nul", "a.nul.x", "list.1.0", "list.x", "a.b.0.c.d", "nope", "k.1"];
const templates = ["https://x/{a.b.0.c}", "https://x/{raw:a.b.0.c}/{a.n}", "{a.big}", "{a.z}", "{a.e}", "{nope}", "{a.t}/{raw:a.t}", "{a.b}", "{list}", "{a.nul}", "a b/{a.b.0.c}?q={raw:a.n}"];
const toDates: [unknown, string | undefined, string | undefined][] = [
  [1760000000000, "epoch_ms", undefined], [1760000000, "epoch_s", undefined], ["1760000000", "epoch_s", undefined], ["abc", "epoch_s", undefined], [1e18, "epoch_ms", undefined], [-1000, "epoch_ms", undefined],
  [1760000000.5, "epoch_s", undefined], ["20260926", "yyyymmdd", "+08:00"], ["2026092", "yyyymmdd", undefined], [20260926, "yyyymmdd", "+00:00"], ["2026-09-26", undefined, undefined],
  ["Sep 26, 2026", undefined, "-05:00"], [null, "epoch_ms", undefined], ["", undefined, undefined], [true, undefined, undefined], [{}, undefined, undefined], ["2026-09-26T10:00:00Z", undefined, "+00:00"],
];
const src = (config: Record<string, unknown>) => ({ id: "s", name: "S", kind: "rss", config, tier: "T2", participation_mode: "editorial", uses: [], first_party: false, interval_minutes: 30, enabled: true, cursor: null, fail_count: 0 }) as never;
const cands = [
  { url: "http://a.example/x/1", title: "Hello Agent", excerpt: "about stuff", categories: ["ai"] },
  { url: "https://a.example/y/2", title: "Sponsored: buy", excerpt: null, categories: [] },
  { url: "https://a.example/old/3", title: "Plain", excerpt: "Promo inside", categories: ["promo", "x"] },
  { url: "https://b.example/4", title: "Other", excerpt: "agent promo", categories: undefined },
  { url: "https://a.example/x/5", title: "Newest", excerpt: "fine", categories: ["ai", "news"] },
];
const filterConfigs: Record<string, unknown>[] = [
  {}, { allowUrlPrefixes: ["https://a.example/x/"] }, { allowUrlPrefixes: ["http://a.example/"], denyUrlPrefixes: ["https://a.example/old/"] }, { denyCategories: ["promo"] }, { allowCategories: ["ai"] },
  { ingestNoiseFilter: { dropMarkers: ["promo"], dropMarkersTitleOnly: ["sponsored"], keepIfMatches: ["agent"] } }, { itemUrlPrefixRewrite: { from: "http://a.example/", to: "https://mirror.example/" } },
  { itemUrlPrefixRewrite: { from: "", to: "x" }, ingestNoiseFilter: {} },
];
const windowItems = [
  { id: 1, publishedAt: new Date("2026-11-01T00:00:00Z") }, { id: 2, publishedAt: new Date("2026-11-04T09:00:00Z") }, { id: 3, publishedAt: null }, { id: 4, publishedAt: new Date("2026-11-04T10:00:00Z") },
  { id: 5, publishedAt: new Date("2026-11-04T10:31:00Z") }, { id: 6, publishedAt: new Date("2026-11-02T00:00:00Z") },
];
const WNOW = Date.parse("2026-11-04T09:30:00Z");
const configChecks: [string, Record<string, unknown>][] = [
  ["rss", { feedUrl: "x", summaryIsBody: true }], ["rss", { feedUrl: "x", bogus: 1, adapter: "x" }], ["web_list", { url: "x", adapter: "mimo_home", parseMode: "html" }], ["web_list", { adapter: "site_cards", parseMode: "nope" }],
  ["json_list", { url: "x", requireBoolean: { path: "a", equals: true, extra: 1 } }], ["json_list", { detail: { maxFetches: 1, titleFoo: 2 } }], ["rss", { detail: { preserve: [{ selector: "pre", as: "pre", code: "code" }] } }],
  ["rss", { detail: { preserve: [{ selector: "pre", as: "table", code: "c" }] } }], ["rss", { detail: { preserve: [{ selector: " ", as: "pre" }] } }], ["rss", { detail: { preserve: "x" } }], ["rss", { detail: [1, 2] }],
  ["rss", { publishedAfter: "2026-09-26T00:00:00Z" }], ["rss", { publishedAfter: "2026-09-26T00:00:00.123Z" }], ["rss", { publishedAfter: "2026-02-30T00:00:00Z" }], ["rss", { publishedAfter: "2026-09-26" }], ["rss", { publishedAfter: 5 }],
  ["rss", { acceptLanguage: "en-US,en;q=0.9" }], ["rss", { acceptLanguage: "en-US, zh;q=0.9, *;q=0.1" }], ["rss", { acceptLanguage: "en;q=2" }], ["rss", { acceptLanguage: "" }], ["rss", { acceptLanguage: 5 }],
  ["rss", { publisherRole: "organization", publisherUrlPrefixes: ["https://a.example/", "http://b.example/x"], publisherRequiresDiscovery: true }], ["rss", { publisherUrlPrefixes: ["https://a.example/?q=1"] }], ["rss", { publisherUrlPrefixes: ["https://u:p@a.example/"] }],
  ["rss", { publisherUrlPrefixes: ["ftp://a.example/"] }], ["rss", { publisherUrlPrefixes: ["not a url"] }], ["rss", { publisherUrlPrefixes: "x" }], ["rss", { publisherRequiresDiscovery: true }], ["rss", { publisherRequiresDiscovery: "yes" }],
  ["rss", { publisherRole: "robot" }], ["rss", { publisherRole: 5 }], ["x_search", { query: "q", searchType: "Latest", feedUrl: "x" }], ["mp_account", { wxid: "w", ghid: "g", url: "x" }], ["external", { anything: 1 }], ["nope" as never, { a: 1 }],
  ["rss", { _aihot: { initialBackfillLimit: 5, other: 1 } }], ["rss", { ingestNoiseFilter: { dropMarkers: [] , weird: 1 } }], ["rss", { ingestNoiseFilter: null }],
];
Object.assign(out, {
  getPath: paths.map((p) => ({ in: p, out: getPath(doc, p) ?? null })),
  renderTemplate: templates.map((t) => ({ in: t, out: renderTemplate(t, doc) })),
  toDate: toDates.map(([v, unit, off]) => ({ in: [v ?? null, unit ?? null, off ?? null], out: ((r) => ({ at: r.publishedAt?.toISOString() ?? null, time: r.publicationTime }))(toDate(v, unit, off)) })),
  admitListing: filterConfigs.map((c) => ({ config: c, out: admitListing(cands as never[], src(c)).map((x) => x.url) })),
  noiseFiltered: filterConfigs.flatMap((c) => cands.map((x, i) => ({ config: c, index: i, out: noiseFiltered(x as never, src(c)) }))),
  window: [undefined, "2026-11-01T12:00:00.000Z", "2026-11-04T09:00:00.000Z"].map((after) => ({ after: after ?? null, out: filterPublicationWindow(windowItems as never[], after, WNOW).map((x: { id?: number }) => x.id) })),
  windowNow: new Date(WNOW).toISOString(),
  unsupportedConfig: configChecks.map(([kind, config]) => ({ kind, config, out: unsupportedConfig(kind as never, config) })),
  cands: cands.map((c) => ({ ...c, categories: c.categories ?? null })),
});
process.stdout.write(JSON.stringify(out));
