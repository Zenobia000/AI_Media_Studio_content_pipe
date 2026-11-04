# 素材收集 MVP：交棒給組員

日期 2026-11-04。Albert 負責的部分（收集、補料、inbox）已是 Python（`src/newsroom/`），可以獨立跑。網站與 AIHOT 原本的後端（RSS／HN／HF 收集器、事件聚類、熱門排行）仍是 TypeScript，兩邊共用同一個 PostgreSQL。後端要不要整個改成 Python、怎麼改，等跟其他人串接之後再一起做。

## 已用真實網路驗證過的流程

在全新資料庫與全新工作區跑過：

```
newsroom init <工作區>
newsroom add-source https://github.com/ollama/ollama --title "…" --note "…"   # 進 inbox 候選
newsroom write-inbox                                                          # 產生 inbox/2026-11-04.md（含熱度分數、建議節目與格式）
（人在 inbox 勾選）
newsroom open-topic <cand_id> --formats threads,reel                          # topics/<id>/ 含 topic.md、contents/threads-01、reel-01
newsroom fetch-topic <topic_id>                                               # sources/<src_id>.md（IF-004 前置資訊＋Markdown 原文）
newsroom fetch-media <topic_id>                                               # 影片、圖片進 media/raw/
newsroom assets-topic <topic_id>                                              # assets.json（IF-005）、media.md、缺口
```

## 交出去的東西（下游讀這些）

| 檔案 | 誰讀 | 內容 |
|---|---|---|
| `inbox/YYYY-MM-DD.md` | 人、楊舒媁（早報） | 候選清單；受管區塊 `<!-- newsroom:begin candidates -->` 內人只改勾選、節目、格式、備註 |
| `topics/<id>/topic.md`、`notes.md` | 周長曉（寫稿） | 題目、節目、候選與來源編號、人的備註 |
| `topics/<id>/sources/*.md` | 周長曉 | 每個來源的原文，前置資訊有 `source_id`、`url`、`kind`、`published_at`、`fetch_method`、`word_count` |
| `topics/<id>/assets.json`、`media.md` | 周長曉（輪播、Reel） | 素材清單（路徑、型別、長度、尺寸、可用片段 `usable_ranges_ms`、來源與授權註記）與缺口 |
| `topics/<id>/media/proc/` | 周長曉（字幕、Reel） | 前處理後的影片（30fps CFR）與旁白（48kHz WAV，−14 LUFS） |
| `topics/<id>/contents/<format>-<nn>/content.md` | 周長曉、楊舒媁 | 每個格式一份，狀態在受管區塊 `status` |

資料庫：`candidate_state`（候選狀態：new／picked／opened／skipped）、`topics`、`topic_candidates`、`topic_sources`、`topic_assets`。**這幾張表的欄位不是三人定好的契約**，楊舒媁的狀態機接進來時要一起對。

## 串接時要一起決定的事

1. **契約（T-002／T-003）**：repo 還沒有 `schemas/` 與 fixture。上面的檔案格式是照 `docs/spec/07_interfaces.md` 寫的，但沒有用 JSON Schema 驗證過。候選編號格式規格是 `cand_` ＋ 12 碼，目前是 `cand_` ＋ AIHOT 的文章或事件編號。
2. **狀態管理**：content 的狀態目前只有 `status` 區塊裡的文字 `new`；楊舒媁的狀態機接進來後，誰寫、誰讀要對。
3. **排程**：目前由 `newsroom run-scheduler` 跑（inbox 每 30 分鐘、訊號每小時、網頁變動每 10 分鐘、補料自我測試每天 06:40）。楊舒媁的排程器做好後，要決定是否合併。
4. **通知**：S 級通知現在只送到飛書警報群 webhook；規格要走楊舒媁的通知（OPS-FR-003）。
5. **後端語言**：AIHOT 的 TypeScript 後端約 3 萬行，改成 Python 要另外排。建議等上面幾項串接過再做，才知道哪些部分真的會被用到。

## 怎麼跑

```
python -m pip install -e ".[dev]"
export DATABASE_URL=postgres://…/mediahot_dev      # 已套用 AIHOT migration 的資料庫
newsroom migrate && newsroom doctor
python -m pytest tests                              # 要資料庫的測試需要 DATABASE_URL 指向 *_test 或 *_ci
```
