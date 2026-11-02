// Gives a candidate its Chinese title, or lists the candidates that still need one (COL-FR-012).
// node --env-file=.env scripts/set-title-zh.ts --list | <cand_id> "<中文標題>" [--force]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { EmptyTitleError, pendingTitles, setTitleZh } from "@aihot/inbox/titles";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { list: { type: "boolean" }, force: { type: "boolean" } } });
try {
  if (values.list) {
    console.log(JSON.stringify(await pendingTitles()));
  } else {
    const [id, title] = positionals;
    if (!id || title === undefined) {
      console.error("用法：set-title-zh --list，或 set-title-zh <cand_id> <中文標題> [--force]");
      process.exitCode = 2;
    } else {
      console.log((await setTitleZh(id, title, values.force)) ? "已寫入中文標題。" : "這則已經有中文標題了（要改請加 --force）。");
    }
  }
} catch (err) {
  if (err instanceof EmptyTitleError) {
    console.error(err.message);
    process.exitCode = 2;
  } else throw err;
} finally {
  await closeDb();
}
