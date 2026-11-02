// IF-001 inbox file (docs/spec/07_interfaces.md): the program writes only the managed block, the person
// edits the checkbox, 節目, 格式 and 備註. Regenerating keeps what the person wrote.

export type Tier = "S" | "A" | "B";

export interface Candidate {
  /** cand_<story public id>: the program maps a tick back to its story by it, so it never changes. */
  id: string;
  title: string;
  tier: Tier;
  participantCount: number;
  sourceNames: string[];
  firstSeen: string;
  suggestedProgram: string;
  suggestedFormats: string[];
}

export interface Annotation {
  checked: boolean;
  program: string;
  formats: string;
  note: string;
}

const BEGIN = "<!-- newsroom:begin candidates -->";
const END = "<!-- newsroom:end candidates -->";
const TIER_TITLES: Record<Tier, string> = { S: "S 級", A: "A 級", B: "B 級" };

/** Interim grading by independent participants until the heat tiers of T-075 replace it. */
export function tierOf(participantCount: number): Tier {
  return participantCount >= 5 ? "S" : participantCount >= 3 ? "A" : "B";
}

export class ManagedBlockError extends Error {
  constructor() {
    super("E_MANAGED_BLOCK: inbox 受管區塊損壞，不覆寫");
  }
}

/** Reads the person's ticks, 節目, 格式 and 備註 back, by candidate id. */
export function parseAnnotations(text: string): Map<string, Annotation> {
  const out = new Map<string, Annotation>();
  const block = managedBlock(text);
  if (!block) return out;
  let current: Annotation | null = null;
  for (const line of block.split(/\r?\n/)) {
    const head = line.match(/^- \[( |x|X)\] .*`(cand_[A-Za-z0-9]+)`\s*$/);
    if (head) {
      current = { checked: head[1] !== " ", program: "", formats: "", note: "" };
      out.set(head[2]!, current);
      continue;
    }
    const field = current && line.match(/^ {2}- (節目|格式|備註)：(.*)$/);
    if (field && current) {
      const value = field[2]!.trim();
      if (field[1] === "節目") current.program = value;
      else if (field[1] === "格式") current.formats = value;
      else current.note = value;
    }
  }
  return out;
}

export function renderBlock(candidates: readonly Candidate[], notes: ReadonlyMap<string, Annotation>): string {
  const lines = [BEGIN];
  for (const tier of ["S", "A", "B"] as const) {
    const group = candidates.filter(c => c.tier === tier);
    if (!group.length) continue;
    lines.push(`## ${TIER_TITLES[tier]}`);
    for (const c of group) {
      const kept = notes.get(c.id);
      const sources = c.sourceNames.slice(0, 3).join("、");
      lines.push(
        `- [${kept?.checked ? "x" : " "}] **${c.title}** \`${c.id}\``,
        `  - 共振 ${c.participantCount} 源｜${sources}｜首次出現 ${c.firstSeen}`,
        `  - 建議：${c.suggestedProgram}｜${c.suggestedFormats.join(", ")}`,
        `  - 節目：${kept?.program ?? ""}`,
        `  - 格式：${kept?.formats ?? ""}`,
        `  - 備註：${kept?.note ?? ""}`,
      );
    }
  }
  lines.push(END);
  return lines.join("\n");
}

/** A new file, or the existing one with only its managed block replaced. Throws ManagedBlockError when the block is damaged. */
export function renderFile(date: string, candidates: readonly Candidate[], existing: string | null): string {
  const notes = existing ? parseAnnotations(existing) : new Map<string, Annotation>();
  const block = renderBlock(candidates, notes);
  if (existing === null) return `# ${date} 候選\n\n${block}\n`;
  const start = existing.indexOf(BEGIN);
  const end = existing.indexOf(END);
  if (start < 0 || end < start) throw new ManagedBlockError();
  return existing.slice(0, start) + block + existing.slice(end + END.length);
}

function managedBlock(text: string): string | null {
  const start = text.indexOf(BEGIN);
  const end = text.indexOf(END);
  return start < 0 || end < start ? null : text.slice(start + BEGIN.length, end);
}
