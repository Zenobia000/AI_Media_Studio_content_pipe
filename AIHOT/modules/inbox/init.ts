// init (CORE-FR-001): the workspace of docs/spec/00_overview.md §5.1. Only what is missing is made; a file or folder
// that exists is never touched (not even its modification time), so running it again changes nothing.
import { existsSync } from "node:fs";
import { mkdir, readdir, stat, symlink, writeFile } from "node:fs/promises";
import path from "node:path";

const DIRECTORIES = ["inbox", "topics", "reports/morning", "calendar", "watchlist", "data", "logs", ".claude/skills"];

export const SKILLS_DIR = path.resolve(import.meta.dirname, "../../../skills");

const CONFIG = `# 工作區標記（CORE-FR-003：往上找含 config.toml 的 newsroom/ 就是工作區根目錄）。
# 這個引擎（AIHOT 的修改版）的設定在 AIHOT/.env 與 AIHOT/site/、AIHOT/industry/，不在這裡。
schema_version = 1
`;

const VOICE = `# 口吻

寫稿時要像誰說話？在這裡寫下：用詞、語氣、不用的說法、固定開頭或結尾。
範例與反例越具體越好。
`;

const GLOSSARY = `# 名詞表

專有名詞的固定寫法與譯名。格式：\`英文原文｜固定寫法｜備註\`

| 原文 | 固定寫法 | 備註 |
|---|---|---|
| | | |
`;

const SITES = `# 要盯的網站

收集器的來源清單在 \`AIHOT/industry/sources.json\`，這份是給人記的筆記：哪些來源為什麼要盯、哪些不要。

| 名稱 | 網址 | 權重 | 預設節目 |
|---|---|---|---|
| | | | |
`;

const X = `# X（Twitter）帳號與 List

X 收集器（R4）需要 X API 或 SocialData。要追的 List ID 與帳號寫在這裡。

| List ID／帳號 | 權重 | 備註 |
|---|---|---|
| | | |
`;

const CREATORS = `# 中文圈創作者

在地熱度檢查（R4）要比對的創作者與平台帳號。

| 名稱 | 平台 | 帳號 |
|---|---|---|
| | | |
`;

const FORUMS = `# 論壇

Reddit 的公開 API 預計 2027-03 前關閉，這份清單先不使用（PRD Q5）。
`;

const EVENTS = `# 發表會行事曆

| 日期 | 時間 | 活動 | 關鍵字 | 官方連結 |
|---|---|---|---|---|
| | | | | |
`;

const GITIGNORE = `data/
logs/
topics/*/media/
*.tmp
`;

/** The workspace CLAUDE.md (AGT-FR-002): at most 200 lines, so only the role, the hard rules and where to look. */
export const CLAUDE_MD = `# 內容產線工作區

你是 AI Media Studio 內容產線的助手：幫創作者把 AI 與科技新聞做成 Threads 貼文、IG 輪播和 Reel。
創作者用口語下需求，你依 \`.claude/skills/\` 裡的 skill 呼叫工具，不自己做判斷以外的事。

## 硬性規則

1. **不替人確認，也不發布。** 勾選候選、確認稿件、發布都要人明確說了才做。沒有人的確認，不呼叫任何會發布或排程的工具。
2. **只透過工具改檔。** 不手動編輯 \`topics/\` 底下由工具產生的檔案；受管區塊（\`<!-- newsroom:begin … -->\` 到 \`end\`）之外是人的內容，絕對不動。
3. **不編造。** 原文抓不到就告訴人，請他貼進 \`sources/manual-<n>.md\`；缺素材就說缺什麼。數字、名稱、日期只用 \`sources/\` 裡看得到的。
4. **不繞過網站的阻擋。** 抓取被擋（HTTP 403 等）就回報，不要改標頭、換身分或用其他方式硬抓。
5. **看到錯誤就照實說。** 工具回 \`ok: false\` 時，轉述 \`message\` 與 \`next\`，不要假裝成功。

## 怎麼找該用哪個 skill

哪種需求先讀哪份 skill，看 \`skills/RESOLVER.md\`（建立後）。目前有的：

| 使用者會說 | skill |
|---|---|
| 「做第 3 題」「把勾選的建題」「這兩題合成一題」 | \`open-topic\` |
| 「幫我抓原文」「這題素材夠嗎」「我放了影片，更新一下」 | \`gather-assets\` |

## 工作區

- \`inbox/YYYY-MM-DD.md\`：每日候選。人只改勾選、節目、格式、備註。
- \`topics/<topic_id>/\`：一個題目。\`topic.md\`、\`notes.md\`、\`sources/\`、\`media/\`、\`assets.json\`、\`media.md\`、\`contents/<format>-<nn>/\`。
- 工作區位置：環境變數 \`NEWSROOM_HOME\`，或目前目錄往上找含 \`config.toml\` 的 \`newsroom/\`。

## 工具

都在 \`AIHOT/scripts/\`，用 \`node --env-file=.env scripts/<名稱>.ts … --json\` 呼叫；成功與失敗都輸出 JSON（\`ok\`、\`data\`、\`error_code\`、\`message\`、\`next\`）。結束碼：0 成功、2 輸入有誤、3 被阻擋（要人補資料）。

| 工具 | 做什麼 |
|---|---|
| \`add-source\` | 手動新增候選 |
| \`open-topic\`、\`merge-candidates\` | 建題、合併候選 |
| \`fetch-topic\` | 擷取來源原文 |
| \`fetch-media\` | 下載網頁與影音平台上的媒體 |
| \`assets-topic\` | 盤點素材與缺口 |
| \`doctor\` | 檢查環境 |
`;

export interface InitResult {
  root: string;
  created: string[];
  existing: string[];
  warnings: string[];
}

const TEMPLATES: Array<[string, string]> = [
  ["config.toml", CONFIG], ["voice.md", VOICE], ["glossary.md", GLOSSARY], ["CLAUDE.md", CLAUDE_MD], [".gitignore", GITIGNORE],
  ["watchlist/sites.md", SITES], ["watchlist/x.md", X], ["watchlist/creators.md", CREATORS], ["watchlist/forums.md", FORUMS], ["calendar/events.md", EVENTS],
];

/** Makes the workspace at `root`; `skills` is the folder whose subfolders become the skills the workspace sees. */
export async function initWorkspace(root: string, opts: { skills?: string } = {}): Promise<InitResult> {
  const result: InitResult = { root, created: [], existing: [], warnings: [] };
  const note = (rel: string, existed: boolean) => (existed ? result.existing : result.created).push(rel);
  for (const dir of DIRECTORIES) {
    const full = path.join(root, dir);
    const existed = existsSync(full);
    if (!existed) await mkdir(full, { recursive: true });
    note(`${dir}/`, existed);
  }
  for (const [rel, text] of TEMPLATES) {
    const full = path.join(root, rel);
    const existed = existsSync(full);
    if (!existed) await writeFile(full, text, { encoding: "utf8", flag: "wx" });
    note(rel, existed);
  }

  // A link per skill, so Claude Code opened in the workspace sees them (a junction on Windows, which needs no privilege).
  const skills = opts.skills ?? SKILLS_DIR;
  const names = existsSync(skills) ? (await readdir(skills, { withFileTypes: true })).filter(e => e.isDirectory() && !e.name.startsWith("_")).map(e => e.name).sort() : [];
  if (!names.length) result.warnings.push(`找不到任何 skill（${skills}）；工作區的 .claude/skills/ 是空的`);
  for (const name of names) {
    const link = path.join(root, ".claude/skills", name);
    const existed = await stat(link).then(() => true, () => false);
    if (!existed) await symlink(path.join(skills, name), link, "junction");
    note(`.claude/skills/${name}`, existed);
  }
  return result;
}

/** The workspace root: NEWSROOM_HOME, else the first folder going up from `from` that holds newsroom/config.toml (or is itself one). */
export function findWorkspace(from: string, env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.NEWSROOM_HOME) return path.resolve(env.NEWSROOM_HOME);
  for (let dir = path.resolve(from); ; dir = path.dirname(dir)) {
    if (existsSync(path.join(dir, "config.toml")) && existsSync(path.join(dir, "inbox"))) return dir;
    if (existsSync(path.join(dir, "newsroom", "config.toml"))) return path.join(dir, "newsroom");
    if (path.dirname(dir) === dir) return null;
  }
}
