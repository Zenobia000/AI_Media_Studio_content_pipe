// add-source: a link a person found goes into the inbox as a candidate (COL-FR-001). The same address added
// again, in any spelling the identity rule treats as one, keeps one article and one candidate and gains the note.
import { upsertMaterial } from "@aihot/backend/content/materials";
import { sql } from "@aihot/backend/db";
import { queueProcessing } from "@aihot/backend/jobs/content";
import { normalizeUrl } from "@aihot/backend/lib/url";
import { parseHTML } from "linkedom";
import { expandShortUrl } from "./shorturl.ts";
import { defaultGet, type Get } from "./source-extract.ts";

export const MANUAL_SOURCE = "manual";
const TIME_ZONE = "Asia/Taipei";

export class InvalidUrlError extends Error {
  constructor(url: string) {
    super(`不是有效的網址（只接受 http／https）：${url}`);
  }
}

export interface AddSourceOptions {
  note?: string;
  program?: string;
  formats?: string[];
  /** Shown until the page itself has been read; the page's own title when absent, else the address. */
  title?: string;
  /** Queue the article for analysis (default). Off only where no queue runs. */
  queue?: boolean;
  /** How a short link is followed (default: the guarded fetch); for tests. */
  get?: Get;
}

export interface AddSourceResult {
  articleId: string;
  url: string;
  /** The article is new to the site; false when a source or an earlier add already brought it. */
  created: boolean;
  /** It was already a manual candidate, so only the note (and any program or formats) was added. */
  alreadyAdded: boolean;
  /** The day (Asia/Taipei) of the inbox it first appeared in. */
  inboxDate: string;
}

/** The page's own title (og:title, else <title>); nothing when it cannot be read. */
async function pageTitle(url: string, get: Get): Promise<string | undefined> {
  try {
    const res = await get(url, { "accept-language": "zh-TW,zh;q=0.9,en;q=0.8" });
    if (res.status !== 200 || !/html/.test(res.type)) return undefined;
    const { document } = parseHTML(res.text);
    const og = document.querySelector('meta[property="og:title"]')?.getAttribute("content");
    return (og || document.title || "").replace(/\s+/g, " ").trim() || undefined;
  } catch {
    return undefined;
  }
}

export async function addSource(input: string, opts: AddSourceOptions = {}): Promise<AddSourceResult> {
  const url = normalizeUrl(await expandShortUrl(input, opts.get));
  if (!url) throw new InvalidUrlError(input);
  const title = opts.title?.trim() || (await pageTitle(url, opts.get ?? defaultGet)) || url;
  await sql`
    INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health, tags)
    VALUES (${MANUAL_SOURCE}, '手動新增', 'external', '{}'::jsonb, 'T2', 'editorial', 1440, true, 'ok', ${["manual"]})
    ON CONFLICT (id) DO NOTHING`;
  const material = await upsertMaterial({ sourceId: MANUAL_SOURCE, url, title, via: "manual" });
  const note = opts.note?.trim() ?? "";
  const [row] = await sql<{ inserted: boolean; added_at: Date }[]>`
    INSERT INTO manual_candidates (article_id, notes, program, formats)
    VALUES (${material.articleId}, ${note}, ${opts.program ?? null}, ${opts.formats ?? []})
    ON CONFLICT (article_id) DO UPDATE SET
      notes = CASE WHEN EXCLUDED.notes = '' THEN manual_candidates.notes
                   WHEN manual_candidates.notes = '' THEN EXCLUDED.notes
                   ELSE manual_candidates.notes || E'\n' || EXCLUDED.notes END,
      program = COALESCE(EXCLUDED.program, manual_candidates.program),
      formats = CASE WHEN cardinality(EXCLUDED.formats) = 0 THEN manual_candidates.formats ELSE EXCLUDED.formats END
    RETURNING (xmax = 0) AS inserted, added_at`;
  if (opts.queue !== false && (material.created || material.revised || material.processingNeeded)) await queueProcessing(material.articleId);
  return {
    articleId: material.articleId,
    url,
    created: material.created,
    alreadyAdded: !row!.inserted,
    inboxDate: new Intl.DateTimeFormat("sv-SE", { timeZone: TIME_ZONE, dateStyle: "short" }).format(row!.added_at),
  };
}
