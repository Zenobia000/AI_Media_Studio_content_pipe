# Reddit Data API 申請步驟

| 項目 | 內容 |
|---|---|
| 誰做 | 主管 |
| 期限 | **2026-10-30**（10-31 起 Reddit 不再受理新的公開 API 申請） |
| 花多少時間 | 約 20 分鐘；審核時間 Reddit 沒有公布 |
| 對應 | PRD Q5、技術選型 §1.3、[T-061](../tickets/T-061.md) |
| 資料查核日 | 2026-10-23 |

---

## 1. 先知道這三件事

1. **登記 app 不等於拿到 API 權限。** 2025-11 起，Reddit 依 Responsible Builder Policy 改成**人工審核**：
   - 在 `reddit.com/prefs/apps` 建 app，只會得到一個 app 標籤，不會自動拿到可用的憑證。
   - 要另外送申請單，等 Reddit 明確核准。
2. **核准機率不高，可能不回覆。** r/redditdev 上的經驗分享（二手資料）大致是這樣：
   - 版主工具、學術研究比較容易過。
   - 個人或興趣專案常被擱置，或收到制式拒絕信。
   - 等待時間從幾週到沒有回音都有。
3. **就算核准，最多也只能用到 2027-03。** 公開 API 在 2027-03 全面關閉，有沒有登記都一樣：

   | 日期 | 事件 |
   |---|---|
   | 2026-10-31 | 停止受理新申請 |
   | 2026-11-13 | RSS 停止 |
   | 2027-01-12 | 開始關閉未登記的 app |
   | 2027-03 | 公開 API 全部關閉 |

   我們的 Reddit 收集器排在 S4（T-061），就算核准，實際能用的時間也只有幾個月。

**結論：** 試一次的成本只有 20 分鐘，值得送；但整個計畫不依賴它。沒過或沒回音，就照原計畫用電子報（[T-037](../tickets/T-037.md)）間接涵蓋 Reddit 上的討論。

---

## 2. 申請前準備

- [ ] **一個 Reddit 帳號，建議用工作室專用帳號，不要用個人帳號。** 要求如下：
  - Email 已驗證。
  - 帳號最好有一點歷史，全新帳號比較可疑。
- [ ] **這個帳號不要開兩步驟驗證（2FA）。** `script` 類型的 app 用帳號密碼換 token，開了 2FA 會失敗。這點是社群專案的說明，不是 Reddit 官方文件；如果 Reddit 審核時要求開 2FA，以 Reddit 為準。
- [ ] 先讀過兩份政策，申請內容不能違反：
  - [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
  - [Developer Platform & Accessing Reddit Data](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data)

---

## 3. 步驟

### 步驟 1：建立 script app（5 分鐘）

1. 用工作室帳號登入，打開 https://www.reddit.com/prefs/apps
2. 拉到最下面，按 **create another app...**（或 **are you a developer? create an app...**）。
3. 照下表填：

   | 欄位 | 填什麼 |
   |---|---|
   | name | `ai-media-studio-collector` |
   | 類型 | **script** |
   | description | `Read-only topic discovery for an internal content research prototype.` |
   | about url | `https://github.com/Zenobia000/AI_Media_Studio_content_pipe` |
   | redirect uri | `http://localhost:8080` |

4. 按 **create app**，然後記下兩個值：
   - **client ID**：app 名稱正下方那串約 14–22 字元的字串。
   - **secret**：標示為 `secret` 的那一行。
5. 如果頁面不讓你建立，或叫你改去讀政策，就跳過這一步，直接做步驟 2。申請單不一定需要 client ID。

### 步驟 2：送 Data API 存取申請（10 分鐘）

1. 打開 Reddit 支援申請單：https://support.reddithelp.com/hc/en-us/requests/new
2. 選 API／Data API 存取申請相關的類別。身分選 **developer**，用途選**非商業（non-commercial）**。表單選項名稱 Reddit 可能會改，選最接近的就好。
3. 把第 4 節的範本貼進說明欄，`<...>` 的地方換成實際內容。
4. 送出後，**把申請單號和送出畫面截圖存下來**，回填到第 5 節。

### 步驟 3：等待結果

- 結果會寄到帳號的 Email。
- 2026-11-10 Reddit 預告會發一次進度更新，記得看 r/redditdev。
- 2026-11-30 前沒有回音，就當作沒過。

---

## 4. 申請說明範本（英文，照實填寫）

> **寫的原則**：照實寫用途，不要誇大或隱瞞。
> - **用途**：我們只讀取貼文標題、連結、分數、留言數，拿來判斷科技新聞的熱度，不轉載 Reddit 內容，也不回寫任何東西。
> - **規模與商業性**：目前是內部 POC。如果 Reddit 追問商業用途，就如實說「內容會發在我們自己經營的社群帳號」，由 Reddit 判斷要不要走商業條款。

```
Subject: Data API access request – read-only topic discovery (internal prototype)

Hello Reddit team,

We are a small team (4 people) building an internal prototype that helps us
decide which AI/tech news topics are worth researching. We would like
read-only Data API access for one script app.

App
- Reddit account: u/<account>
- App name: ai-media-studio-collector (script)
- Client ID: <client id, if created>
- Project: https://github.com/Zenobia000/AI_Media_Studio_content_pipe

What we read
- Listings (hot/new) from about 3–10 public subreddits, e.g.
  r/LocalLLaMA, r/MachineLearning, r/singularity.
- Only post title, permalink, external URL, score, comment count, created time.
- No comment bodies, no user profiles, no private or NSFW subreddits.

What we do NOT do
- No posting, commenting, voting, messaging, or any write action.
- No redistribution or republishing of Reddit content.
- No use of Reddit data to train AI models.
- No use of deleted or removed content.

Volume and retention
- About 1 request per subreddit per hour (well under 300 requests/day).
- We keep the fields above for 90 days for deduplication, then delete them.
- Credentials are stored locally and never shared.

Status
- Internal prototype, not a commercial product. If our use later becomes
  commercial, we will contact Reddit about commercial terms first.

Thank you,
<name>
<email>
```

---

## 5. 結果紀錄（申請後回填）

| 項目 | 內容 |
|---|---|
| 送出日期 | |
| 申請單號 | |
| 使用帳號 | |
| client ID 是否已建立 | |
| 結果（核准／拒絕／無回應） | |
| 結果日期 | |

---

## 6. 核准之後

1. **存憑證**：放進 repo 根目錄的 `.env`，這個檔案已在 `.gitignore` 裡，**絕對不要 commit**。

   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USERNAME=...
   REDDIT_PASSWORD=...
   REDDIT_USER_AGENT=ai-media-studio-collector/0.1 by u/<account>
   ```

2. **交給 Albert**：通知 Albert Chiang，T-061 就實作 subreddit 收集。用 PRAW 讀取，只做唯讀操作，頻率照申請時寫的。
3. **更新文件**：把 PRD Q5 改成已結案，在 D11 補上「已取得過渡期存取，2027-03 前停用」。

## 7. 沒過或沒回音

不用做任何事：

- T-061 照「停用路徑」實作，確認關掉 Reddit 時一切正常。
- Reddit 上的討論改由電子報（T-037）間接涵蓋。
- PRD Q5 改成「已結案：未取得」。

**不要改用爬蟲或第三方 Reddit 爬取服務。** Reddit 規則明文禁止未經授權的爬取，帳號和法律風險都不值得（PRD D8）。

---

## 出處

| 資料 | 連結 |
|---|---|
| 時程（2026-09-30 公告的二手整理） | [PPC Land](https://ppc.land/reddit-to-halt-public-data-api-access-in-march-2027-as-14-000-apps-register/)、[Unite.AI](https://www.unite.ai/reddit-sets-dates-to-retire-rss-feeds-and-close-public-api-access/)、[TechRepublic](https://www.techrepublic.com/article/news-reddit-rss-public-api-shutdown/)、[WinBuzzer](https://winbuzzer.com/2026/10/01/reddit-to-end-feed-reader-updates-and-public-api-access-for-apps-a002-xcxwbn/) |
| 申請要審核、表單位置、script app 與 2FA | [read-it 專案說明](https://glama.ai/mcp/servers/gavinmcfall/read-it)、[redditapis.com 整理](https://www.redditapis.com/blogs/reddit-data-api-2026)（第三方商業網站，僅供參考） |
| 官方政策 | [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy) |

以上時程與流程大多來自二手報導；Reddit 的原始公告在 r/redditdev 的〈Moving Data API apps to the Developer Platform〉與 r/modnews 的〈Continuing our infrastructure updates〉。申請表單的實際欄位，以送出當下看到的畫面為準。
