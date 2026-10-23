# SRS 07｜模組介面契約

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | 三人共同；有爭議時由主管仲裁 |
| 實體檔案 | `newsroom/schemas/*.schema.json`（JSON Schema draft 2020-12）、`tests/fixtures/workspace/` |

這份文件是三個人之間「交棒」的依據。上游產出的檔案只要通過這裡的 schema 驗證，下游就必須能處理；下游不得自行假設 schema 以外的欄位。

**修改規則**
- 新增選填欄位：直接修改，並通知其他兩人。
- 新增必填欄位、改名、刪除、改型別：視為破壞性變更，要開票，三人都同意後才能合併，並同步更新 fixtures。
- 每份 schema 都有 `schema_version` 欄位。破壞性變更時，主版本號 +1。

---

## IF-001｜inbox 檔案（collect → 人 → produce）

路徑：`inbox/YYYY-MM-DD.md`。程式只改寫受管區塊，人只改勾選框、`格式`、`節目`、`備註` 這三個欄位。

```markdown
# 2026-10-17 候選

<!-- newsroom:begin candidates -->
## S 級
- [ ] **Tesla Roadster 二代發表** `cand_3f9a1c0b7e22`
  - 共振 5 源｜X 官方直播、Electrek、HN 412 分｜首次出現 01:42｜在地：中文圈尚未報導
  - 建議：ai-today｜threads, carousel, reel
  - 節目：
  - 格式：
  - 備註：
<!-- newsroom:end candidates -->
```

| 欄位 | 規則 |
|---|---|
| 勾選 `[x]` | 代表選題，不代表允許發布 |
| `cand_…` | 不可修改，程式靠它把勾選對應回候選 |
| 節目 | 空白時採用建議值；可填 `ai-today`、`tool-review`、`ai-engineering` |
| 格式 | 空白時採用建議值；逗號分隔，可填 `threads`、`carousel`、`reel` |
| 備註 | 自由文字，會寫進 topic 的 `notes.md` |

## IF-002｜topic.md front-matter（produce 建立，人可修改）

```yaml
---
schema_version: 1
id: 2026-10-17-roadster
title: Tesla Roadster 二代發表
program: ai-today            # 預設節目；各 content 可以覆寫
tier: S                      # S | A | B | evergreen | null
candidate_ids: [cand_3f9a1c0b7e22]
source_ids: [src_3f9a1c0b7e22, src_77ab01c9d3e1]
first_seen: 2026-10-17T01:42:00+08:00
created_at: 2026-10-17T08:10:00+08:00
---
```

本文由人自由撰寫。狀態摘要放在受管區塊 `<!-- newsroom:begin status -->`，程式只更新這個區塊。

## IF-003｜content.md front-matter

```yaml
---
schema_version: 1
id: reel-01
topic_id: 2026-10-17-roadster
program: ai-today
format: reel                 # threads | carousel | reel
selected_revision: r002      # 人選用的草稿版本；null 代表還沒選
angle: 一句話觀點，來自 inbox 備註
wave: 1                      # 一題三波：1 | 2 | 3
---
```

狀態以 SQLite 為準，content.md 只有受管區塊的狀態摘要。

## IF-004｜sources/<source_id>.md（fetch 產出）

```yaml
---
schema_version: 1
source_id: src_3f9a1c0b7e22
url: https://www.tesla.com/blog/...
canonical_url: https://www.tesla.com/blog/...
kind: article                # article | tweet | release | video | paper | forum | other
publisher: Tesla
author: null
published_at: 2026-10-17T01:30:00Z    # 可空
fetched_at: 2026-10-17T08:11:02+08:00
fetch_method: trafilatura    # 實際使用的擷取方法
language: en
word_count: 1240
---
（正文 Markdown；圖片以原始網址保留）
```

## IF-005｜assets.json（fetch 與人工素材盤點產出；draft、production 讀取）

```json
{
  "schema_version": 1,
  "topic_id": "2026-10-17-roadster",
  "updated_at": "2026-10-17T08:15:00+08:00",
  "assets": [
    {
      "asset_id": "ast_a1b2c3d4e5f6",
      "path": "media/raw/official-02.mp4",
      "type": "video",
      "origin": "official",
      "source_id": "src_3f9a1c0b7e22",
      "source_url": "https://...",
      "fetch_layer": 2,
      "fetch_method": "html-video",
      "duration_ms": 18400,
      "width": 1920, "height": 1080, "fps": 30,
      "has_audio": true,
      "usable_ranges_ms": [[0, 18400]],
      "notes": "",
      "usage_note": "官方影片，標註來源"
    }
  ],
  "gaps": [
    {"need": "demo", "reason": "互動式 demo 無影片檔", "suggestion": "人工錄 30 秒"}
  ]
}
```

| 欄位 | 規則 |
|---|---|
| `type` | `video`、`image`、`gif`、`audio`、`screenshot`、`document` |
| `origin` | `official`（官方）、`self`（自錄）、`third_party`（第三方）、`generated`（生成）、`derived`（衍生） |
| `fetch_layer` | 1–5，對應 SRS 03 的分層；自錄素材為 null |
| `source_id` | 自錄素材可以是 null |
| `path` | 相對於 topic 資料夾的路徑 |

## IF-006｜草稿版本 drafts/<revision>/（draft 產出）

```text
drafts/r002/
  meta.json        # 生成資訊
  script.md        # 影片格式才有
  carousel.md      # 輪播格式才有
  caption.md       # 所有格式都有
  citations.json
```

`meta.json`：

```json
{
  "schema_version": 1,
  "revision": "r002",
  "content_ref": "2026-10-17-roadster/reel-01",
  "created_at": "2026-10-17T08:20:00+08:00",
  "model": "<llm 名稱>",
  "inputs": {
    "source_ids": ["src_3f9a1c0b7e22"],
    "asset_ids": ["ast_a1b2c3d4e5f6"],
    "notes_sha256": "…",
    "voice_sha256": "…",
    "glossary_sha256": "…"
  },
  "cost_usd": 0.12,
  "unverified_count": 1
}
```

`citations.json`：每個需要依據的說法對應到 source_id 與原文摘錄。

```json
{
  "schema_version": 1,
  "claims": [
    {"claim_id": "c1", "text": "0–100 km/h 加速 0.81 秒", "source_id": "src_3f9a1c0b7e22",
     "quote": "0-60 mph in under 1 second", "status": "verified"},
    {"claim_id": "c2", "text": "售價約 20 萬美元", "source_id": null, "quote": null,
     "status": "unverified"}
  ]
}
```

人選用某個版本時，程式會把該版本的檔案複製到 content 資料夾，並更新 `selected_revision`。從此之後 content 資料夾裡的稿件屬於人，程式不再寫入。

## IF-007｜script.md 節奏表（draft 產出，人修改；production 讀取）

script.md 分成兩段：講稿（自由文字）和節奏表（固定格式表格，程式會解析）。

```markdown
## 講稿
Tesla 真的做出會飛的車了？……

## 節奏表
| shot | 時間 | 畫面 | 素材 | 裁切 | 講稿 | 強調 | 引用 |
|---|---|---|---|---|---|---|---|
| s01 | 0–3s | 官方 Demo | ast_a1b2c3d4e5f6 | 6.0–9.0 | Tesla 真的做出會飛的車了？ | 會飛 | |
| s02 | 3–15s | 數字卡 | template:number-card | | 加速只要 0.81 秒 | 0.81 秒 | c1 |
```

| 欄位 | 規則 |
|---|---|
| shot | `s` + 兩位數，表內唯一 |
| 時間 | 預估時間；實際時間以錄音對時為準 |
| 素材 | asset_id，或 `template:<模板名>` |
| 裁切 | 秒數 `起–迄`；只有影片素材需要 |
| 引用 | citations.json 的 claim_id，多個時以逗號分隔 |

解析失敗（欄位缺漏、asset_id 不存在、裁切超過片長）時，production 回報 `E_INPUT`，並指出是哪一列，不能自行猜測。

## IF-008｜carousel.md（draft 產出，人修改；production 讀取）

```markdown
---
schema_version: 1
template: news-default
---
## 1 封面
標題：Tesla Roadster 二代來了
副標：0.81 秒破百是真的嗎？
圖：ast_a1b2c3d4e5f6

## 2
標題：三個重點
內文：
- 加速 0.81 秒 [c1]
- …
```

每個 `## n` 是一張卡；可用的欄位由模板定義（見 SRS 05 CAR-FR）。

## IF-009｜caption.md（所有格式都有）

```markdown
---
schema_version: 1
platforms: [threads, instagram]
---
（貼文本文）

#AI #Tesla

來源：Tesla 官方部落格 https://...
```

## IF-010｜shots.json（production 內部：轉換器產出 → 渲染器讀取）

```json
{
  "schema_version": 1,
  "build_id": "b20261017-143012",
  "fps": 30,
  "width": 1080, "height": 1920,
  "duration_frames": 1800,
  "layout": "split-top-demo",
  "shots": [
    {
      "shot_id": "s01",
      "start_frame": 0, "duration_frames": 90,
      "visual": {"kind": "asset", "asset_id": "ast_a1b2c3d4e5f6",
                 "path": "../../../../media/raw/official-02.mp4",
                 "trim_start_ms": 6000, "trim_end_ms": 9000, "transparent": false},
      "text": "Tesla 真的做出會飛的車了？",
      "emphasis": ["會飛"],
      "claim_ids": []
    },
    {
      "shot_id": "s02",
      "start_frame": 90, "duration_frames": 360,
      "visual": {"kind": "template", "template": "number-card",
                 "props": {"value": "0.81", "unit": "秒"}},
      "text": "加速只要 0.81 秒",
      "emphasis": ["0.81 秒"],
      "claim_ids": ["c1"]
    }
  ],
  "audio": {"voiceover": "../../../../media/raw/vo-01.wav", "bgm": null}
}
```

## IF-011｜captions.json（字幕流程產出 → 渲染器讀取）

```json
{
  "schema_version": 1,
  "source_audio": "media/raw/vo-01.wav",
  "audio_sha256": "…",
  "script_revision": "r002",
  "lines": [
    {
      "start_ms": 0, "end_ms": 2800,
      "text": "Tesla 真的做出會飛的車了？",
      "words": [
        {"text": "Tesla", "start_ms": 0, "end_ms": 520, "emphasis": false},
        {"text": "真的", "start_ms": 520, "end_ms": 900, "emphasis": false},
        {"text": "會飛", "start_ms": 1800, "end_ms": 2300, "emphasis": true}
      ]
    }
  ],
  "manual_edits": false
}
```

時間一律是相對於成品起點的毫秒。一行字幕的字數上限由模板設定（預設 14 個全形字）。

## IF-012｜manifest.json（每次 Build 一份，綁定所有輸入）

```json
{
  "schema_version": 1,
  "build_id": "b20261017-143012",
  "content_ref": "2026-10-17-roadster/reel-01",
  "format": "reel",
  "created_at": "2026-10-17T14:30:12+08:00",
  "inputs": {
    "script_sha256": "…", "selected_revision": "r002",
    "carousel_sha256": null,
    "captions_path": "captions.json", "shots_path": "shots.json",
    "assets": [{"asset_id": "ast_a1b2c3d4e5f6", "sha256": "…", "source_id": "src_3f9a1c0b7e22"}]
  },
  "output_spec": {"preset": "reel-1080x1920-30"},
  "outputs": [{"path": "out/reel.mp4", "sha256": "…", "kind": "video"}],
  "checks": {"spec": "pass", "overflow": null},
  "status": "succeeded"
}
```

判斷「需要重製」：只要 manifest 裡記錄的任何輸入雜湊和現況不同，這個 Build 就標記為過期（stale）。

## IF-013｜Python → Node 渲染呼叫

```bash
node render/dist/cli.js <command> --manifest <path/to/manifest.json> [--preview]
# command: reel | carousel | still | check
```

- Node 端讀取 manifest 指到的 shots／captions／carousel，輸出到 manifest 所在資料夾的 `out/`。
- 標準輸出的**最後一行**必須是一個 JSON：

```json
{"ok": true, "outputs": ["out/reel.mp4"], "duration_ms": 412000, "warnings": []}
{"ok": false, "error_code": "E_INPUT", "message": "shot s03 的 asset 檔案不存在", "details": {"shot_id": "s03"}}
```

- 結束碼與 SRS 00 §5.4 相同。進度以 `PROGRESS <0-100>` 一行一筆輸出到 stderr。

## IF-014｜Job 紀錄（所有模組透過協調器建立）

```json
{
  "job_id": "01JBX…",
  "kind": "fetch | draft | subtitle | render | collect | publish | metrics",
  "target": "2026-10-17-roadster/reel-01",
  "status": "queued | running | succeeded | blocked | failed",
  "attempt": 1,
  "input_fingerprint": "sha256…",
  "output_paths": ["…"],
  "error_code": null,
  "error_message": null,
  "retry_after": null,
  "created_at": "…", "started_at": "…", "finished_at": "…"
}
```

`input_fingerprint` 相同、而且上次執行已成功的 Job，不會重跑。這就是冪等的依據（NFR-003）。

## IF-015｜Step 介面（Python 模組實作，由協調器呼叫）

```python
class Step(Protocol):
    kind: str                                   # 對應 Job.kind
    def fingerprint(self, ctx: StepContext) -> str: ...
    def run(self, ctx: StepContext) -> StepResult: ...

@dataclass
class StepResult:
    status: Literal["succeeded", "blocked", "failed"]
    output_paths: list[Path]
    error_code: str | None = None
    message: str | None = None
    cost_usd: float | None = None
```

每個模組只要實作 Step，就能接上協調器、重跑、鎖與預算控管，不需要自己處理狀態。

## IF-016｜Publication 與 publication.md

SQLite 是主資料。publication.md 是匯出給人看的摘要，並保留一個區塊讓人填回資料：

```markdown
<!-- newsroom:begin publications -->
| 平台 | 狀態 | 預定時間 | 實際時間 | 網址 | build |
|---|---|---|---|---|---|
| instagram | published | | 2026-10-17 15:02 | https://instagram.com/p/... | b20261017-143012 |
<!-- newsroom:end publications -->
```

人填回時使用 CLI：`newsroom mark-published <content_ref> --platform instagram --url <url> [--at <time>]`。程式不解析人在 Markdown 表格裡手改的內容。

## IF-017｜MetricSnapshot

```json
{"publication_id": "01JBY…", "captured_at": "2026-10-18T15:02:00+08:00",
 "source": "manual | instagram_api | threads_api | youtube_api",
 "metrics": {"views": 10234, "likes": 512, "comments": 33, "shares": 41, "saves": 120, "reach": null}}
```

沒有資料的指標存 `null`，不能存成 0。

## IF-018｜Fixtures

`tests/fixtures/workspace/` 是一份完整的假工作區，至少要有：

| 路徑 | 用途 | 誰維護 |
|---|---|---|
| `inbox/2026-10-17.md` | 含已勾選與未勾選的候選 | Albert |
| `topics/2026-10-17-demo/sources/*.md` | 兩篇原文 | Albert |
| `topics/2026-10-17-demo/assets.json`＋`media/raw/` | 一段 10 秒影片、一張圖、一段 20 秒旁白 wav | Albert |
| `contents/reel-01/drafts/r001/` | 一份完整草稿 | 周長曉 |
| `contents/reel-01/builds/b…/{shots,captions,manifest}.json` | 可以直接渲染的輸入 | 周長曉 |
| `contents/threads-01/` | 已確認、已發布的 Threads 內容 | 楊舒媁 |
| `data/fixture.sql` | 對應的 SQLite 資料 | 楊舒媁 |

Sprint 1 第一週結束前，fixtures 要能通過所有 schema 驗證。之後每次修改 schema，都要同步更新 fixtures。
