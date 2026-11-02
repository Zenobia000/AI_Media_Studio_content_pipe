// Checks the machine and the settings (CORE-FR-002). Exit 1 when anything failed. --json for the machine-readable form.
// node --env-file=.env scripts/doctor.ts [--json]
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { parseArgs } from "node:util";
import { exitCodeOf, runChecks, type Deps } from "@aihot/inbox/doctor";
import { findWorkspace } from "@aihot/inbox/init";

const run = promisify(execFile);
const { values } = parseArgs({ options: { json: { type: "boolean" } } });

const deps: Deps = {
  async exec(command, args) {
    try {
      const { stdout } = await run(command, args, { timeout: 15_000, windowsHide: true });
      return { ok: true, out: stdout };
    } catch (error) {
      return { ok: false, out: (error as Error).message };
    }
  },
  env: process.env,
  async reach(url) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(8_000), headers: { "user-agent": "MediaHOTBot/1.0 (doctor)" } });
      return true;
    } catch {
      return false;
    }
  },
  async database() {
    try {
      const { sql, closeDb } = await import("@aihot/backend/db");
      try {
        await sql`SELECT 1 FROM articles LIMIT 1`;
        return null;
      } finally {
        await closeDb();
      }
    } catch (error) {
      return (error as Error).message.slice(0, 160);
    }
  },
  nodeVersion: process.version,
  workspace: findWorkspace(process.cwd()),
};

const checks = await runChecks(deps);
const code = exitCodeOf(checks);
if (values.json) {
  console.log(JSON.stringify(code === 0
    ? { ok: true, tool: "doctor", data: { checks }, warnings: checks.filter(c => c.status === "warn").map(c => `${c.name}：${c.detail}`), next: [] }
    : { ok: false, tool: "doctor", error_code: "E_INPUT", message: `${checks.filter(c => c.status === "fail").length} 項檢查失敗`, blocked: false, data: { checks } }));
} else {
  const mark = { ok: "ok  ", warn: "warn", fail: "FAIL" };
  for (const c of checks) {
    console.log(`[${mark[c.status]}] ${c.name}：${c.detail}`);
    if (c.fix && c.status !== "ok") console.log(`       → ${c.fix}`);
  }
  const n = (s: string) => checks.filter(c => c.status === s).length;
  console.log(`\nok ${n("ok")}、warn ${n("warn")}、fail ${n("fail")}`);
}
process.exitCode = code;
