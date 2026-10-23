# SRS 04｜草稿生成

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | **周長曉** |
| 對應 Epic | E04 草稿 |
| 輸入契約 | sources/*.md（IF-004）、assets.json（IF-005）、notes.md、voice.md、glossary.md |
| 輸出契約 | [IF-006 drafts](07_interfaces.md#if-006草稿版本-draftsrevisiondraft-產出)、[IF-007 script.md](07_interfaces.md#if-007scriptmd-節奏表draft-產出人修改production-讀取)、IF-008 carousel.md、IF-009 caption.md |
| 工具 | 見 [技術選型 §草稿](../04_Tech_Selection.md) |

---

## 1. 流程：三段式，事實和寫作分開

```
① 抽事實（LLM，開 Citations）
   sources/*.md → facts.json：一句一個事實，帶原文摘錄與位置
② 寫稿（LLM，結構化輸出）
   facts.json＋assets.json＋notes＋voice＋glossary＋節目模板 → 結構化草稿（JSON）
③ 程式檢查（不用 LLM）
   講稿中的數字與專有名詞，逐一比對 fact 的原文摘錄；比對不到的標為待確認
→ 轉成 Markdown（script.md／carousel.md／caption.md）＋citations.json，存為新的 revision
```

為什麼要拆成兩次呼叫：Claude API 的 Citations 和結構化輸出不能在同一個請求同時啟用。拆開之後，事實抽取可以快取重用，換素材或改觀點重寫時，只需要重跑第 ②、③ 段。

## 2. LLM adapter 介面

```python
class LLM(Protocol):
    def extract_facts(self, docs: list[SourceDoc]) -> FactsResult: ...          # 開 citations
    def generate(self, prompt: Prompt, schema: type[BaseModel]) -> GenResult: ... # 結構化輸出
    # 兩者都回傳 usage（input/output/cache tokens）與 cost_usd，交給預算控管
```

- 第一個實作是 Anthropic SDK＋Pydantic schema；Instructor 當作換供應商時的備援（見技術選型）。
- 模型名稱、溫度、最大 token 都放在 config；程式裡不寫死。
- 固定不變的內容（系統指示、voice、glossary、few-shot、節目模板）放在 prompt 最前面，讓 prompt caching 生效；每次不同的 facts 與素材放在後面。

## 3. 功能需求

**DRF-FR-001｜事實抽取**（R1｜M｜US-031）
- **輸入**：topic 的所有 sources/*.md。
- **處理**：把每份來源以 document 形式送進 LLM，開啟 citations，要求「一句一個事實，數字、名稱、日期都要完整」。結果存成 `topics/<id>/facts.json`：

```json
{"schema_version": 1, "facts": [
  {"fact_id": "f01", "claim": "Roadster 二代 0–60 mph 加速不到 1 秒",
   "numbers": ["1 秒"], "entities": ["Tesla", "Roadster"],
   "source_id": "src_3f9a1c0b7e22", "cited_text": "0-60 mph in under 1 second",
   "char_start": 1032, "char_end": 1060}
]}
```

- **快取**：輸入的 sources 雜湊沒變時，不重新抽取。
- **驗收**：同一組來源執行兩次，第二次不呼叫 LLM；每個 fact 的 cited_text 都能在原文中逐字找到。

**DRF-FR-002｜節目模板**（R1｜M｜US-034）
- **處理**：每個節目有一份模板 `templates/programs/<program>.md`，定義段落結構與長度：
  - AI Today：鉤子 → 一句話講清楚 → 2–3 個數字 → 背景或比較 → 保留語 → 請觀眾留言；30–45 秒
  - Tool Review：問題 → 實測 → 優缺點 → 適合誰；45–90 秒
  - AI Engineering：現象 → 原理 → 拆解 → 啟發；60–120 秒
- 每個格式也有輸出 schema：threads（貼文）、carousel（卡片陣列）、reel（講稿＋節奏表）。
- **驗收**：新增一個節目只需要加模板檔，不改程式。

**DRF-FR-003｜依素材生成草稿**（R1｜M｜US-030）
- **輸入**：facts.json、assets.json（類型、長度、可用片段、使用註記）、notes.md、content.md 的 angle、voice.md、glossary.md、節目模板。
- **處理**：
  1. 用 Pydantic 定義輸出結構（講稿段落、每段的 fact_ids、節奏表、圖卡、caption），以結構化輸出呼叫 LLM。
  2. 規則寫在系統指示裡：facts.json 沒有的事實不能寫；節奏表只能使用 assets.json 裡存在的 asset_id 或模板；裁切範圍不能超過素材長度。
  3. 先盤點素材，再寫稿（05 §8.1）：素材少就寫短、多用模板畫面；沒有 Demo 時不寫「看這段 Demo」。
- **輸出**：`drafts/<revision>/`（IF-006）。
- **例外**：沒有任何來源時，`E_MISSING_SOURCE`（blocked）。LLM 回傳的 JSON 不符合 schema 時，自動重試一次；仍然失敗就 failed，並保留原始回應以便除錯。
- **驗收**：用 fixture topic 生成 threads、carousel、reel 三種草稿，都通過 schema 驗證；節奏表裡的 asset_id 都存在於 assets.json。

**DRF-FR-004｜引用檢查（程式）**（R1｜M｜US-031）
- **處理**：
  1. 從講稿、圖卡、caption 抽出所有數字（含中文數字、百分比、倍數、金額、日期）與英文專有名詞。
  2. 正規化：「八成」對到 80%、「1.5 億」對到 150M、全形半形統一、千分位去掉。
  3. 每個數字與名詞，都要能在它所屬段落的 fact_ids 指到的 cited_text（或 claim）裡找到。
  4. 找不到的，在 citations.json 標為 `unverified`，並在 Markdown 中以 `⚠待確認` 標記。
- **驗收**：準備 10 份有標準答案的草稿（含 50 個數字與名詞），漏標（錯的或沒出處卻沒標出來）= 0，誤報 ≤ 10%。

**DRF-FR-005｜口吻與名詞**（R1｜S｜US-033）
- **處理**：voice.md 提供口吻描述與 3–5 則創作者自己的貼文作為 few-shot（一律使用繁體，避免用詞偏中國）；glossary.md 定義專有名詞的寫法與禁用詞。生成後用程式檢查禁用詞與台灣用語（例如「軟件」換成「軟體」），結果列在 review 的待確認項。
- **驗收**：草稿裡出現 glossary 的禁用詞時，review 列出所在位置。

**DRF-FR-006｜版本與人工稿保護**（R1｜M｜US-032、NFR-004）
- **處理**：
  - 每次生成都是新的 revision（`r001`、`r002`…），存在 `drafts/` 底下，**不寫入** content 資料夾。
  - 第一次生成而且 content 資料夾裡還沒有稿件時，自動選用 r001（複製到 content 資料夾，設定 `selected_revision`）。
  - 之後必須由人執行 `newsroom select <ref> <revision>` 才會選用新版本。選用前，如果 content 資料夾的稿件和目前選用的 revision 不同（代表人改過），先把它備份成 `drafts/<revision>-edited/`，再複製新版本。
- **驗收**：人修改 script.md 後執行 redraft，script.md 完全不變，新稿出現在 drafts/r002；執行 select r002 後，人改過的版本被備份。

**DRF-FR-007｜重新生成（redraft）**（R1｜M｜US-032）
- **輸入**：`newsroom redraft <content_ref> [--note "..."]`。
- **處理**：重新讀取 notes、素材與 facts（facts 沒變就用快取），生成新 revision；`--note` 會附加到這次 prompt，並記錄在 meta.json。
- **驗收**：補一段自錄影片並重新盤點後執行 redraft，新的節奏表有使用這段影片。

**DRF-FR-008｜Threads 文字輸出**（R1｜M）
- **處理**：threads 格式的草稿就是成品：caption.md 的本文符合 Threads 的字數上限（設定在 config，預設 500 字），超過時拆成串文（1/n）；來源連結放最後。
- **驗收**：一篇長稿被正確拆成串文，每段都在上限內。

**DRF-FR-009｜節奏表解析器**（R3｜M）
- **處理**：提供 `parse_script(path) -> Script`，解析 IF-007 的節奏表，檢查欄位、asset_id 是否存在、裁切範圍是否在素材長度內、claim_id 是否存在。production 模組與 review 都使用這個解析器。
- **例外**：任何錯誤都要指出是哪一列、哪一欄、為什麼。
- **驗收**：準備 10 種錯誤的節奏表，每一種都回報正確的列與原因。

**DRF-FR-010｜成本紀錄**（R1｜M｜NFR-006）
- **處理**：每次呼叫記錄 input、output、cache read、cache write 的 token 數與花費，寫入 meta.json 與 spend 資料表。
- **驗收**：同一個 topic 第二次生成時，`cache_read_input_tokens` 大於 0。

## 4. 驗證實驗（Spike）

| Spike | 內容 | 過關條件 |
|---|---|---|
| SPK-DRF-1 引用準確度 | 10 篇真實新聞跑完 ①～③，人工核對 50 個數字與名詞 | 漏標 = 0，誤報 ≤ 10% |
| SPK-DRF-2 口吻盲測 | 5 篇 AI 草稿混 5 篇自己寫的，請 2 人猜哪篇是 AI | 猜對率 ≤ 70% |
| SPK-DRF-3 成本 | 量測一則 Reel 草稿的 token 與花費，比較有無快取 | 第二次以後快取命中，單則花費落在預算內 |
