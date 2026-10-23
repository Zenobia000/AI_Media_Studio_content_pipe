# SRS 04｜草稿生成

| 項目 | 內容 |
|---|---|
| 版本 | v1.1｜2026-10-24（事實抽取與寫稿改由 agent 執行，見架構 ADR-08、ADR-09） |
| 擁有者 | **周長曉** |
| 對應 Epic | E04 草稿 |
| 輸入契約 | sources/*.md（IF-004）、assets.json（IF-005）、notes.md、voice.md、glossary.md |
| 輸出契約 | [IF-006 drafts](07_interfaces.md#if-006草稿版本-draftsrevisiondraft-產出)、[IF-007 script.md](07_interfaces.md#if-007scriptmd-節奏表draft-產出人修改production-讀取)、IF-008 carousel.md、IF-009 caption.md |
| 工具 | 見 [技術選型 §草稿](../architecture/tech-selection.md) |

---

## 1. 流程：三段式，事實和寫作分開

```
① 抽事實（agent，依 draft-content skill）
   sources/*.md → facts.json：一句一個事實，帶原文摘錄與位置
   → 工具 facts-verify：schema、每段 cited_text 在原文逐字存在
② 寫稿（agent，依 draft-content skill、節目模板、voice、glossary）
   facts.json＋assets.json＋notes＋voice＋glossary＋節目模板 → 草稿目錄（script／carousel／caption＋draft.json）
③ 程式檢查（工具 draft-save，不用 LLM）
   schema、asset_id、裁切範圍；講稿中的數字與專有名詞逐一比對 fact 的原文摘錄；比對不到的標為待確認
→ 通過後存為新的 revision（drafts/rNNN/）
```

判斷與寫作在 agent 裡做；可以驗證的事（原文是否存在、數字是否對得上、格式是否正確）都在工具裡做。工具不呼叫 LLM，所以不需要 LLM adapter；agent 的花費由 run 記錄（AGT-FR-004）。

## 2. `draft-content` skill 與工具的分工

| 步驟 | 誰做 | 失敗時 |
|---|---|---|
| 讀 sources、抽事實、寫 facts.json | agent | — |
| 驗證 facts.json | `newsroom facts-verify` | 回報哪一個 fact 的摘錄找不到；agent 修正後重新驗證，最多 2 次，仍失敗就 blocked |
| 依模板寫稿 | agent | — |
| 驗證並存成 revision | `newsroom draft-save` | 回報 schema 或 asset_id 錯誤的位置；agent 修正後重存，最多 2 次 |
| 選用版本 | `newsroom select`（人說要用哪一版時） | — |

## 3. 功能需求

**DRF-FR-001｜事實抽取與驗證**（R1｜M｜US-031）
- **輸入**：topic 的所有 sources/*.md。
- **處理**：agent 依 `draft-content` skill 讀每份來源，「一句一個事實，數字、名稱、日期都要完整，附原文逐字摘錄」，寫成 `topics/<id>/facts.json`；再呼叫 `newsroom facts-verify` 驗證：

```json
{"schema_version": 1, "facts": [
  {"fact_id": "f01", "claim": "Roadster 二代 0–60 mph 加速不到 1 秒",
   "numbers": ["1 秒"], "entities": ["Tesla", "Roadster"],
   "source_id": "src_3f9a1c0b7e22", "cited_text": "0-60 mph in under 1 second",
   "char_start": 1032, "char_end": 1060}
]}
```

- **驗證規則**：符合 schema；`cited_text` 在 `source_id` 的原文中逐字存在（全形半形與空白正規化後），`char_start`／`char_end` 由工具回填；`numbers`、`entities` 都出現在 `cited_text` 或 `claim` 中。
- **快取**：facts.json 記錄 sources 的雜湊；雜湊沒變時 skill 直接沿用，不重抽。
- **驗收**：故意放一個原文沒有的摘錄，`facts-verify` 指出是哪個 fact；同一組來源第二次執行 skill，不重新抽取。

**DRF-FR-002｜節目模板**（R1｜M｜US-034）
- **處理**：每個節目有一份模板 `templates/programs/<program>.md`，定義段落結構與長度：
  - AI Today：鉤子 → 一句話講清楚 → 2–3 個數字 → 背景或比較 → 保留語 → 請觀眾留言；30–45 秒
  - Tool Review：問題 → 實測 → 優缺點 → 適合誰；45–90 秒
  - AI Engineering：現象 → 原理 → 拆解 → 啟發；60–120 秒
- 每個格式也有輸出 schema：threads（貼文）、carousel（卡片陣列）、reel（講稿＋節奏表）。
- **驗收**：新增一個節目只需要加模板檔，不改程式，也不改 skill。

**DRF-FR-003｜依素材生成草稿**（R1｜M｜US-030）
- **輸入**：facts.json、assets.json（類型、長度、可用片段、使用註記）、notes.md、content.md 的 angle、voice.md、glossary.md、節目模板。
- **處理**：
  1. agent 依 skill 與節目模板寫稿，輸出到暫存目錄：script.md、carousel.md、caption.md，以及 `draft.json`（講稿段落、每段的 fact_ids、節奏表、圖卡；schema 見 IF-006）。
  2. 規則寫在 skill 裡，並由 `draft-save` 檢查：facts.json 沒有的事實不能寫；節奏表只能使用 assets.json 裡存在的 asset_id 或模板；裁切範圍不能超過素材長度。
  3. 先盤點素材，再寫稿（05 §8.1）：素材少就寫短、多用模板畫面；沒有 Demo 時不寫「看這段 Demo」。
- **輸出**：`drafts/<revision>/`（IF-006）。
- **例外**：沒有任何來源時，`E_MISSING_SOURCE`（blocked）。`draft-save` 驗證失敗時回報位置，skill 修正後重存，最多 2 次；仍然失敗就 failed，暫存目錄保留以便除錯。
- **驗收**：用 fixture topic 跑 `draft-content` 的 eval，threads、carousel、reel 三種草稿都通過 `draft-save`；節奏表裡的 asset_id 都存在於 assets.json。

**DRF-FR-004｜引用檢查（程式）**（R1｜M｜US-031）
- **處理**：
  1. 從講稿、圖卡、caption 抽出所有數字（含中文數字、百分比、倍數、金額、日期）與英文專有名詞。
  2. 正規化：「八成」對到 80%、「1.5 億」對到 150M、全形半形統一、千分位去掉。
  3. 每個數字與名詞，都要能在它所屬段落的 fact_ids 指到的 cited_text（或 claim）裡找到。
  4. 找不到的，在 citations.json 標為 `unverified`，並在 Markdown 中以 `⚠待確認` 標記。
- **驗收**：準備 10 份有標準答案的草稿（含 50 個數字與名詞），漏標（錯的或沒出處卻沒標出來）= 0，誤報 ≤ 10%。

**DRF-FR-005｜口吻與名詞**（R1｜S｜US-033）
- **處理**：voice.md 提供口吻描述與 3–5 則創作者自己的貼文作為範例（一律使用繁體，避免用詞偏中國）；glossary.md 定義專有名詞的寫法與禁用詞。skill 寫稿時要讀這兩份；`draft-save` 用程式檢查禁用詞與台灣用語（例如「軟件」換成「軟體」），結果列在 review 的待確認項。
- **驗收**：草稿裡出現 glossary 的禁用詞時，review 列出所在位置。

**DRF-FR-006｜版本與人工稿保護**（R1｜M｜US-032、NFR-004）
- **處理**：
  - 每次生成都是新的 revision（`r001`、`r002`…），存在 `drafts/` 底下，**不寫入** content 資料夾。
  - 第一次生成而且 content 資料夾裡還沒有稿件時，自動選用 r001（複製到 content 資料夾，設定 `selected_revision`）。
  - 之後必須由人指定（對 agent 說「用 r002」，或自己執行 `newsroom select <ref> <revision>`）才會選用新版本；skill 不能自己選。選用前，如果 content 資料夾的稿件和目前選用的 revision 不同（代表人改過），先把它備份成 `drafts/<revision>-edited/`，再複製新版本。
- **驗收**：人修改 script.md 後執行 redraft，script.md 完全不變，新稿出現在 drafts/r002；執行 select r002 後，人改過的版本被備份。

**DRF-FR-007｜重新生成（redraft）**（R1｜M｜US-032）
- **輸入**：人對 agent 說要怎麼改（例如「短一點」「多講價格」），或在 notes.md 補一段。
- **處理**：`draft-content` skill 重新讀取 notes、素材與 facts（facts 沒變就沿用），寫出新稿，經 `draft-save` 存成新 revision；人的修改要求記錄在 meta.json 的 `request`。
- **驗收**：補一段自錄影片並重新盤點後執行 redraft，新的節奏表有使用這段影片。

**DRF-FR-008｜Threads 文字輸出**（R1｜M）
- **處理**：threads 格式的草稿就是成品：caption.md 的本文符合 Threads 的字數上限（設定在 config，預設 500 字），超過時拆成串文（1/n）；來源連結放最後。
- **驗收**：一篇長稿被正確拆成串文，每段都在上限內。

**DRF-FR-009｜節奏表解析器**（R3｜M）
- **處理**：提供 `parse_script(path) -> Script`，解析 IF-007 的節奏表，檢查欄位、asset_id 是否存在、裁切範圍是否在素材長度內、claim_id 是否存在。production 模組與 review 都使用這個解析器。
- **例外**：任何錯誤都要指出是哪一列、哪一欄、為什麼。
- **驗收**：準備 10 種錯誤的節奏表，每一種都回報正確的列與原因。

**DRF-FR-010｜成本紀錄**（R1｜M｜NFR-006）
- **處理**：草稿的花費以 run 為單位記錄（AGT-FR-004）：meta.json 記錄產生這個 revision 的 run_id；headless 執行的花費由 `newsroom run` 寫入 agent_runs 與 spend。
- **驗收**：從 revision 的 meta.json 能查到 run_id，`status --run` 顯示那次執行的花費。

## 4. 驗證實驗（Spike）

| Spike | 內容 | 過關條件 |
|---|---|---|
| SPK-DRF-1 引用準確度 | 10 篇真實新聞跑完 `draft-content`，人工核對 50 個數字與名詞 | 漏標 = 0，誤報 ≤ 10% |
| SPK-DRF-2 口吻盲測 | 5 篇 AI 草稿混 5 篇自己寫的，請 2 人猜哪篇是 AI | 猜對率 ≤ 70% |
| SPK-DRF-3 成本 | 量測一則 Reel 草稿的 agent 執行花費與回合數（互動與 headless 各一次） | 單則花費落在預算內；回合數穩定（3 次差距 ≤ 30%） |
