// Tries each fetch layer on a fixed address (FET-FR-010) and checks the downloader's version. Exit 1 when a layer failed.
// Output follows IF-020 with --json. The addresses are in modules/inbox/selftest.json (--file to use another).
// node --env-file=.env scripts/fetch-selftest.ts [--file targets.json] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { loadTargets, runSelftest } from "@aihot/inbox/selftest";

const { values } = parseArgs({ options: { json: { type: "boolean" }, file: { type: "string" } } });
try {
  const checks = await runSelftest(await loadTargets(values.file));
  const failed = checks.filter(c => c.status === "fail");
  if (values.json) {
    console.log(JSON.stringify(failed.length
      ? { ok: false, tool: "fetch-selftest", error_code: "E_EXTERNAL", message: `${failed.length} 項失敗`, blocked: false, data: { checks } }
      : { ok: true, tool: "fetch-selftest", data: { checks }, warnings: checks.filter(c => c.status === "warn").map(c => `${c.name}：${c.detail}`), next: [] }));
  } else {
    const mark = { ok: "ok  ", warn: "warn", fail: "FAIL" };
    for (const c of checks) console.log(`[${mark[c.status]}] ${c.name}${c.url ? `  ${c.url}` : ""}\n       ${c.detail}`);
    console.log(`\nok ${checks.filter(c => c.status === "ok").length}、warn ${checks.filter(c => c.status === "warn").length}、fail ${failed.length}`);
  }
  process.exitCode = failed.length ? 1 : 0;
} finally {
  await closeDb();
}
