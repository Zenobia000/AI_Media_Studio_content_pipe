# SPK-COL-1｜X API 成本（桌面調查，未實測）

| | |
|---|---|
| 對應票 | [T-059](../tickets/T-059.md) |
| 做的人 | Albert Chiang（內容由 Claude 協助整理） |
| 日期 | 2026-11-02 |
| 狀態 | **只做了查資料和估算。票上要求的「150 人 List 跑 7 天、對照實際帳單」沒有做**，因為沒有付費金鑰 |

## 查到的（都是第三方部落格，不是 X 官方頁面，下單前要到 Developer Console 核對）

- X 現在以「用多少付多少」計費；舊的固定月費方案已轉成這種方式，免費方案 2026-02-06 起不再收新專案。
- 讀一則貼文約 **0.005 美元**；讀使用者資料約 0.010 美元；發文 0.01～0.015 美元，含網址的貼文約 0.20 美元。
- 按「回傳的則數」收費，所以輪詢的次數本身不花錢，**花錢的是拿回來的新貼文數**（用 `since_id` 只拿新的）。
- 每月讀取上限有 200 萬與 300 萬兩種說法，互相衝突。
- **找不到** List 端點的單獨價格。先假設照貼文讀取價算。

## 估算（假設，不是數據）

月費 ≈ 追蹤人數 × 每人每天則數 × 30 × 0.005 美元。每人每天幾則是我猜的，要實測才準。

| 追蹤人數 | 每人每天 3 則 | 每人每天 5 則 | 每人每天 10 則 |
|---|---|---|---|
| 50 | 22.5 美元 | 37.5 美元 | 75 美元 |
| 150 | 67.5 美元 | 112.5 美元 | 225 美元 |

票上的原估是每月 90～225 美元，和 150 人每天 4～10 則的範圍一致。**這張表沒有信賴區間**，因為沒有實測數據。

## 建議

- 月費若超過 60 美元（票的門檻），照票上寫的縮減：核心 50 人 ＋ AINews（電子報）。50 人在每天 5 則時約 37.5 美元，落在門檻內。
- 轉推與回覆很多，若 List 端點不能 `exclude=replies,retweets`，實際則數會比估的高；這一項要實測。
- 主管決定 PRD Q2 前，至少要跑一個 3 天、50 人的小實驗，量真實的每日則數。

## 還沒做（票上要求的）

- [ ] 150 人 List 跑 7 天，記每日則數與實際帳單
- [ ] 測 `exclude=replies,retweets` 能不能用在 List 端點
- [ ] 測書籤讀取與影片檔案的各種畫質版本（media variants）

## 來源

- [X API pricing 2026（opentweet）](https://opentweet.io/x-api-pricing)
- [X API pricing 2026（posteverywhere）](https://posteverywhere.ai/blog/x-twitter-api-pricing)
- [Best X API providers 2026（apiserpent）](https://apiserpent.com/blog/best-x-twitter-api-providers-2026)
- [X API pricing 2026（website-apn）](https://website-apn.pages.dev/blog/x-api-pricing-2026/)
