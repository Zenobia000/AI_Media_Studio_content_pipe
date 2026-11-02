// The front-matter of a Markdown file (IF-002, IF-003): `key: value` lines between two `---` lines. An update changes
// the lines of the keys it names and adds new keys at the end; every other byte of the file, unknown keys and the body
// included, stays as it was (CORE-FR-005).
const FENCE = /^---[ \t]*$/;

interface Split {
  head: string[];
  /** The lines after the closing fence (none when the file ends with it). */
  rest: string[];
  eol: string;
}

function split(text: string): Split | null {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(eol);
  if (!FENCE.test(lines[0] ?? "")) return null;
  const end = lines.findIndex((l, i) => i > 0 && FENCE.test(l));
  if (end < 0) return null;
  return { head: lines.slice(1, end), rest: lines.slice(end + 1), eol };
}

const keyOf = (line: string) => /^([A-Za-z_][\w-]*)[ \t]*:/.exec(line)?.[1] ?? null;

/** The fields as written (values as text, quotes removed), and the body. A file without front-matter has none. */
export function readFrontMatter(text: string): { fields: Record<string, string>; body: string } {
  const s = split(text);
  if (!s) return { fields: {}, body: text };
  const fields: Record<string, string> = {};
  for (const line of s.head) {
    const key = keyOf(line);
    if (key) fields[key] = line.slice(line.indexOf(":") + 1).trim().replace(/^(["'])(.*)\1$/, "$2");
  }
  return { fields, body: s.rest.join(s.eol) };
}

/** A value that YAML would read as something else, or that has a line break or a colon-space, is quoted. */
const render = (value: string) => (/^$|^[\s"'\[{&*!|>%@`#-]|[:#]\s|\s$|[\r\n]/.test(value) ? JSON.stringify(value) : value);

/** Sets the given fields; a file without front-matter gets a block in front of its text. */
export function updateFrontMatter(text: string, updates: Record<string, string>): string {
  const s = split(text);
  const eol = s?.eol ?? (text.includes("\r\n") ? "\r\n" : "\n");
  const head = s ? [...s.head] : [];
  for (const [key, value] of Object.entries(updates)) {
    const line = `${key}: ${render(value)}`;
    const at = head.findIndex(l => keyOf(l) === key);
    if (at >= 0) head[at] = line;
    else head.push(line);
  }
  const block = ["---", ...head, "---"].join(eol);
  return s ? block + (s.rest.length ? eol + s.rest.join(eol) : "") : block + eol + text;
}
