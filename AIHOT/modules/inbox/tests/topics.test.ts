import { tag } from "../../../tests/setup.ts";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { closeDb, sql } from "@aihot/backend/db";
import { addSource } from "../manual.ts";
import { mergeCandidates, openTopic, OpenTopicError, slugOf, sourceIdOf } from "../topics.ts";
import { writeInbox } from "../server.ts";

let home: string;
before(async () => {
  home = await mkdtemp(path.join(tmpdir(), "newsroom-"));
  process.env.NEWSROOM_HOME = home;
});
after(async () => {
  delete process.env.NEWSROOM_HOME;
  await rm(home, { recursive: true, force: true });
  await closeDb();
});

/** A hand-added link that the inbox has listed, so it is a candidate. */
async function candidate(note = "") {
  const added = await addSource(`https://example.com/topic-${tag()}`, { note, title: `Roadster 二代發表 ${tag()}`, queue: false });
  await writeInbox();
  return { id: `cand_${added.articleId}`, url: added.url };
}
const topicsOnDisk = async () => (await readdir(path.join(home, "topics")).catch(() => [])).sort();
const contentsOf = async (topicId: string) => (await readdir(path.join(home, "topics", topicId, "contents"))).sort();

test("opening the same candidate twice leaves one topic and one content; a format added later adds only that one", async () => {
  const c = await candidate("從工具角度切入");
  const first = await openTopic({ candidateIds: [c.id], program: "ai-today", formats: ["threads"] });
  assert.equal(first.created, true);
  assert.deepEqual(first.contentsCreated, ["threads-01"]);
  assert.match(first.topicId, /^\d{4}-\d{2}-\d{2}-roadster/);

  const again = await openTopic({ candidateIds: [c.id], program: "ai-today", formats: ["threads"] });
  assert.equal(again.created, false);
  assert.equal(again.topicId, first.topicId);
  assert.deepEqual(again.contentsCreated, []);
  assert.deepEqual(again.contentsExisting, ["threads-01"]);

  const more = await openTopic({ candidateIds: [c.id], formats: ["threads", "reel"] });
  assert.deepEqual(more.contentsCreated, ["reel-01"]);
  assert.deepEqual(await contentsOf(first.topicId), ["reel-01", "threads-01"]);
  const [count] = await sql<{ n: number }[]>`SELECT count(*)::int AS n FROM topics WHERE topic_id = ${first.topicId}`;
  assert.equal(count!.n, 1);

  const topicMd = await readFile(path.join(home, "topics", first.topicId, "topic.md"), "utf8");
  assert.match(topicMd, new RegExp(`id: ${first.topicId}`));
  assert.match(topicMd, new RegExp(`candidate_ids: \\[${c.id}\\]`));
  assert.match(topicMd, new RegExp(`source_ids: \\[${sourceIdOf(c.url)}\\]`));
  assert.match(topicMd, /<!-- newsroom:begin status -->/);
  assert.match(await readFile(path.join(home, "topics", first.topicId, "notes.md"), "utf8"), /從工具角度切入/);
  const contentMd = await readFile(path.join(home, "topics", first.topicId, "contents", "threads-01", "content.md"), "utf8");
  assert.match(contentMd, /format: threads/);
  assert.match(contentMd, /angle: "從工具角度切入"/);
  const [state] = await sql<{ status: string }[]>`SELECT status FROM candidate_state WHERE candidate_id = ${c.id}`;
  assert.equal(state!.status, "opened");
});

test("two merged candidates give one topic whose sources are both of theirs", async () => {
  const a = await candidate();
  const b = await candidate();
  assert.deepEqual(await mergeCandidates(a.id, b.id), { topicId: null });
  const opened = await openTopic({ candidateIds: [a.id], program: "tool-review", formats: ["carousel"] });
  assert.deepEqual(opened.candidateIds.sort(), [a.id, b.id].sort());
  assert.deepEqual(opened.sourceIds.sort(), [sourceIdOf(a.url), sourceIdOf(b.url)].sort());
  const [row] = await sql<{ n: number }[]>`SELECT count(DISTINCT topic_id)::int AS n FROM topic_candidates WHERE candidate_id IN (${a.id}, ${b.id})`;
  assert.equal(row!.n, 1);
});

test("a note saying 合併到 another candidate in the inbox merges them too", async () => {
  const a = await candidate();
  const b = await candidate();
  const day = (await readdir(path.join(home, "inbox"))).sort().at(-1)!;
  const file = path.join(home, "inbox", day);
  const text = await readFile(file, "utf8");
  const at = text.indexOf(`\`${b.id}\``);
  const noteLine = text.indexOf("  - 備註：", at);
  await writeFile(file, `${text.slice(0, noteLine)}  - 備註：合併到 ${a.id}${text.slice(text.indexOf("\n", noteLine))}`, "utf8");
  const opened = await openTopic({ candidateIds: [b.id], formats: ["threads"] });
  assert.deepEqual(opened.candidateIds.sort(), [a.id, b.id].sort());
  assert.deepEqual(opened.warnings, []);
});

test("an unknown candidate, a program or a format that does not exist, and no candidate at all are refused", async () => {
  const before = await topicsOnDisk();
  await assert.rejects(openTopic({ candidateIds: ["cand_doesnotexist"] }), OpenTopicError);
  const c = await candidate();
  await assert.rejects(openTopic({ candidateIds: [c.id], program: "news" }), OpenTopicError);
  await assert.rejects(openTopic({ candidateIds: [c.id], formats: ["podcast"] }), OpenTopicError);
  await assert.rejects(openTopic({ candidateIds: [] }), OpenTopicError);
  assert.deepEqual(await topicsOnDisk(), before);
});

test("slugs are ASCII, lower case and at most 40 characters; the same address always has the same source id", () => {
  assert.equal(slugOf("Tesla Roadster 二代發表!"), "tesla-roadster");
  assert.equal(slugOf("全中文標題"), "topic");
  assert.ok(slugOf("a".repeat(100)).length <= 40);
  assert.equal(sourceIdOf("https://example.com/a?utm_source=x"), sourceIdOf("https://www.example.com/a#top"));
  assert.match(sourceIdOf("https://example.com/a"), /^src_[0-9a-f]{12}$/);
});
