import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { renderHealth, withHealth } from "../format.ts";
import { writeInbox } from "../server.ts";

let dir: string;
before(async () => {
  dir = await mkdtemp(path.join(tmpdir(), "inbox-health-"));
  process.env.INBOX_DIR = dir;
});
after(async () => {
  delete process.env.INBOX_DIR;
  await rm(dir, { recursive: true, force: true });
  await closeDb();
});

const inboxText = async () => readFile(path.join(dir, (await readdir(dir)).sort().at(-1)!), "utf8");

test("the collection-health block is added at the foot once and replaced after that", () => {
  const base = "# 2026-11-02 候選\n\n<!-- newsroom:begin candidates -->\n<!-- newsroom:end candidates -->\n";
  const failing = [{ name: "Broken feed", failCount: 4, lastError: "HTTP 404", lastOkAt: "2026-11-01 08:00" }];
  const once = withHealth(base, failing);
  assert.match(once, /\*\*Broken feed\*\*：連續失敗 4 次｜最後成功：2026-11-01 08:00｜HTTP 404/);
  assert.ok(once.indexOf("newsroom:end candidates") < once.indexOf("newsroom:begin collect-health"));
  const healed = withHealth(once, []);
  assert.equal(healed.match(/newsroom:begin collect-health/g)!.length, 1);
  assert.match(healed, /所有來源最近一次收集都正常/);
  assert.doesNotMatch(healed, /Broken feed/);
  assert.match(renderHealth([{ name: "X", failCount: 3, lastError: null, lastOkAt: null }]), /從未成功/);
});

test("a source that failed three collections in a row is listed in the inbox, a source that failed twice is not", async () => {
  const bad = `Bad feed ${tag()}`;
  const flaky = `Flaky feed ${tag()}`;
  await sql`INSERT INTO sources (id, name, kind, next_fetch_at, fail_count, last_error, health) VALUES
    (${`test-bad-${tag()}`}, ${bad}, 'rss', '2100-01-01', 3, 'HTTP 404', 'degraded'),
    (${`test-flaky-${tag()}`}, ${flaky}, 'rss', '2100-01-01', 2, 'timeout', 'degraded')`;
  assert.ok(await writeInbox(), "the inbox is written even with nothing else to list");
  const text = await inboxText();
  assert.match(text, new RegExp(`\\*\\*${bad}\\*\\*：連續失敗 3 次`));
  assert.doesNotMatch(text, new RegExp(flaky));

  await sql`UPDATE sources SET fail_count = 0 WHERE name = ${bad}`;
  await writeInbox();
  const healed = await inboxText();
  assert.doesNotMatch(healed, new RegExp(bad));
  assert.match(healed, /所有來源最近一次收集都正常/);
});
