// Newsletters (COL-FR-019): an issue holds many stories, so each link in it becomes an article of its own. A link that
// is already an article (an official blog the feed brought) stays one article; what is added is that a newsletter
// mentioned it, kept as a heat signal ("被 2 份電子報提及").
import { upsertMaterial } from "@aihot/backend/content/materials";
import { sql } from "@aihot/backend/db";
import { queueProcessing } from "@aihot/backend/jobs/content";
import { identityKeyForUrl } from "@aihot/backend/lib/url";
import { hourOf, store } from "./signals.ts";
import { extractSource, ExtractFailure, type Get } from "./source-extract.ts";

export const LINKS_SOURCE = "newsletter-links";
const MAX_LINKS = 30;
const ISSUE_AGE_DAYS = 3;
const MAX_ISSUES = 10;
/** Where a newsletter links that is no story: social profiles, sign-up pages, its own pages. */
const SKIP_HOSTS = /(^|\.)(twitter\.com|x\.com|linkedin\.com|facebook\.com|instagram\.com|t\.me|discord\.gg)$/i;
const SKIP_TEXT = /sponsor|advertis|unsubscribe|subscribe|sign up|preferences|view in browser|careers|^read more$|^learn more$/i;
const SKIP_PATH = /unsubscribe|preferences|subscribe|advertise|sponsor|careers|jobs/i;
/** Pictures and clips are not stories; neither is a link whose text names no story ("image", "Hugging Face", a bare address). */
const SKIP_MEDIA_HOST = /(^|\.)(i\.redd\.it|v\.redd\.it|preview\.redd\.it|imgur\.com)$/i;
const SKIP_FILE = /\.(png|jpe?g|gif|webp|svg|mp4|webm|mov|pdf)$/i;
const GENERIC_TEXT = /^(image|images|video|link|links|here|source|paper|github|website|site|demo|code|blog|post|thread|tweet|hugging face|reddit|twitter|x)$/i;
const BARE_ADDRESS = /^[a-z0-9.-]+\.[a-z]{2,}(\/\S*)?$/i;
const MIN_TITLE = 8;

export interface MentionedLink {
  url: string;
  title: string;
}

/** The story links of an issue's Markdown: the sponsors, the social links and the newsletter's own pages left out. */
export function storyLinks(markdown: string, issueUrl: string): MentionedLink[] {
  const issueHost = new URL(issueUrl).hostname.replace(/^www\./, "");
  const found = new Map<string, MentionedLink>();
  for (const m of markdown.matchAll(/(?<!!)\[([^\]]{3,300})\]\((https?:[^)\s]+)\)/g)) {
    const title = m[1]!.replace(/\s+/g, " ").replace(/^[*_`\s]+|[*_`\s]+$/g, "");
    if (title.length < MIN_TITLE || GENERIC_TEXT.test(title) || BARE_ADDRESS.test(title)) continue;
    let url: URL;
    try {
      url = new URL(m[2]!);
    } catch {
      continue;
    }
    const host = url.hostname.replace(/^www\./, "");
    if (SKIP_HOSTS.test(host) || SKIP_MEDIA_HOST.test(host) || SKIP_FILE.test(url.pathname) || host === issueHost || host.endsWith(`.${issueHost}`) || SKIP_TEXT.test(title) || SKIP_PATH.test(url.pathname)) continue;
    if (url.pathname.length < 2 && !url.search) continue; // a site's front page is no story
    const key = identityKeyForUrl(url.toString());
    if (key && !found.has(key)) found.set(key, { url: url.toString(), title: title.slice(0, 300) });
    if (found.size >= MAX_LINKS) break;
  }
  return [...found.values()];
}

export interface SplitResult {
  issues: number;
  links: number;
  /** Articles that were not there before. */
  created: number;
  failed: Array<{ url: string; reason: string }>;
}

async function ensureLinksSource(): Promise<void> {
  await sql`
    INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health, tags)
    VALUES (${LINKS_SOURCE}, '電子報提及', 'external', '{}'::jsonb, 'T2', 'editorial', 1440, true, 'ok', ${["電子報"]})
    ON CONFLICT (id) DO NOTHING`;
}

/** Splits the issues of newsletter sources that have not been split yet. */
export async function splitNewsletters(opts: { get?: Get; now?: Date; queue?: boolean } = {}): Promise<SplitResult> {
  const now = opts.now ?? new Date();
  const result: SplitResult = { issues: 0, links: 0, created: 0, failed: [] };
  const issues = await sql<{ id: string; url: string }[]>`
    SELECT a.id, a.url FROM articles a JOIN sources s ON s.id = a.source_id
    WHERE '電子報' = ANY(s.tags) AND s.id <> ${LINKS_SOURCE} AND a.discovered_at > ${new Date(now.getTime() - ISSUE_AGE_DAYS * 86_400_000)}
      AND NOT EXISTS (SELECT 1 FROM newsletter_issues i WHERE i.article_id = a.id)
    ORDER BY a.discovered_at DESC LIMIT ${MAX_ISSUES}`;
  if (!issues.length) return result;
  await ensureLinksSource();
  for (const issue of issues) {
    let links: MentionedLink[];
    try {
      const page = await extractSource(issue.url, { get: opts.get, subject: `newsletter:${issue.id}` });
      links = storyLinks(page.markdown, issue.url);
    } catch (error) {
      if (!(error instanceof ExtractFailure)) throw error;
      result.failed.push({ url: issue.url, reason: error.message }); // tried again on the next run, until the issue is old
      continue;
    }
    for (const link of links) {
      const material = await upsertMaterial({ sourceId: LINKS_SOURCE, url: link.url, title: link.title, via: "newsletter" });
      if (material.created) result.created += 1;
      if (opts.queue !== false && (material.created || material.revised || material.processingNeeded)) await queueProcessing(material.articleId);
      await sql`INSERT INTO newsletter_mentions (issue_article_id, identity_key) VALUES (${issue.id}, ${identityKeyForUrl(link.url)!}) ON CONFLICT DO NOTHING`;
    }
    await sql`INSERT INTO newsletter_issues (article_id, links) VALUES (${issue.id}, ${links.length}) ON CONFLICT (article_id) DO NOTHING`;
    result.issues += 1;
    result.links += links.length;
  }
  await recordMentions(now);
  return result;
}

/** How many issues mention each address, as a heat signal for this hour. */
export async function recordMentions(now: Date): Promise<number> {
  const rows = await sql<{ identity_key: string; n: number }[]>`SELECT identity_key, count(*)::int AS n FROM newsletter_mentions GROUP BY identity_key`;
  await store(rows.map(r => ({ identityKey: r.identity_key, metric: "newsletter_mentions", value: r.n })), hourOf(now));
  return rows.length;
}

