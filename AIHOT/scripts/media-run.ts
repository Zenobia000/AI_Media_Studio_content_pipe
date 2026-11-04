// Media helpers for the Python port's tests. argv[2] is a JSON file: { op: "ssim" | "luma" | "renditions" | "prepare", ... }.
//   ssim:       { a: number[], b: number[], width, height } → the SSIM of two luma pictures
//   luma:       { png: base64 } → the luma plane of an image
//   renditions: { answers: unknown[] } → the (url, mode) pairs its proxy addresses name
//   prepare:    { ids: string[] } → prepareArticleMedia for each article
import { readFileSync } from "node:fs";
import { closeDb } from "@aihot/backend/db";
import { prepareArticleMedia, proxiedRenditions } from "@aihot/backend/media/prepare";
import { luma, ssim } from "@aihot/backend/media/similarity";

const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
let out: unknown;
if (input.op === "ssim") {
  const plane = (v: number[]) => ({ values: Float32Array.from(v), width: input.width, height: input.height });
  out = ssim(plane(input.a), plane(input.b));
} else if (input.op === "luma") {
  const l = await luma(Buffer.from(input.png, "base64"));
  out = { width: l.width, height: l.height, values: Array.from(l.values) };
} else if (input.op === "renditions") {
  out = proxiedRenditions(input.answers);
} else {
  const results = [];
  for (const id of input.ids) results.push(await prepareArticleMedia(id));
  out = results;
  await closeDb();
}
console.log(JSON.stringify(out));
process.exit(0);
