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
  /** Added by hand (add-source): listed first, under its own heading, with what the person asked for already filled in. */
  manual?: { note: string; program: string; formats: string };
  /** Brought from an earlier day: not ticked yet and still within 48 hours ("昨日"), or ticked and not opened as a topic yet. */
  carried?: "昨日" | "已勾選";
  /** What people are saying ("HN 412 分、120 則留言"), from the signal snapshots; shown with the sources. */
  signals?: string;
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
  const groups: Array<[string, readonly Candidate[]]> = [
    ["手動新增", candidates.filter(c => c.manual)],
    ...(["S", "A", "B"] as const).map((tier): [string, readonly Candidate[]] => [TIER_TITLES[tier], candidates.filter(c => !c.manual && c.tier === tier)]),
  ];
  for (const [heading, group] of groups) {
    if (!group.length) continue;
    lines.push(`## ${heading}`);
    for (const c of group) {
      const kept = notes.get(c.id);
      const sources = [...c.sourceNames.slice(0, 3), ...(c.signals ? [c.signals] : [])].join("、");
      lines.push(
        `- [${kept?.checked ? "x" : " "}] **${c.title}** \`${c.id}\``,
        `  - 共振 ${c.participantCount} 源｜${sources}｜首次出現 ${c.firstSeen}${c.carried ? `｜${c.carried}` : ""}`,
        `  - 建議：${c.suggestedProgram}｜${c.suggestedFormats.join(", ")}`,
        `  - 節目：${kept?.program || c.manual?.program || ""}`,
        `  - 格式：${kept?.formats || c.manual?.formats || ""}`,
        `  - 備註：${kept?.note || (c.manual?.note ?? "").replace(/\s*\n\s*/g, "；")}`,
      );
    }
  }
  lines.push(END);
  return lines.join("\n");
}

/** A new file, or the existing one with only its managed block replaced. Throws ManagedBlockError when the block is damaged. */
export function renderFile(date: string, candidates: readonly Candidate[], existing: string | null, earlier: ReadonlyMap<string, Annotation> = new Map()): string {
  // What the person wrote on earlier days travels with a carried candidate; today's file overrides it.
  const notes = new Map([...earlier, ...(existing ? parseAnnotations(existing) : [])]);
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

const HEALTH_BEGIN = "<!-- newsroom:begin collect-health -->";
const HEALTH_END = "<!-- newsroom:end collect-health -->";

/** A source whose last collections kept failing. */
export interface FailingSource {
  name: string;
  failCount: number;
  lastError: string | null;
  /** Inbox-style "YYYY-MM-DD HH:mm", or null when it never succeeded. */
  lastOkAt: string | null;
}

export function renderHealth(failing: readonly FailingSource[]): string {
  const body = failing.length
    ? ["## 收集狀況", ...failing.map(f => `- **${f.name}**：連續失敗 ${f.failCount} 次｜最後成功：${f.lastOkAt ?? "從未成功"}${f.lastError ? `｜${f.lastError.replace(/\s+/g, " ").slice(0, 120)}` : ""}`)].join("\n")
    : "## 收集狀況\n所有來源最近一次收集都正常。";
  return `${HEALTH_BEGIN}\n${body}\n${HEALTH_END}`;
}

/** The inbox with its collection-health block (COL-FR-004), replaced where it is or added at the foot. */
export function withHealth(text: string, failing: readonly FailingSource[]): string {
  const block = renderHealth(failing);
  const start = text.indexOf(HEALTH_BEGIN);
  const end = text.indexOf(HEALTH_END);
  if (start >= 0 && end > start) return text.slice(0, start) + block + text.slice(end + HEALTH_END.length);
  return `${text.trimEnd()}\n\n${block}\n`;
}
