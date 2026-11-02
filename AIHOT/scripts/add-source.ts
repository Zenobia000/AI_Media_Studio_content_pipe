// Adds a link to the inbox by hand.
// node --env-file=.env scripts/add-source.ts <url> [--note "..."] [--program ai-today] [--formats threads,carousel] [--title "..."]
import { parseArgs } from "node:util";
import { closeDb } from "@aihot/backend/db";
import { stopBoss } from "@aihot/backend/jobs/queue";
import { addSource, InvalidUrlError } from "@aihot/inbox/manual";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { note: { type: "string" }, program: { type: "string" }, formats: { type: "string" }, title: { type: "string" } },
});
const [url] = positionals;
if (!url) {
  console.error("用法：add-source <url> [--note ...] [--program ...] [--formats threads,carousel,reel] [--title ...]");
  process.exit(2);
}
try {
  const r = await addSource(url, {
    note: values.note, program: values.program, title: values.title,
    formats: values.formats?.split(",").map((f) => f.trim()).filter(Boolean),
  });
  console.log(JSON.stringify(r));
  console.log(r.alreadyAdded ? `已經加過了，備註已附加；它在 ${r.inboxDate} 的 inbox。` : `已加入候選，會出現在 ${r.inboxDate} 的 inbox。`);
} catch (err) {
  if (err instanceof InvalidUrlError) {
    console.error(err.message);
    process.exitCode = 2;
  } else throw err;
} finally {
  await stopBoss();
  await closeDb();
}
