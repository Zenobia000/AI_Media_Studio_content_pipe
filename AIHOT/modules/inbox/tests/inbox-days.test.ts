import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { writeInbox } from "../server.ts";
import { setTitleZh } from "../titles.ts";

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

test("taking a tick away puts the candidate back, so it is not kept past its 48 hours; a Chinese title shows on a carried one", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "inbox-untick-"));
  process.env.INBOX_DIR = dir;
  const file = (day: number) => path.join(dir, `2026-10-${day}.md`);
  try {
    const item = await addSource(`https://example.com/untick-${tag()}`, { queue: false });
    const id = `cand_${item.articleId}`;
    await sql`UPDATE manual_candidates SET added_at = ${at(10)} WHERE article_id = ${item.articleId}`;
    await writeInbox(at(10));
    await writeFile(file(10), (await readFile(file(10), "utf8")).replace(new RegExp(`- \\[ \\] (\\*\\*[^\\n]*${id})`), "- [x] $1"), "utf8");
    await writeInbox(at(10, 3));
    assert.equal((await sql<{ status: string }[]>`SELECT status FROM candidate_state WHERE candidate_id = ${id}`)[0]!.status, "picked");

    await writeFile(file(10), (await readFile(file(10), "utf8")).replace(new RegExp(`- \\[x\\] (\\*\\*[^\\n]*${id})`), "- [ ] $1"), "utf8");
    await setTitleZh(id, "中文標題測試");
    await writeInbox(at(11));
    assert.equal((await sql<{ status: string }[]>`SELECT status FROM candidate_state WHERE candidate_id = ${id}`)[0]!.status, "new");
    assert.match(await readFile(file(11), "utf8"), /中文標題測試/);
    assert.equal(await setTitleZh(id, "換一個"), false);
  } finally {
    delete process.env.INBOX_DIR;
    await rm(dir, { recursive: true, force: true });
  }
});

test("the day before a launch event the inbox has a preparation candidate, and a story naming its keyword after it ends is S", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "inbox-events-"));
  process.env.INBOX_DIR = dir;
  process.env.NEWSROOM_HOME = dir;
  try {
    await mkdir(path.join(dir, "calendar"), { recursive: true });
    await writeFile(path.join(dir, "calendar", "events.md"), "| 日期 | 時間 | 活動 | 關鍵字 | 官方連結 |\n|---|---|---|---|---|\n| 2026-10-21 | 10:00 | Zeta DevDay | zetaword | https://zeta.example/ |\n", "utf8");
    const news = await addSource(`https://example.com/zeta-${tag()}`, { title: "ZetaWord unveils something", queue: false });
    await sql`UPDATE manual_candidates SET added_at = ${at(21, 6)} WHERE article_id = ${news.articleId}`;

    await writeInbox(at(20));
    const before = await readFile(path.join(dir, "2026-10-20.md"), "utf8");
    assert.match(before, /準備：Zeta DevDay/);

    await writeInbox(at(21, 6));
    const after = await readFile(path.join(dir, "2026-10-21.md"), "utf8");
    assert.match(after, new RegExp(`ZetaWord unveils something\\*\\* \`cand_${news.articleId}\`\\n[^\\n]*\\n  - 熱度：[^\\n]*預告過的發表會剛結束\\n  - 建議：ai-today｜threads, carousel, reel`));
  } finally {
    delete process.env.INBOX_DIR;
    delete process.env.NEWSROOM_HOME;
    await rm(dir, { recursive: true, force: true });
  }
});
