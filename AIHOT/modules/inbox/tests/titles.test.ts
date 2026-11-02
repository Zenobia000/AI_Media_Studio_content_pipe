import assert from "node:assert/strict";
import { test } from "node:test";
import { needsTranslation } from "../titles.ts";

test("only a title without Chinese is waiting for a translation", () => {
  assert.ok(needsTranslation("OpenAI releases a new model"));
  assert.ok(!needsTranslation("OpenAI 發表新模型"));
});
