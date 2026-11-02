// Short links (t.co, bit.ly, ...) name the same page under another address, so the one a person adds is followed to the
// page before the identity rule sees it. A link that cannot be followed is kept as it is.
import { defaultGet, type Get } from "./source-extract.ts";

const SHORTENERS = new Set(["t.co", "bit.ly", "buff.ly", "lnkd.in", "goo.gl", "tinyurl.com", "ow.ly", "is.gd", "dlvr.it", "t.ly", "cutt.ly", "reurl.cc", "fb.me", "wp.me"]);

export function isShortUrl(input: string): boolean {
  try {
    return SHORTENERS.has(new URL(input.trim()).hostname.toLowerCase().replace(/^www\./, ""));
  } catch {
    return false;
  }
}

export async function expandShortUrl(input: string, get: Get = defaultGet): Promise<string> {
  if (!isShortUrl(input)) return input;
  try {
    const page = await get(input.trim());
    return page.status < 400 && /^https?:\/\//.test(page.url) && !isShortUrl(page.url) ? page.url : input;
  } catch {
    return input;
  }
}
