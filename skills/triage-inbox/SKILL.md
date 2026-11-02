---
name: triage-inbox
description: 整理今天的 inbox：替英文標題補中文標題、說明哪些題值得做與建議的節目格式。使用者說「幫我整理 inbox」「今天有什麼值得做」「標題翻成中文」時使用。
owner: Albert Chiang
inputs: [IF-001]
outputs: []
tools: [set-title-zh]
gates: []
---

# triage-inbox

讀今天的 inbox，替還是外文的候選補上中文標題，再告訴人哪幾題值得先看。這個 skill 只翻標題和提建議，不勾選、不建題。

## 工具

| 工具 | 指令 |
|---|---|
| `set-title-zh` | `node AIHOT/scripts/set-title-zh.ts --list`（列出還沒中文標題的候選，輸出 JSON） |
| | `node AIHOT/scripts/set-title-zh.ts <cand_id> "<中文標題>" [--force]` |

## 步驟

1. 讀今天的 `inbox/YYYY-MM-DD.md`。
2. `set-title-zh --list` 取得待翻譯的候選。
3. 逐則翻成自然的繁體中文標題：產品名、模型名、公司名保留原文，依 `glossary.md` 的譯法；不加原文沒有的內容。每則呼叫一次 `set-title-zh <cand_id> "<標題>"`。
4. 下次 inbox 重新產生（每 30 分鐘）時標題就會換成中文。
5. 向人報告：S 級與 A 級各幾題，挑最多三題說明為什麼值得做（引用 inbox 的「熱度」與「原因」欄），並指出建議的節目與格式。

## 判斷標準

- 已經有中文標題的不重翻（工具會拒絕，除非人要求改並加 `--force`）。
- 沒把握的專有名詞留原文，不要硬譯。
- 推薦理由只能引用 inbox 上看得到的資料，不要自己補數字。

## 失敗時

- 翻譯失敗或工具出錯：不影響 inbox，該則繼續顯示原文；照實告訴人哪幾則沒翻。
- 結束碼 `2`：標題是空的或參數不對，修正後再試。

## 不要做

- 不要替人勾選候選、不要建題（那是 `open-topic`）。
- 不要直接改 inbox 檔案裡的標題，下次產生會被蓋掉；一律用工具。
