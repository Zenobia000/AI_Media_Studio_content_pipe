# Reddit 來源處理（T-061 的前置調查，桌面調查）

| | |
|---|---|
| 對應票 | [T-061](../tickets/T-061.md)、`COL-FR-007` |
| 日期 | 2026-11-02 |
| 狀態 | 只查了規則。**沒有寫 Reddit 收集器**，也沒有 PRD Q5 的主管決定 |

## 查到的（第三方文章，不是 Reddit 官方頁面，要核對 Reddit 的 Data API Wiki 與條款）

- 免費額度只給**非商業**用途：每個 OAuth 用戶端每分鐘 100 次，未登入每分鐘 10 次。
- 凡是公司使用、或放在營利產品裡的，Reddit 都算商業用途，要另簽合約。我們的產線產出會發佈到社群帳號，**很可能算商業**。
- 新的存取權要人工審核，約 2～4 週，不保證通過；自助申請據說已關閉。
- 商業價格說法不一（有 0.24 美元／千次、每年最低 12,000 美元的說法，也有人說那是 2023 年數字、現在官方頁面沒寫價格）。
- 預設條款禁止拿來訓練 AI／機器學習，除非另外同意。
- 有幾篇說公開 API 的新請求會在 2026-10-31 停止、2027 年 3 月結束，**只有少數來源這樣說，查不到官方確認**。

## 結論與建議

1. **建議採用停用路徑**：Reddit 預設關閉，討論熱度改由電子報與 Hacker News 涵蓋（這兩項已經做完）。這也符合票上「確認 config 停用時一切正常」的驗收。
2. 若主管要 Reddit，先確認是否算商業用途、並送出申請（2～4 週），拿到存取權後才寫收集器；沒有權限就寫了也不能用。
3. 收集器若將來要寫，遇到 403 要停用該收集器並只通知一次（票的驗收第 3 點）。

## 目前程式的狀況

AIHOT 的信源清單（`industry/sources.json`）裡沒有 Reddit，所以「停用時一切正常」現在就成立；沒有為此新增任何程式。

## 來源

- [Reddit API pricing（octolens）](https://octolens.com/blog/reddit-api-pricing)
- [Free Reddit API in 2026（apiserpent）](https://apiserpent.com/blog/free-reddit-api)
- [Reddit Data API 2026（dev.to）](https://dev.to/agenthustler/reddit-data-api-2026-after-the-pricing-change-heres-what-developers-actually-use-4o2h)
- [Reddit API pricing tiers（xpoz）](https://www.xpoz.ai/blog/guides/reddit-api-pricing-tiers-and-alternatives/)
