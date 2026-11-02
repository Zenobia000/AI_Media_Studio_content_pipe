import assert from "node:assert/strict";
import { test } from "node:test";
import { ManagedBlockError, parseAnnotations, renderFile, tierOf, type Candidate } from "../format.ts";

const cand = (id: string, n: number): Candidate => ({
  id, title: `題目 ${id}`, tier: tierOf(n), participantCount: n, sourceNames: ["A", "B"], firstSeen: "01:42",
  suggestedProgram: "ai-today", suggestedFormats: ["threads"],
});

test("regenerating keeps ticks and notes and adds new candidates", () => {
  const first = renderFile("2026-10-17", [cand("cand_aaa", 5)], null);
  const edited = first.replace("- [ ] **題目 cand_aaa**", "- [x] **題目 cand_aaa**").replace("備註：", "備註：從工具角度切入");
  const second = renderFile("2026-10-17", [cand("cand_aaa", 5), cand("cand_bbb", 2)], edited);
  const notes = parseAnnotations(second);
  assert.equal(notes.get("cand_aaa")?.checked, true);
  assert.equal(notes.get("cand_aaa")?.note, "從工具角度切入");
  assert.equal(notes.get("cand_bbb")?.checked, false);
});

test("a damaged managed block is not overwritten", () => {
  assert.throws(() => renderFile("2026-10-17", [cand("cand_aaa", 5)], "# 2026-10-17 候選\n"), ManagedBlockError);
});

test("hand-added candidates come first under their own heading with the person's note filled in", () => {
  const manual: Candidate = { ...cand("cand_man", 1), manual: { note: "從工具角度切入\n再補一句", program: "tool-review", formats: "threads, reel" } };
  const text = renderFile("2026-10-17", [cand("cand_aaa", 5), manual], null);
  assert.ok(text.indexOf("## 手動新增") < text.indexOf("## S 級"));
  assert.match(text, /節目：tool-review/);
  assert.match(text, /格式：threads, reel/);
  assert.match(text, /備註：從工具角度切入；再補一句/);
});
