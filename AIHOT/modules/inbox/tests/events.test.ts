import assert from "node:assert/strict";
import { test } from "node:test";
import { justEnded, mentionsEndedEvent, parseEvents, startingTomorrow } from "../events.ts";

const TABLE = `# 發表會行事曆

| 日期 | 時間 | 活動 | 關鍵字 | 官方連結 |
|---|---|---|---|---|
| 2026-11-05 | 10:00-12:30 | Acme DevDay | Acme, 新模型 | https://acme.example/devday |
| 2026-11-06 | 02:00 | Foo Launch | foo | |
| 壞日期 | 10:00 | 壞掉的 | x | |
| | | | | |
`;

test("the table gives events in Taipei time, and says which rows it skipped", () => {
  const { events, problems } = parseEvents(TABLE);
  assert.equal(events.length, 2);
  assert.equal(events[0]!.startsAt.toISOString(), "2026-11-05T02:00:00.000Z");
  assert.equal(events[0]!.endsAt.toISOString(), "2026-11-05T04:30:00.000Z");
  assert.deepEqual(events[0]!.keywords, ["Acme", "新模型"]);
  assert.equal(events[1]!.endsAt.getTime() - events[1]!.startsAt.getTime(), 2 * 3600_000);
  assert.equal(events[1]!.url, null);
  assert.equal(problems.length, 1);
  assert.equal(parseEvents(TABLE).events[0]!.id, events[0]!.id);
});

test("a story naming a keyword of an event that ended in the last day is about it; before or long after it is not", () => {
  const { events } = parseEvents(TABLE);
  const at = (iso: string) => new Date(iso);
  const after = justEnded(events, at("2026-11-05T06:00:00Z"));
  assert.equal(after.length, 1);
  assert.ok(mentionsEndedEvent("ACME unveils its model", after));
  assert.ok(!mentionsEndedEvent("Other news", after));
  assert.equal(justEnded(events, at("2026-11-05T03:00:00Z")).length, 0);
  assert.equal(justEnded(events, at("2026-11-07T06:00:00Z")).length, 0);
});

test("the day before, an event is due for preparation", () => {
  const { events } = parseEvents(TABLE);
  assert.deepEqual(startingTomorrow(events, new Date("2026-11-04T09:00:00+08:00")).map(e => e.title), ["Acme DevDay"]);
  assert.equal(startingTomorrow(events, new Date("2026-11-03T09:00:00+08:00")).length, 0);
});
