// Normalises the media of a topic for cutting (REEL-FR-003, SUB-FR-001): video and GIF to 30 fps CFR yuv420p H.264,
// recordings to 48 kHz WAV at -14 LUFS with the silence taken off both ends. Output goes to media/proc/.
// Output follows IF-020 with --json; exit 2 on bad input, 3 (E_MISSING_ASSET) with --require-audio and no recording.
// node --env-file=.env scripts/preprocess-topic.ts <topic_id> [--video-only | --audio-only] [--lufs -14] [--require-audio] [--json]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { inventory } from "@aihot/inbox/assets";
import { preprocess } from "@aihot/inbox/preprocess";
import { OpenTopicError } from "@aihot/inbox/topics";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { json: { type: "boolean" }, "video-only": { type: "boolean" }, "audio-only": { type: "boolean" }, lufs: { type: "string" }, "require-audio": { type: "boolean" } },
});
try {
  if (positionals.length !== 1) throw new OpenTopicError("用法：preprocess-topic <topic_id> [--video-only | --audio-only] [--lufs -14] [--require-audio] [--json]");
  if (values["video-only"] && values["audio-only"]) throw new OpenTopicError("--video-only 與 --audio-only 不能同時用");
  const lufs = values.lufs === undefined ? undefined : Number(values.lufs);
  if (lufs !== undefined && !(lufs < 0 && lufs >= -40)) throw new OpenTopicError(`--lufs 要在 -40 到 0 之間：${values.lufs}`);
  const topicId = positionals[0]!;
  const before = await inventory(topicId);
  if (values["require-audio"] && !before.assets.some(a => a.type === "audio" && a.path.startsWith("media/raw/"))) {
    const message = "找不到旁白錄音。請錄製旁白並放進 media/raw/";
    if (values.json) console.log(JSON.stringify({ ok: false, tool: "preprocess", error_code: "E_MISSING_ASSET", message, blocked: true, next: ["把錄音放進 media/raw/，再執行 preprocess"] }));
    else console.error(message);
    process.exitCode = 3;
  } else {
    const r = await preprocess(topicId, { video: !values["audio-only"], audio: !values["video-only"], lufs });
    await inventory(topicId);
    if (values.json) console.log(JSON.stringify({ ok: true, tool: "preprocess", data: r, warnings: r.failed.map(f => `${f.from}：${f.reason}`), next: [`assets ${topicId}`] }));
    else {
      for (const m of r.made) console.log(`${m.kind === "video" ? "影片" : "旁白"}  ${m.from} → ${m.path}`);
      if (r.kept.length) console.log(`已處理過、沒有重做：${r.kept.length} 個`);
      for (const f of r.failed) console.log(`失敗 ${f.from}：${f.reason}`);
      if (!r.made.length && !r.kept.length && !r.failed.length) console.log("沒有需要處理的素材。");
    }
    if (r.failed.length && !r.made.length && !r.kept.length) process.exitCode = 1;
  }
} catch (err) {
  if (!(err instanceof OpenTopicError)) throw err;
  if (values.json) console.log(JSON.stringify({ ok: false, tool: "preprocess", error_code: "E_INPUT", message: err.message, blocked: false }));
  else console.error(err.message);
  process.exitCode = 2;
} finally {
  await closeDb();
}
