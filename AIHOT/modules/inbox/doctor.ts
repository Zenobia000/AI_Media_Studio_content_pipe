// doctor (CORE-FR-002): what this machine has and lacks for the pipeline, each item ok, warn or fail with how to fix it.
// fail: something the pipeline cannot work without. warn: something a part of it needs, or that cannot be checked here.

export type Status = "ok" | "warn" | "fail";

export interface Check {
  name: string;
  status: Status;
  detail: string;
  fix?: string;
}

export interface Deps {
  /** Runs a program; ok is false when it is missing or exits non-zero. */
  exec(command: string, args: string[]): Promise<{ ok: boolean; out: string }>;
  env: NodeJS.ProcessEnv;
  /** Whether an address answers at all. */
  reach(url: string): Promise<boolean>;
  /** An error message when the database cannot be used, else null. */
  database(): Promise<string | null>;
  nodeVersion: string;
  /** The workspace folder found, or null. */
  workspace: string | null;
}

export const MIN_NODE = [24, 22, 1] as const;
const SERVICES: Array<[string, string]> = [["Hacker News", "https://hn.algolia.com/api/v1/search?query=ai&hitsPerPage=1"], ["GitHub", "https://api.github.com/zen"], ["Hugging Face", "https://huggingface.co/api/daily_papers"]];

const FFMPEG_FIX = "安裝 ffmpeg 並確認 ffmpeg 與 ffprobe 都在 PATH：Windows `winget install Gyan.FFmpeg`、macOS `brew install ffmpeg`、Linux `sudo apt install ffmpeg`";

export function atLeast(version: string, min: readonly number[]): boolean {
  const parts = version.replace(/^v/, "").split(".").map(Number);
  for (let i = 0; i < min.length; i++) {
    if ((parts[i] ?? 0) !== min[i]!) return (parts[i] ?? 0) > min[i]!;
  }
  return true;
}

export async function runChecks(d: Deps): Promise<Check[]> {
  const checks: Check[] = [];
  const add = (c: Check) => checks.push(c);

  add(atLeast(d.nodeVersion, MIN_NODE)
    ? { name: "Node.js", status: "ok", detail: d.nodeVersion }
    : { name: "Node.js", status: "fail", detail: `${d.nodeVersion}（需要 ${MIN_NODE.join(".")} 以上）`, fix: "安裝 Node.js 24：https://nodejs.org/en/download，Windows 可用 `winget install OpenJS.NodeJS.LTS`" });

  for (const tool of ["ffmpeg", "ffprobe"]) {
    const r = await d.exec(tool, ["-version"]);
    add(r.ok ? { name: tool, status: "ok", detail: r.out.split("\n")[0]!.trim() } : { name: tool, status: "fail", detail: "找不到", fix: FFMPEG_FIX });
  }

  const python = await d.exec("python", ["--version"]);
  add(python.ok && /Python 3/.test(python.out)
    ? { name: "Python", status: "ok", detail: python.out.trim() }
    : { name: "Python", status: "warn", detail: "找不到 Python 3", fix: "這個引擎不需要 Python，但組員的工具（字幕、渲染）可能需要；需要時安裝 Python 3.11 以上" });

  const docker = await d.exec("docker", ["info", "--format", "{{.ServerVersion}}"]);
  add(docker.ok
    ? { name: "Docker", status: "ok", detail: `服務運作中（${docker.out.trim()}）` }
    : { name: "Docker", status: "warn", detail: "沒有執行中的 Docker 服務", fix: "啟動 Docker Desktop；用 Docker 跑 PostgreSQL 或網頁變動監測時需要" });

  const gpu = await d.exec("nvidia-smi", ["-L"]);
  add(gpu.ok ? { name: "GPU", status: "ok", detail: gpu.out.split("\n")[0]!.trim() } : { name: "GPU", status: "warn", detail: "找不到 NVIDIA GPU", fix: "語音辨識與影片渲染較快需要 GPU；只做文字與輪播可以忽略" });

  const claude = await d.exec("claude", ["--version"]);
  add(claude.ok
    ? { name: "Claude Code", status: "warn", detail: `${claude.out.trim()}（已安裝；登入狀態無法自動檢查）`, fix: "在終端機執行 `claude` 確認已登入" }
    : { name: "Claude Code", status: "warn", detail: "找不到 claude 指令", fix: "安裝 Claude Code：https://docs.claude.com/en/docs/claude-code，再執行 `claude` 登入" });

  if (!d.env.DATABASE_URL) add({ name: "資料庫", status: "fail", detail: "沒有設定 DATABASE_URL", fix: "設定 DATABASE_URL，例如 postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev，並執行 `node scripts/migrate.ts`" });
  else {
    const error = await d.database();
    add(error === null
      ? { name: "資料庫", status: "ok", detail: "連得上，且已有資料表" }
      : { name: "資料庫", status: "fail", detail: error, fix: "確認 PostgreSQL 已啟動、資料庫存在，並執行 `node scripts/migrate.ts`" });
  }

  const wants = (name: string) => d.env[name] === "true";
  if ((wants("MODEL_CALLS_ENABLED") || wants("COLLECT_ENABLED")) && !d.env.LLM_API_KEY) {
    add({ name: "模型金鑰", status: "fail", detail: "已啟用模型呼叫或收集，但沒有 LLM_API_KEY", fix: "在 .env 設定 LLM_API_KEY（node scripts/init-env.ts --llm-key <金鑰>）" });
  } else add({ name: "模型金鑰", status: d.env.LLM_API_KEY ? "ok" : "warn", detail: d.env.LLM_API_KEY ? "已設定" : "沒有設定（模型呼叫目前也沒有啟用）", ...(d.env.LLM_API_KEY ? {} : { fix: "要用模型精選與翻譯時，在 .env 設定 LLM_API_KEY" }) });
  for (const [name, use] of [["GITHUB_TOKEN", "GitHub 擷取較不會被限速"], ["JINA_API_KEY", "原文擷取失敗時的備援"]] as const) {
    add(d.env[name] ? { name, status: "ok", detail: "已設定" } : { name, status: "warn", detail: "沒有設定（選用）", fix: `設定後可用於：${use}` });
  }

  for (const [name, url] of SERVICES) {
    add((await d.reach(url)) ? { name: `連線：${name}`, status: "ok", detail: "連得上" } : { name: `連線：${name}`, status: "warn", detail: "連不上", fix: "確認網路；對應的收集器會暫時失敗，但不影響其他來源" });
  }

  add(d.workspace
    ? { name: "工作區", status: "ok", detail: d.workspace }
    : { name: "工作區", status: "warn", detail: "還沒有工作區", fix: "執行 `node scripts/init-workspace.ts <路徑>` 建立，再設定 NEWSROOM_HOME" });
  return checks;
}

/** Exit code: 1 when anything failed. */
export const exitCodeOf = (checks: readonly Check[]) => (checks.some(c => c.status === "fail") ? 1 : 0);
