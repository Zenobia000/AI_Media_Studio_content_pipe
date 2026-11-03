// FET-FR-001: the text of one source as Markdown. The sites that have an interface of their own are read through
// it (GitHub, arXiv, YouTube); everything else goes through the engine's Readability extraction, then Jina Reader
// when a key is set (SPK-FET-1: docs/spikes/SPK-FET-1.md). A page that gives under 200 characters is not a source.
import { credential } from "@aihot/backend/config";
import { extractFromUrl, readable } from "@aihot/backend/content/extract";
import { guardedFetch } from "@aihot/backend/lib/http-fetch";
import { siteIdentityForUrl } from "@aihot/backend/lib/url";
import { parseHTML } from "linkedom";
import { htmlToMarkdown } from "./html-markdown.ts";

export const MIN_CHARS = 200;

export type Kind = "article" | "tweet" | "release" | "video" | "paper" | "forum" | "other";

export interface Extracted {
  kind: Kind;
  title: string;
  publisher: string | null;
  author: string | null;
  /** ISO-8601 in UTC, when the page says. */
  publishedAt: string | null;
  language: string;
  /** The way it was read: github-api, arxiv-api, youtube, readability or jina. */
  method: string;
  markdown: string;
  /** Addresses the text links to, for finding the official source behind a report. */
  links: string[];
}

export interface Page {
  status: number;
  url: string;
  type: string;
  text: string;
}
export type Get = (url: string, headers?: Record<string, string>) => Promise<Page>;

/** Why nothing could be taken from an address. */
export class ExtractFailure extends Error {}

export const defaultGet: Get = async (url, headers) => {
  const res = await guardedFetch(url, { headers, timeoutMs: 20_000, maxBytes: 16 * 1024 * 1024 });
  return { status: res.status, url: res.url, type: res.headers.get("content-type") ?? "", text: res.text() };
};

/** Characters of text, Chinese counting one each and other words one each (a measure of length, not a word count). */
export function wordCount(markdown: string): number {
  const text = markdown.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[#>*`_~|-]/g, " ");
  return (text.match(/[㐀-鿿]/g)?.length ?? 0) + (text.replace(/[㐀-鿿]/g, " ").match(/[A-Za-z0-9À-ɏ]+/g)?.length ?? 0);
}

export function languageOf(text: string): string {
  const cjk = text.match(/[㐀-鿿]/g)?.length ?? 0;
  return cjk / Math.max(1, text.length) > 0.15 ? "zh" : "en";
}

const longEnough = (markdown: string) => markdown.replace(/\s+/g, "").length >= MIN_CHARS;

function meta(document: ReturnType<typeof parseHTML>["document"], ...names: string[]): string | null {
  for (const name of names) {
    const el = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
    const content = el?.getAttribute("content")?.trim();
    if (content) return content;
  }
  return null;
}

async function json<T>(get: Get, url: string, headers: Record<string, string> = {}): Promise<T | null> {
  const page = await get(url, { accept: "application/vnd.github+json", ...headers });
  return page.status === 200 ? (JSON.parse(page.text) as T) : null;
}

async function github(get: Get, owner: string, repo: string, tag: string | null): Promise<Extracted> {
  const token = credential("collectors", "GITHUB_TOKEN");
  const auth: Record<string, string> = token ? { authorization: `Bearer ${token}` } : {};
  const readme = await get(`https://api.github.com/repos/${owner}/${repo}/readme`, { accept: "application/vnd.github.raw+json", ...auth });
  const release = await json<{ tag_name: string; name: string | null; body: string | null; published_at: string | null }>(
    get, `https://api.github.com/repos/${owner}/${repo}/releases/${tag ? `tags/${encodeURIComponent(tag)}` : "latest"}`, auth);
  const parts = [`# ${owner}/${repo}`];
  if (release?.body) parts.push(`## Release ${release.tag_name}${release.name && release.name !== release.tag_name ? `：${release.name}` : ""}`, release.body.trim());
  if (!tag && readme.status === 200) parts.push("## README", readme.text.trim());
  const markdown = parts.join("\n\n");
  if (!longEnough(markdown)) throw new ExtractFailure(`GitHub 沒有給出足夠的內容（README HTTP ${readme.status}${release ? "" : "，沒有 release"}）`);
  return { kind: release?.body ? "release" : "other", title: `${owner}/${repo}`, publisher: "GitHub", author: owner, publishedAt: release?.published_at ?? null,
    language: languageOf(markdown), method: "github-api", markdown, links: [...markdown.matchAll(/\]\((https?:[^)\s]+)\)/g)].map(m => m[1]!) };
}

async function arxiv(get: Get, id: string): Promise<Extracted> {
  const page = await get(`https://export.arxiv.org/api/query?id_list=${encodeURIComponent(id)}`);
  const entry = page.text.slice(Math.max(0, page.text.indexOf("<entry>")));
  const summary = /<summary>([\s\S]*?)<\/summary>/.exec(entry)?.[1]?.replace(/\s+/g, " ").trim();
  const title = /<title>([\s\S]*?)<\/title>/.exec(entry)?.[1]?.replace(/\s+/g, " ").trim();
  if (page.status !== 200 || !summary || !title) throw new ExtractFailure(`arXiv 沒有找到 ${id} 的摘要（HTTP ${page.status}）`);
  const authors = [...entry.matchAll(/<author>\s*<name>([^<]+)<\/name>/g)].map(m => m[1]!.trim());
  const markdown = `# ${title}\n\n作者：${authors.join("、")}\n\n## 摘要\n\n${summary}\n\nPDF：https://arxiv.org/pdf/${id}`;
  return { kind: "paper", title, publisher: "arXiv", author: authors[0] ?? null, publishedAt: /<published>([^<]+)<\/published>/.exec(entry)?.[1] ?? null,
    language: "en", method: "arxiv-api", markdown, links: [`https://arxiv.org/pdf/${id}`] };
}

async function youtube(get: Get, id: string): Promise<Extracted> {
  const watch = `https://www.youtube.com/watch?v=${id}`;
  const oembed = await json<{ title: string; author_name: string }>(get, `https://www.youtube.com/oembed?url=${encodeURIComponent(watch)}&format=json`, { accept: "application/json" });
  if (!oembed) throw new ExtractFailure("YouTube 沒有回應這支影片（可能是私人或已刪除）");
  const page = await get(watch);
  const description = meta(parseHTML(page.text).document, "og:description", "description");
  const markdown = `# ${oembed.title}\n\n頻道：${oembed.author_name}\n\n${description ?? ""}\n\n影片：${watch}`;
  if (!longEnough(markdown)) throw new ExtractFailure("影片說明太短，沒有足夠的文字可以當原文；請把重點貼進 sources/manual-1.md");
  return { kind: "video", title: oembed.title, publisher: "YouTube", author: oembed.author_name, publishedAt: null, language: languageOf(markdown), method: "youtube", markdown, links: [] };
}

async function page(get: Get, url: string, subject: string): Promise<Extracted> {
  let failure = "沒有抓到內容";
  try {
    const res = await get(url, { "accept-language": "zh-TW,zh;q=0.9,en;q=0.8" });
    if (res.status !== 200) failure = `HTTP ${res.status}`;
    else if (!/html/.test(res.type)) failure = `不是網頁（${res.type || "未知型別"}）`;
    else {
      const body = readable(res.text, res.url);
      if (body) return fromBody(body, res, "readability");
      failure = "網頁裡找不到足夠的正文（可能要執行 JavaScript，或在付費牆後面）";
    }
  } catch (error) {
    failure = (error as Error).message.slice(0, 120);
  }
  if (credential("collectors", "JINA_API_KEY")) {
    const body = await extractFromUrl(url, subject, {}, { paidFallback: true });
    if (body) return fromBody(body, { url, text: "", status: 200, type: "text/html" }, "jina");
    failure += "；Jina Reader 也沒有讀到";
  }
  throw new ExtractFailure(failure);
}

function fromBody(body: { html: string; text: string; publishedAt?: Date | null }, res: Page, method: "readability" | "jina"): Extracted {
  const markdown = htmlToMarkdown(body.html, res.url);
  if (!longEnough(markdown)) throw new ExtractFailure(`正文不到 ${MIN_CHARS} 字`);
  const { document } = parseHTML(res.text || "<html></html>");
  const title = document.querySelector("h1")?.textContent?.trim() || meta(document, "og:title") || document.title || res.url;
  return {
    kind: "article",
    title,
    publisher: meta(document, "og:site_name") ?? new URL(res.url).hostname.replace(/^www\./, ""),
    author: meta(document, "author", "article:author"),
    publishedAt: body.publishedAt?.toISOString() ?? null,
    language: document.documentElement?.getAttribute("lang")?.slice(0, 2) || languageOf(markdown),
    method,
    markdown,
    links: [...body.html.matchAll(/href="(https?:[^"]+)"/g)].map(m => m[1]!.replace(/&amp;/g, "&")),
  };
}

export async function extractSource(url: string, opts: { get?: Get; subject?: string } = {}): Promise<Extracted> {
  const get = opts.get ?? defaultGet;
  const site = siteIdentityForUrl(url);
  if (site?.startsWith("arxiv:")) return arxiv(get, site.slice("arxiv:".length));
  if (site?.startsWith("youtube:")) return youtube(get, site.slice("youtube:".length));
  if (site?.startsWith("github:")) {
    const [owner, repo] = new URL(url).pathname.split("/").filter(Boolean) as [string, string];
    return github(get, owner, repo.replace(/\.git$/, ""), null);
  }
  const release = /^https?:\/\/(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+)\/releases\/tag\/([^/?#]+)/i.exec(url);
  if (release) return github(get, release[1]!, release[2]!, decodeURIComponent(release[3]!));
  if (/^https?:\/\/(?:(?:www|mobile|m)\.)?(?:x|twitter)\.com\//i.test(url)) throw new ExtractFailure("推文沒有公開的讀取方式（需要 X API 或 SocialData，R4 才支援）");
  return page(get, url, opts.subject ?? "topic-source");
}
