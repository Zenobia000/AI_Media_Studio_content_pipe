import assert from "node:assert/strict";
import { test } from "node:test";
import { readFrontMatter, updateFrontMatter } from "../frontmatter.ts";
import { expandShortUrl, isShortUrl } from "../shorturl.ts";

const DOC = `---\nid: t-1\nstatus: new\ncustom: 保留\n---\n# 標題\n\n人寫的內文\n`;

test("an update changes the fields it names and leaves unknown fields and the body as they were", () => {
  const out = updateFrontMatter(DOC, { status: "picked", angle: "價格: 比較" });
  assert.equal(out, `---\nid: t-1\nstatus: picked\ncustom: 保留\nangle: "價格: 比較"\n---\n# 標題\n\n人寫的內文\n`);
  assert.deepEqual(readFrontMatter(out).fields, { id: "t-1", status: "picked", custom: "保留", angle: "價格: 比較" });
  assert.equal(readFrontMatter(out).body, "# 標題\n\n人寫的內文\n");
});

test("nothing to change gives the same bytes, also with CRLF and a file that ends at the fence", () => {
  assert.equal(updateFrontMatter(DOC, {}), DOC);
  const crlf = DOC.replaceAll("\n", "\r\n");
  assert.equal(updateFrontMatter(crlf, { status: "new" }), crlf);
  assert.equal(updateFrontMatter("---\na: 1\n---", { a: "2" }), "---\na: 2\n---");
});

test("a file without front-matter gets one in front and keeps its text", () => {
  assert.equal(updateFrontMatter("內文\n", { id: "x" }), "---\nid: x\n---\n內文\n");
  assert.equal(updateFrontMatter("---\nbroken\n", { id: "x" }), "---\nid: x\n---\n---\nbroken\n");
});

test("only the known shorteners are followed, to the page they lead to", async () => {
  assert.ok(isShortUrl("https://t.co/abc"));
  assert.ok(!isShortUrl("https://example.com/t.co"));
  const get = async () => ({ status: 200, url: "https://openai.com/index/x", type: "text/html", text: "" });
  assert.equal(await expandShortUrl("https://t.co/abc", get), "https://openai.com/index/x");
  assert.equal(await expandShortUrl("https://example.com/a", async () => { throw new Error("no"); }), "https://example.com/a");
  assert.equal(await expandShortUrl("https://bit.ly/z", async () => { throw new Error("down"); }), "https://bit.ly/z");
  assert.equal(await expandShortUrl("https://bit.ly/z", async () => ({ status: 404, url: "https://bit.ly/z", type: "", text: "" })), "https://bit.ly/z");
});
