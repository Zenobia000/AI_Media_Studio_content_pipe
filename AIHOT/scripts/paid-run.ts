// Paid-source helpers for the Python port's tests. argv[2] is a JSON file: { calls: [name, ...args][] } with collectSource (id, force, xSince), collectXShard (key, ids),
// checkMpAccount (id, reason), scheduleMpReconcile (ISO instant), followXRenames (ISO instant), extractArticleBody (id), refreshPresentation (id, options, requestId),
// previewSource (draft), and the pure planXShards / xArticleText / tweetText / tweetToCandidate.
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { rerun } from "@aihot/backend/admin/content";
import { previewSource } from "@aihot/backend/admin/sources";
import { extractArticleBody } from "@aihot/backend/content/extract";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { tweetText } from "@aihot/backend/providers/socialdata";
import { collectSource, collectXShard } from "@aihot/backend/sources/collect";
import { checkMpAccount, scheduleMpReconcile } from "@aihot/backend/sources/mp";
import { planXShards, tweetToCandidate, xArticleText } from "@aihot/backend/sources/x";
import { followXRenames } from "@aihot/backend/sources/x-renames";
import { exitAfterFlush } from "./flush.ts";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const out: unknown[] = [];
const plain = (v: unknown) => JSON.parse(JSON.stringify(v));
for (const [name, ...a] of input.calls) {
  try {
    if (name === "collectSource") out.push(await collectSource(a[0], { force: a[1], xSince: a[2] ?? undefined }));
    else if (name === "collectXShard") out.push(await collectXShard(a[0], a[1]));
    else if (name === "checkMpAccount") out.push(await checkMpAccount(a[0], a[1]));
    else if (name === "scheduleMpReconcile") out.push(await scheduleMpReconcile(new Date(a[0])));
    else if (name === "followXRenames") out.push(await followXRenames({ now: Date.parse(a[0]), pauseMs: 0 }));
    else if (name === "extractArticleBody") out.push(await extractArticleBody(a[0]));
    else if (name === "refreshPresentation") out.push(await rerun(a[0], "presentation", a[1], "tester", a[2]));
    else if (name === "previewSource") out.push(plain(await previewSource(a[0])));
    else if (name === "planXShards") out.push(planXShards(a[0]));
    else if (name === "xArticleText") out.push(xArticleText(a[0]));
    else if (name === "tweetText") out.push(tweetText(a[0]));
    else if (name === "tweetToCandidate") out.push(plain(tweetToCandidate(a[0])));
  } catch (error) {
    out.push({ error: (error as Error).message, name: (error as Error).constructor.name });
  }
}
console.log(JSON.stringify(out));
await stopBoss();
await closeDb();
await exitAfterFlush();
