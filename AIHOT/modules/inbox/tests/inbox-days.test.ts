import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { writeInbox } from "../server.ts";

after(() => closeDb());

// Taipei is UTC+8: these instants are 10:00 on the 17th, 18th and 19th.
const at = (day: number, hour = 2) => new Date(Date.UTC(2026, 9, day, hour));

test("over three days a ticked link and its note stay, an unticked one is carried once and then dropped", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "inbox-days-"));
  process.env.INBOX_DIR = dir;
  const read = (day: number) => readFile(path.join(dir, `2026-10-${day}.md`), "utf8");
  try {
    const kept = await addSource(`https://example.com/kept-${tag()}`, { note: "要做", queue: false });
    const dropped = await addSource(`https://example.com/dropped-${tag()}`, { note: "不做", queue: false });
    // Both were added now; pretend the first inbox of the 17th is when they were first listed.
    await sql`UPDATE manual_candidates SET added_at = ${at(17)}`;

    await writeInbox(at(17));
    const day1 = await read(17);
    assert.match(day1, new RegExp(`\`cand_${kept.articleId}\``));
    await writeFile(path.join(dir, "2026-10-17.md"), day1.replace(new RegExp(`- \\[ \\] (\\*\\*[^\\n]*cand_${kept.articleId})`), "- [x] $1"), "utf8");

    // The 18th: the ticked one is carried with its tick and note, the other is carried as 昨日.
    await writeInbox(at(18));
    const day2 = await read(18);
    assert.match(day2, new RegExp(`- \\[x\\] .*\`cand_${kept.articleId}\`\\n.*已勾選(?:\\n  - [^\\n]*)*\\n  - 備註：要做`));
    assert.match(day2, new RegExp(`- \\[ \\] .*\`cand_${dropped.articleId}\`\\n.*昨日`));

    // The 19th, more than 48 hours after the first listing: the unticked one is gone, the ticked one stays.
    await writeInbox(at(19, 4));
    const day3 = await read(19);
    assert.match(day3, new RegExp(`cand_${kept.articleId}`));
    assert.doesNotMatch(day3, new RegExp(`cand_${dropped.articleId}`));
    const [state] = await sql<{ status: string }[]>`SELECT status FROM candidate_state WHERE candidate_id = ${`cand_${dropped.articleId}`}`;
    assert.equal(state!.status, "skipped");
  } finally {
    delete process.env.INBOX_DIR;
    await rm(dir, { recursive: true, force: true });
  }
});
