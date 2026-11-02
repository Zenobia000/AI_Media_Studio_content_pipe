// The cleaned body HTML of an article (content/extract.ts) as Markdown, the way IF-004 keeps it: headings, links,
// lists, quotes and code, with images kept by their original address.
import { parseHTML } from "linkedom";

type Node = { nodeType: number; nodeName: string; textContent: string | null; childNodes: ArrayLike<Node>; getAttribute?: (name: string) => string | null };

const BLOCKS = new Set(["P", "DIV", "SECTION", "ARTICLE", "FIGURE", "FIGCAPTION", "MAIN", "TABLE", "TBODY", "THEAD", "TR"]);

function inline(node: Node, base: string): string {
  let out = "";
  for (const child of Array.from(node.childNodes)) out += render(child, base, 0);
  return out;
}

function absolute(href: string | null | undefined, base: string): string | null {
  if (!href) return null;
  try {
    return new URL(href, base).toString();
  } catch {
    return null;
  }
}

function render(node: Node, base: string, depth: number): string {
  if (node.nodeType === 3) return (node.textContent ?? "").replace(/\s+/g, " ");
  if (node.nodeType !== 1) return "";
  const name = node.nodeName.toUpperCase();
  const children = () => inline(node, base);
  switch (name) {
    case "SCRIPT": case "STYLE": case "NOSCRIPT": case "SVG": return "";
    case "H1": case "H2": case "H3": case "H4": case "H5": case "H6":
      return `\n\n${"#".repeat(Number(name[1]))} ${children().trim()}\n\n`;
    case "BR": return "  \n";
    case "HR": return "\n\n---\n\n";
    case "STRONG": case "B": return children().trim() ? `**${children().trim()}**` : "";
    case "EM": case "I": return children().trim() ? `*${children().trim()}*` : "";
    case "CODE": return `\`${(node.textContent ?? "").replace(/`/g, "'")}\``;
    case "PRE": return `\n\n\`\`\`\n${(node.textContent ?? "").replace(/\n+$/, "")}\n\`\`\`\n\n`;
    case "A": {
      const href = absolute(node.getAttribute?.("href"), base);
      const text = children().trim();
      return href && text ? `[${text}](${href})` : text;
    }
    case "IMG": {
      const src = absolute(node.getAttribute?.("src") ?? node.getAttribute?.("data-src"), base);
      return src ? `![${(node.getAttribute?.("alt") ?? "").replace(/[\[\]]/g, "")}](${src})` : "";
    }
    case "BLOCKQUOTE":
      return `\n\n${children().trim().split("\n").map(l => `> ${l}`).join("\n")}\n\n`;
    case "UL": case "OL": {
      const items = Array.from(node.childNodes).filter(c => c.nodeName.toUpperCase() === "LI");
      const lines = items.map((li, i) => `${"  ".repeat(depth)}${name === "OL" ? `${i + 1}.` : "-"} ${inline(li, base).trim().replace(/\n{2,}/g, "\n")}`);
      return `\n\n${lines.join("\n")}\n\n`;
    }
    case "TD": case "TH": return `${children().trim()} | `;
    default:
      return BLOCKS.has(name) ? `\n\n${children().trim()}\n\n` : children();
  }
}

export function htmlToMarkdown(html: string, base: string): string {
  const { document } = parseHTML(`<!doctype html><html><body>${html}</body></html>`);
  return render(document.body as unknown as Node, base, 0)
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
