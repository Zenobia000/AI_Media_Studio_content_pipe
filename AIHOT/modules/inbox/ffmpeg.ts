// ffmpeg, run the way every tool of the pipeline runs it.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { OpenTopicError } from "./topics.ts";

const run = promisify(execFile);

/** Runs ffmpeg and returns what it wrote to stderr (its report goes there); a failure says why. */
export async function ffmpeg(args: string[]): Promise<string> {
  try {
    const { stderr } = await run("ffmpeg", ["-hide_banner", "-nostdin", "-y", ...args], { maxBuffer: 64 * 1024 * 1024 });
    return stderr;
  } catch (error) {
    const e = error as NodeJS.ErrnoException & { stderr?: string };
    if (e.code === "ENOENT") throw new OpenTopicError("找不到 ffmpeg；請安裝 ffmpeg 並確認它在 PATH 裡");
    const last = (e.stderr ?? e.message).trim().split("\n").filter(Boolean).slice(-2).join(" ");
    throw new Error(`ffmpeg 失敗：${last.slice(0, 200)}`);
  }
}
