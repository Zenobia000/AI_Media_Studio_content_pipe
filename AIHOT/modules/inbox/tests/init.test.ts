import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { atLeast, exitCodeOf, runChecks, type Deps } from "../doctor.ts";
import { CLAUDE_MD, findWorkspace, initWorkspace, SKILLS_DIR } from "../init.ts";

let tmp: string;
before(async () => {
  tmp = await mkdtemp(path.join(tmpdir(), "newsroom-init-"));
});
after(() => rm(tmp, { recursive: true, force: true }));

async function tree(root: string): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    out[full] = (await stat(full)).mtimeMs;
    if (entry.isDirectory() && !entry.name.startsWith("open-") && !entry.name.startsWith("gather-")) Object.assign(out, await tree(full));
  }
  return out;
}

test("an empty folder becomes a whole workspace in which every skill can be read", async () => {
  const root = path.join(tmp, "a", "newsroom");
  const r = await initWorkspace(root);
  assert.equal(r.existing.length, 0);
  for (const rel of ["inbox", "topics", "reports/morning", "calendar", "watchlist", "data", "logs", ".claude/skills"]) assert.ok((await stat(path.join(root, rel))).isDirectory(), rel);
  for (const rel of ["config.toml", "voice.md", "glossary.md", "CLAUDE.md", ".gitignore", "watchlist/sites.md", "watchlist/x.md", "watchlist/creators.md", "watchlist/forums.md", "calendar/events.md"]) {
    assert.ok((await stat(path.join(root, rel))).isFile(), rel);
  }
  const skills = (await readdir(SKILLS_DIR, { withFileTypes: true })).filter(e => e.isDirectory()).map(e => e.name);
  assert.ok(skills.includes("open-topic") && skills.includes("gather-assets"));
  for (const name of skills) assert.match(await readFile(path.join(root, ".claude/skills", name, "SKILL.md"), "utf8"), new RegExp(`name: ${name}`));
  assert.match(await readFile(path.join(root, ".gitignore"), "utf8"), /topics\/\*\/media\//);
  assert.ok(CLAUDE_MD.split("\n").length <= 200);
});

test("running it again changes nothing, not even a modification time, and keeps what a person wrote", async () => {
  const root = path.join(tmp, "b", "newsroom");
  await initWorkspace(root);
  await writeFile(path.join(root, "voice.md"), "# 我的口吻\n\n短句，不用驚嘆號。\n", "utf8");
  const before = await tree(root);
  await new Promise(resolve => setTimeout(resolve, 30));
  const again = await initWorkspace(root);
  assert.deepEqual(again.created, []);
  assert.deepEqual(await tree(root), before);
  assert.match(await readFile(path.join(root, "voice.md"), "utf8"), /短句/);
});

test("a missing piece is made without touching the others", async () => {
  const root = path.join(tmp, "c", "newsroom");
  await initWorkspace(root);
  await rm(path.join(root, "watchlist/x.md"));
  const sites = (await stat(path.join(root, "watchlist/sites.md"))).mtimeMs;
  await new Promise(resolve => setTimeout(resolve, 30));
  const again = await initWorkspace(root);
  assert.deepEqual(again.created, ["watchlist/x.md"]);
  assert.equal((await stat(path.join(root, "watchlist/sites.md"))).mtimeMs, sites);
});

test("the workspace is found from NEWSROOM_HOME, or going up from a folder inside it", async () => {
  const root = path.join(tmp, "d", "newsroom");
  await initWorkspace(root);
  const inside = path.join(root, "topics", "x", "contents");
  await mkdir(inside, { recursive: true });
  assert.equal(findWorkspace(inside, {}), root);
  assert.equal(findWorkspace(path.join(tmp, "d"), {}), root);
  assert.equal(findWorkspace(inside, { NEWSROOM_HOME: "/somewhere/else" }), path.resolve("/somewhere/else"));
  assert.equal(findWorkspace(path.join(tmp, "nowhere"), {}), null);
});

const good: Deps = {
  exec: async (command) => ({ ok: true, out: command === "python" ? "Python 3.12.1" : `${command} version 1.0\nmore` }),
  env: { DATABASE_URL: "postgres://x/y", LLM_API_KEY: "k", GITHUB_TOKEN: "t", JINA_API_KEY: "j" },
  reach: async () => true,
  database: async () => null,
  nodeVersion: "v24.22.1",
  workspace: "/ws",
};
const byName = (checks: Awaited<ReturnType<typeof runChecks>>, name: string) => checks.find(c => c.name === name)!;

test("a machine with everything has no failure and doctor exits 0", async () => {
  const checks = await runChecks(good);
  assert.equal(exitCodeOf(checks), 0);
  assert.equal(byName(checks, "ffmpeg").status, "ok");
});

test("without ffmpeg doctor says fail with how to install it, and the exit code is not 0", async () => {
  const checks = await runChecks({ ...good, exec: async (command) => (command === "ffmpeg" || command === "ffprobe" ? { ok: false, out: "ENOENT" } : good.exec(command, [])) });
  assert.equal(byName(checks, "ffmpeg").status, "fail");
  assert.match(byName(checks, "ffmpeg").fix!, /ffmpeg/);
  assert.equal(exitCodeOf(checks), 1);
});

test("an old Node, a missing database setting and a database that cannot be used are failures; missing optional things only warn", async () => {
  assert.equal(byName(await runChecks({ ...good, nodeVersion: "v22.1.0" }), "Node.js").status, "fail");
  assert.equal(byName(await runChecks({ ...good, env: {} }), "資料庫").status, "fail");
  const down = await runChecks({ ...good, database: async () => "connection refused" });
  assert.equal(byName(down, "資料庫").detail, "connection refused");
  assert.equal(exitCodeOf(down), 1);
  const bare = await runChecks({ ...good, env: { DATABASE_URL: "x" }, exec: async () => ({ ok: false, out: "" }), reach: async () => false, workspace: null });
  for (const name of ["Docker", "GPU", "Claude Code", "Python", "連線：GitHub", "工作區", "GITHUB_TOKEN"]) assert.equal(byName(bare, name).status, "warn", name);
  assert.equal(byName(await runChecks({ ...good, env: { DATABASE_URL: "x", MODEL_CALLS_ENABLED: "true" } }), "模型金鑰").status, "fail");
});

test("version comparison", () => {
  assert.ok(atLeast("v24.22.1", [24, 22, 1]));
  assert.ok(atLeast("25.0.0", [24, 22, 1]));
  assert.ok(!atLeast("24.22.0", [24, 22, 1]));
  assert.ok(!atLeast("v24.9.9", [24, 22, 1]));
});
