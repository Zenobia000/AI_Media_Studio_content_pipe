---
name: open-topic
description: 依人在 inbox 勾選或口頭指定的候選建立題目（topic）與各格式的稿件（content）。使用者說「做第 3 題」「把勾選的建題」「這題出 Threads 和 Reel」「這兩題合成一題」時使用。
owner: Albert Chiang
inputs: [IF-001]
outputs: [IF-002, IF-003]
tools: [open-topic, merge]
gates: []
---

# open-topic

把人選好的候選變成可以往下做的題目：一個 topic 資料夾，每個要做的格式各一個 content。這個 skill 只決定「做哪些候選、什麼節目、什麼格式、什麼角度」，建檔與去重都由工具負責。

## 什麼時候用

- 人勾選了 inbox 的候選，說「把勾選的開成題」。
- 人用口語指定：「第 3 題做 Threads」「Roadster 那題出輪播和 Reel」。
- 人說兩題其實是同一題：「這兩個合成一題」。

## 工具

目前的實作是 AIHOT 資料夾裡的腳本（`newsroom` 殼完成後改成同名指令，參數不變）：

| 工具 | 指令 |
|---|---|
| `open-topic` | `newsroom open-topic <cand_id>... [--program] [--formats a,b] [--angle "..."] --json` |
| `merge` | `newsroom merge-candidates <保留的 cand_id> <併進去的 cand_id> --json` |

環境變數 `NEWSROOM_HOME` 指向工作區；`--json` 的輸出依 IF-020，結束碼 `2` 代表輸入有誤。

## 步驟

1. 讀今天的 `inbox/YYYY-MM-DD.md`，必要時也讀前一天的。
2. 決定候選：
   - 人勾選的（`- [x]`）就是候選，不要自己加選。
   - 人說「第 N 題」時，照檔案裡**由上到下**的順序數（含「手動新增」區），N 對到的那一行最後的 `` `cand_…` `` 就是編號。數不準時列出前幾題請人確認，不要猜。
3. 決定節目與格式：
   - 人這次明確說的優先；
   - 其次是 inbox 裡人填的「節目」「格式」；
   - 都沒有就不帶參數，工具會採用「建議」欄的值。
   - 節目只能是 `ai-today`、`tool-review`、`ai-engineering`；格式只能是 `threads`、`carousel`、`reel`。
4. 決定角度：人說了就用 `--angle`（一句話）；沒說就不帶，工具會取 inbox 的備註。
5. 兩題要合成一題時，先 `merge`，再對保留的那個 `open-topic`；備註裡寫了「合併到 cand_xxx」的，工具會自動併入，不需要另外處理。
6. 呼叫 `open-topic ... --json`，把結果整理給人：topic 編號、新增了哪些 content、哪些本來就有。
7. 有 `warnings` 時照實轉述。

## 判斷標準

- 同一個候選再叫一次是安全的：topic 與 content 都不會重複，只會補上新加的格式。人說「再加一個 Reel」就是帶完整格式清單再叫一次。
- 一個候選對應一個 topic；候選已經在別的 topic 時，工具會拒絕並說明。

## 失敗時

- `E_INPUT`（結束碼 2）：照 `message` 告訴人哪裡不對（找不到候選、節目或格式不存在、候選已被略過）。找不到候選時，先確認 inbox 是否已產生過，不要自己編一個編號再試。
- 候選已超過 48 小時被略過：告訴人，問他要不要用 `add-source` 把網址重新加進來。

## 不要做

- 不要替人勾選候選，也不要在人沒說的情況下多開題。
- 不要直接改 `topics/` 底下的檔案來建題；一律透過工具。
- 不要在這個 skill 裡補料或寫稿，那是 `gather-assets` 與 `draft-content` 的事。
