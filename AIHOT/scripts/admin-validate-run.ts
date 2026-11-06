// Feeds invalid source bodies to the admin's create and update validation and prints the messages the API would answer with.
import { ZodError } from "zod";
import { createSource, updateSource } from "@aihot/backend/admin/sources";
import { exitAfterFlush } from "./flush.ts";

const message = (error: unknown) => error instanceof ZodError
  ? error.issues.map((issue) => `${issue.path.join(".") || "請求"}: ${issue.message}`).join("; ").slice(0, 300)
  : `non-validation: ${(error as Error).message}`.slice(0, 120);
const ok = { id: "abc-def", name: "N", kind: "rss", config: { feedUrl: "https://e.org/f" } };
const creates: unknown[] = [
  null, "x", [], {}, { ...ok, id: "A" }, { ...ok, id: "ab" }, { ...ok, id: 5 }, { ...ok, name: "" }, { ...ok, name: "x".repeat(201) }, { ...ok, kind: "nope" }, { ...ok, config: "x" }, { ...ok, config: [] },
  { ...ok, tier: "T9" }, { ...ok, selection_class: "nope" }, { ...ok, selection_class: 5 }, { ...ok, participation_mode: "x" }, { ...ok, interval_minutes: 0 }, { ...ok, interval_minutes: 1441 },
  { ...ok, interval_minutes: 1.5 }, { ...ok, interval_minutes: "5" }, { ...ok, first_party: "yes" }, { ...ok, tags: "a" }, { ...ok, tags: [1] }, { ...ok, tags: Array(31).fill("a") },
  { ...ok, uses: ["nope"] }, { ...ok, uses: "x" }, { ...ok, uses: Array(11).fill("a") }, { ...ok, site_fulltext: 1 }, { ...ok, extra: 1 }, { ...ok, a: 1, b: 2 }, { id: 1, name: 2, kind: 3, config: 4 },
  { ...ok, tags: ["x".repeat(61)] },
];
const patches: unknown[] = [
  null, "x", [], { name: "" }, { name: 5 }, { kind: "x_search" }, { kind: "nope" }, { enabled: "yes" }, { interval_minutes: 0 }, { interval_minutes: 2000 }, { tier: "T9" }, { selection_class: "nope" },
  { participation_mode: "x" }, { signal_group_id: 5 }, { signal_group_id: "x".repeat(121) }, { first_party: 1 }, { owner_entity_id: 5 }, { site_fulltext: "x" }, { syndicate_fulltext: "x" },
  { tags: [1] }, { uses: ["nope"] }, { config: "x" }, { config: [] }, { bogus: 1 }, { name: "", bogus: 1, tier: 5 },
];
const out = {
  creates: await Promise.all(creates.map(async (c) => { try { await createSource(c, "t"); return "passed"; } catch (e) { return message(e); } })),
  patches: await Promise.all(patches.map(async (p) => { try { await updateSource("no-such-source-xyz", { patch: p, version: "v" }, "t"); return "passed"; } catch (e) { return message(e); } })),
  createInputs: creates, patchInputs: patches,
};
console.log(JSON.stringify(out));
await exitAfterFlush();
