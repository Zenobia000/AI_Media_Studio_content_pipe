import assert from "node:assert/strict";
import { test } from "node:test";
import { planInbox, type StateRow } from "../cross-day.ts";
import type { Candidate } from "../format.ts";

const cand = (id: string): Candidate => ({
  id, title: `題目 ${id}`, tier: "A", participantCount: 3, sourceNames: ["A", "B"], firstSeen: "10:00",
  suggestedProgram: "ai-today", suggestedFormats: ["threads"],
});
const at = (day: number, hour: number) => new Date(Date.UTC(2026, 9, day, hour));
const day = (d: Date) => d.toISOString().slice(0, 10);
const row = (id: string, status: StateRow["status"], firstListedAt: Date): StateRow => ({ id, status, firstListedAt, snapshot: cand(id) });
const ids = (list: readonly Candidate[]) => list.map(c => `${c.id}${c.carried ? `:${c.carried}` : ""}`);

test("three days: an unticked candidate is carried as 昨日 and skipped after 48 hours; a ticked one stays until opened", () => {
  // Day 1: both are new.
  const day1 = planInbox([cand("a"), cand("b")], [], at(17, 10), "2026-10-17", day);
  assert.deepEqual(ids(day1.listed), ["a", "b"]);
  assert.deepEqual(day1.skip, []);

  // Day 2: the ranking no longer has them; a was ticked, b was not.
  const rows2 = [row("a", "picked", at(17, 10)), row("b", "new", at(17, 10))];
  const day2 = planInbox([], rows2, at(18, 10), "2026-10-18", day);
  assert.deepEqual(ids(day2.listed), ["a:已勾選", "b:昨日"]);
  assert.deepEqual(day2.skip, []);

  // Day 3, 49 hours after the first listing: b is over, a stays.
  const day3 = planInbox([], rows2, at(19, 11), "2026-10-19", day);
  assert.deepEqual(ids(day3.listed), ["a:已勾選"]);
  assert.deepEqual(day3.skip, ["b"]);

  // Opened as a topic, a is no longer listed; a skipped one does not come back when the ranking has it again.
  const rows4 = [row("a", "opened", at(17, 10)), row("b", "skipped", at(17, 10))];
  assert.deepEqual(planInbox([cand("b")], rows4, at(20, 10), "2026-10-20", day).listed, []);
});

test("a candidate first listed today is not marked as carried, even when it drops out of the ranking later the same day", () => {
  const rows = [row("a", "new", at(17, 8))];
  assert.deepEqual(ids(planInbox([], rows, at(17, 20), "2026-10-17", day).listed), ["a"]);
  assert.deepEqual(ids(planInbox([cand("a")], rows, at(17, 20), "2026-10-17", day).listed), ["a"]);
});
