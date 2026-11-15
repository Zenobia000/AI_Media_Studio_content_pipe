// The two pages this site adds as modules (modules/chat, modules/materials), in a browser against a synthetic api:
// what they send, what they show of each answer, and that they fit a phone in the dark theme.
import assert from "node:assert/strict";
import type { IncomingMessage } from "node:http";
import { test } from "node:test";
import { chromium, expect, type Browser } from "@playwright/test";
import { fakeApi, startWebServer, visit, type WebServer } from "./web-server.ts";

async function bodyOf(req: IncomingMessage): Promise<unknown> {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : null;
}

const lines = (...events: object[]) => events.map((e) => JSON.stringify(e)).join("\n") + "\n";
const sources = [
  { n: 1, title: "第一篇出處", url: "https://example.org/one", score: 0.81 },
  { n: 2, title: "第二篇出處", url: "https://example.org/two", score: 0.7 },
];

async function withSite(api: ReturnType<typeof fakeApi>, run: (browser: Browser, web: WebServer) => Promise<void>) {
  const browser = await chromium.launch({ channel: "chromium" });
  let web: WebServer | undefined;
  try {
    web = await startWebServer(api);
    await run(browser, web);
  } finally {
    await browser.close();
    await web?.stop();
  }
}

test("the chat shows an answer with the sources it points to, and sends the earlier turns with a follow-up", async () => {
  const asked: Array<{ messages: Array<{ role: string; content: string }> }> = [];
  const api = fakeApi(async (url, res, req) => {
    if (url.pathname !== "/api/chat") return undefined;
    asked.push((await bodyOf(req)) as (typeof asked)[number]);
    res.setHeader("Content-Type", "application/x-ndjson");
    if (asked.length === 3) return res.end(lines({ type: "error", code: "model_unavailable" }));
    res.end(lines(
      { type: "status", text: "搜尋文章" }, { type: "status", text: "整理回答" }, { type: "sources", sources },
      { type: "delta", text: "這是**重點**，" }, { type: "delta", text: "見第一篇 [1]。" }, { type: "cited", numbers: [1] }, { type: "done" },
    ));
  });
  await withSite(api, async (browser, web) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await visit(page, web.origin + "/chat");
    await page.getByLabel("輸入問題").fill("最近有什麼新模型？");
    await page.getByLabel("輸入問題").press("Enter");
    await expect(page.getByText("見第一篇 [1]。")).toBeVisible();
    await expect(page.locator("strong", { hasText: "重點" })).toBeVisible();
    await expect(page.getByRole("link", { name: "第一篇出處" })).toHaveAttribute("href", "https://example.org/one");
    await expect(page.getByRole("link", { name: "第二篇出處" })).toHaveCount(0); // the answer did not point to it
    await expect(page.getByText("已完成 2 個步驟")).toBeVisible();
    assert.deepEqual(asked[0]!.messages, [{ role: "user", content: "最近有什麼新模型？" }]);

    await page.getByLabel("輸入問題").fill("再短一點");
    await page.getByRole("button", { name: "送出" }).click();
    await expect(page.getByText("見第一篇 [1]。")).toHaveCount(2);
    assert.deepEqual(asked[1]!.messages.map((m) => m.role), ["user", "assistant", "user"]);
    assert.equal(asked[1]!.messages[1]!.content, "這是**重點**，見第一篇 [1]。");

    await page.getByLabel("輸入問題").fill("第三個問題");
    await page.getByLabel("輸入問題").press("Enter");
    await expect(page.getByText("回答用的模型現在沒有開，請稍後再試。")).toBeVisible();
  });
});

const pick = (fields: Record<string, unknown>) => ({
  id: "a1", title: "素材文章", summary: null, url: "https://example.org/a1", source: "Fixture", folder: null, state: "idle", wanted: [],
  result: {}, files: [], counts: { article: 0, photo: 0, video: 0 }, gaps: [], ...fields,
});

test("the materials page asks for the kinds that are ticked and shows how each download went", async () => {
  const fetched: Array<{ id: string; header: string | undefined; body: unknown }> = [];
  const listing = {
    tools: { ytdlp: true, galleryDl: false },
    picks: [
      pick({ id: "a1", title: "還沒下載的" }),
      pick({ id: "a2", title: "下載中的", state: "running" }),
      pick({
        id: "a3", title: "下載完的", state: "done", folder: "topics/t3", counts: { article: 1, photo: 2, video: 0 },
        files: [{ kind: "photo", path: "topics/t3/media/x.png", name: "x.png", size: 2048 }],
        gaps: [{ need: "影片", reason: "頁面沒有影片", suggestion: "自己錄一段" }],
      }),
      pick({ id: "a4", title: "被中斷的", state: "interrupted" }),
    ],
  };
  const api = fakeApi(async (url, res, req) => {
    if (url.pathname === "/api/materials" && req.method === "GET") return listing;
    const started = /^\/api\/materials\/([^/]+)\/fetch$/.exec(url.pathname);
    if (started && req.method === "POST") {
      fetched.push({ id: started[1]!, header: req.headers["x-materials-request"] as string | undefined, body: await bodyOf(req) });
      res.statusCode = started[1] === "a3" ? 409 : 202;
      return { state: "queued" };
    }
    return undefined;
  });
  await withSite(api, async (browser, web) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await visit(page, web.origin + "/materials");
    const card = (title: string) => page.getByRole("listitem").filter({ hasText: title });
    await expect(card("下載完的")).toContainText("完成 · 文章 ✓ · 照片 2");
    await expect(card("下載完的")).toContainText("缺 影片：頁面沒有影片。自己錄一段");
    await card("下載完的").getByText("檔案與紀錄").click();
    await expect(card("下載完的").getByRole("img", { name: "x.png" })).toHaveAttribute("src", "/api/materials/a3/file?path=topics%2Ft3%2Fmedia%2Fx.png");
    await expect(card("下載中的")).toContainText("下載中…");
    await expect(card("下載中的").getByRole("button", { name: "下載" })).toBeDisabled();
    await expect(card("被中斷的")).toContainText("中斷了");
    await expect(page.getByText("還沒裝 gallery-dl")).toBeVisible();

    await card("還沒下載的").getByLabel("影片").uncheck();
    await card("還沒下載的").getByRole("button", { name: "下載" }).click();
    await expect.poll(() => fetched.length).toBe(1);
    assert.deepEqual(fetched[0], { id: "a1", header: "1", body: { kinds: ["article", "photo"] } });

    await card("下載完的").getByRole("button", { name: "再下載" }).click();
    await expect(page.getByRole("status")).toHaveText("這篇已經在排隊或下載了。");
  });
});

test("both pages fit a phone in the dark theme", async () => {
  const api = fakeApi((url, res) => {
    if (url.pathname === "/api/materials") {
      return { tools: { ytdlp: false, galleryDl: false }, picks: [pick({ title: "一個標題很長很長很長很長很長很長很長很長很長很長很長很長的素材文章 with-a-very-long-unbroken-token-0123456789", state: "done", counts: { article: 1, photo: 3, video: 1 } })] };
    }
    if (url.pathname === "/api/chat") {
      res.setHeader("Content-Type", "application/x-ndjson");
      return res.end(lines({ type: "sources", sources }, { type: "delta", text: "一段回答 https://example.org/a-very-long-address-that-does-not-break-0123456789-0123456789-0123456789 [1]" }, { type: "cited", numbers: [1] }, { type: "done" }));
    }
    return undefined;
  });
  await withSite(api, async (browser, web) => {
    const context = await browser.newContext({ viewport: { width: 375, height: 740 }, colorScheme: "dark", isMobile: true, hasTouch: true });
    const page = await context.newPage();
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await visit(page, web.origin + "/materials");
    await expect(page.getByText("完成 · 文章 ✓ · 照片 3 · 影片 1")).toBeVisible();
    assert.equal(await overflow(), 0, "the materials page is wider than the phone");
    if (process.env.MODULE_SHOTS) await page.screenshot({ path: `${process.env.MODULE_SHOTS}/materials-phone-dark.png`, fullPage: true });

    await visit(page, web.origin + "/chat");
    await page.getByLabel("輸入問題").fill("問一個問題");
    await page.getByRole("button", { name: "送出" }).click();
    await expect(page.getByRole("link", { name: "第一篇出處" })).toBeVisible();
    assert.equal(await overflow(), 0, "the chat is wider than the phone");
    if (process.env.MODULE_SHOTS) await page.screenshot({ path: `${process.env.MODULE_SHOTS}/chat-phone-dark.png`, fullPage: true });
  });
});
