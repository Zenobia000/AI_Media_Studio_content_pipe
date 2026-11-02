// Where the workspace lives (docs/spec/00_overview.md §5.1) and the two things every writer of it does the same way.
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { config } from "@aihot/backend/config";
import { parseAnnotations, type Annotation } from "./format.ts";

const TIME_ZONE = "Asia/Taipei";
const OFFSET = "+08:00";

export const workspaceDir = () => process.env.NEWSROOM_HOME ?? path.join(config.dataDir, "workspace");
export const inboxDir = () => process.env.INBOX_DIR ?? path.join(workspaceDir(), "inbox");

export const clock = (d: Date, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("sv-SE", { timeZone: TIME_ZONE, ...opts }).format(d);
/** The inbox day (YYYY-MM-DD) of an instant. */
export const dayOf = (d: Date) => clock(d, { dateStyle: "short" });
/** An instant as ISO-8601 with the Taipei offset, as the front-matter of IF-002 writes it. */
export const taipeiIso = (d: Date) => clock(d, { dateStyle: "short", timeStyle: "medium" }).replace(" ", "T") + OFFSET;

/** Written to a temporary file and renamed, so an interruption never leaves half a file (R-DATA-5). */
export async function writeAtomic(file: string, text: string): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, text, "utf8");
  await rename(tmp, file);
}

/**
 * What the person wrote (ticks, 節目, 格式, 備註) in the inbox files from day `from` up to but not including day
 * `before`, oldest file first so a later day overrides an earlier one.
 */
export async function readAnnotations(dir: string, from: string, before: string): Promise<Map<string, Annotation>> {
  const names = (await readdir(dir).catch(() => [] as string[])).filter(n => /^\d{4}-\d{2}-\d{2}\.md$/.test(n)).sort();
  const notes = new Map<string, Annotation>();
  for (const name of names) {
    const day = name.slice(0, 10);
    if (day < from || day >= before) continue;
    for (const [id, a] of parseAnnotations(await readFile(path.join(dir, name), "utf8"))) notes.set(id, a);
  }
  return notes;
}
