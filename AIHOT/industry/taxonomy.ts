// 這個行業的分類體系：類別、標籤詞表、公司（主體）名錄，以及防止張冠李戴的身份詞典。
// 模型按這裡的詞表打標籤，主題頁（topics.json）按標籤歸類，篩選欄按類別分組。
// 換行業時：類別的 key 會出現在網址裡（/all?category=…），上線後就不要再改；標籤和名錄可以隨時增減。

/**
 * 網頁上的類別（篩選欄、卡片角標、RSS 分類訂閱）。key 是網址和接口裡的身份，上線後不要改。
 * section 是日報裡的分節標題（幾個類別可以共用一節，按這裡的順序排）；guide 告訴結構抽取模型這一類收什麼、
 * 和相鄰類別的邊界在哪（總的歸類原則寫在 prompts/structure.md 裡）。
 * commentary 標出評論類（教程、觀點）：日報寫過的事又有評論類的後續報道，只佔一行快訊（報道它的信源夠多時除外）。
 * 沒歸上類的資料在日報裡放進第一個 key 為 industry 的類別所在的節（沒有就放最後一節）。
 * tagColor 是飛書推送卡片裡跟在標題後面的這一類小標籤的顏色（飛書 text_tag 的顏色名）。
 * feedLabel 是分類 RSS 標題裡的名字（不寫就用 label）。公開介面、RSS 和 MCP 裡要把一類並進另一類發布，寫在站點設定裡（site/site.ts 的 PUBLIC_CATEGORIES）。
 */
export const CATEGORIES = [
  { key: "ai-models", tagColor: "indigo", label: "模型", feedLabel: "AI 模型", section: "模型發布／更新", guide: "模型本身的發布、版本、權重開放、能力或價格變化，以及既有榜單上的模型成績。公佈一次跑分不是發布新基準，也不是教程。" },
  { key: "ai-products", tagColor: "turquoise", label: "產品", feedLabel: "AI 產品", section: "產品發布／更新", guide: "可使用的 AI 產品、功能、應用、工具、API、平臺和工程元件的發布更新。模型廠商發布的推理框架、運算元庫、硬體適配元件仍是產品，不能因為廠商名歸成模型。" },
  { key: "industry", tagColor: "orange", label: "產業", feedLabel: "產業動態", section: "產業動態", guide: "已發生的公司經營、融資併購、人事、合作、訴訟、政策、真實安全事故及調查進展。新聞由當事人發帖、帶有態度，也不因此變成觀點。" },
  { key: "paper", tagColor: "violet", label: "論文", feedLabel: "論文", section: "論文研究", guide: "以新研究方法、實驗設計與發現為核心的論文、技術報告、新基準或研究資料集。系統性紅隊實驗屬於研究；既有榜單成績歸模型，真實事故的新聞調查歸行業。" },
  { key: "tip", tagColor: "green", label: "教學", section: "技巧與觀點", guide: "讀者可以照著使用的方法、提示詞、工具用法、工程實踐復盤與技術講解。重點是可複用的做法；單純發布工具歸產品，只有態度和預測而無做法歸觀點。", commentary: true },
  { key: "opinion", tagColor: "purple", label: "觀點", section: "技巧與觀點", guide: "重點是作者的解釋、判斷、主張、預測、評論或訪談觀點。討論市場不自動歸行業，作者是名人不自動歸觀點。", commentary: true },
] as const satisfies ReadonlyArray<{ key: string; tagColor: string; label: string; feedLabel?: string; section: string; guide: string; commentary?: true }>;

/**
 * 這個行業最受關注的一類發布（AI 行業是新模型）：日報報頭的“N 個新模型”、改分類後修訂已出的報告都按它數。
 * category 是類別，tag 是標籤，兩者都對上才算；unit 接在數字後面。
 * 沒有這樣一類的行業設成 null，報頭就不顯示這個數。
 */
export const RELEASE: { category: string; tag: string; unit: string } | null = { category: "ai-models", tag: "模型發布", unit: "個新模型" };

/** 週報月報的總述可以直接寫、不必在報道里找到出處的行業通用詞（小寫）。站名會自動算進去。 */
export const PLAIN_TERMS: readonly string[] = ["ai", "api", "llm", "gpu", "agi", "ceo", "ipo"];

/**
 * 內容理解一步給每篇資料判的“內容型別”（寫在 prompts/content-understanding.md 裡，改了型別要同步改那份提示詞）。
 * 評分提示詞（prompts/selection-score.md）按型別給五個維度不同的權重。
 */
export const ITEM_TYPES = ["model_release", "product_launch", "tool_or_prompt", "research_paper", "industry_event", "opinion_analysis", "tutorial_explainer"] as const;

// ── 標籤詞表 ────────────────────────────────────────────────────────────────────────────

/** 每篇資料的第一個標籤必須是這些“分類標籤”之一。 */
export const CATEGORY_TAGS = [
  "產品更新", "模型發布", "論文/研究", "開源/倉庫", "教程/實踐", "現象/趨勢", "大佬觀點", "評測/基準", "安全/對齊", "行業動態", "政策/監管",
  "非AI/通用工具", "其他",
] as const;

/** 可選的主題標籤。 */
export const TOPIC_TAGS = [
  "Agent", "編碼", "推理", "多模態", "語音", "影片", "影像生成", "RAG", "端側", "資料/訓練", "搜尋", "部署/工程", "開源生態", "具身智慧", "MCP/工具呼叫",
] as const;

/** 可選的實體標籤（公司、機構、平臺）。 */
export const ENTITY_TAGS = ["OpenAI", "Anthropic", "DeepSeek", "DeepMind", "Google", "Meta", "Microsoft", "xAI", "Hugging Face", "GitHub", "arXiv"] as const;

/** 模型常寫的近義詞，統一成詞表裡的寫法。 */
export const TAG_SYNONYMS: Readonly<Record<string, string>> = {
  "教程/玩法": "教程/實踐", "技巧/最佳實踐": "教程/實踐", "合作/生態": "行業動態", "融資/收購": "行業動態", "公司動態": "行業動態",
  合作: "行業動態", 生態: "行業動態", 融資: "行業動態", 收購: "行業動態", 投資: "行業動態", 併購: "行業動態",
  政策: "政策/監管", 監管: "政策/監管", 法規: "政策/監管", 安全: "安全/對齊", 對齊: "安全/對齊",
  論文: "論文/研究", 研究: "論文/研究", paper: "論文/研究", papers: "論文/研究",
  "open-source": "開源/倉庫", 開源: "開源/倉庫", 倉庫: "開源/倉庫", repo: "開源/倉庫",
  教程: "教程/實踐", 玩法: "教程/實踐", 指南: "教程/實踐", 技巧: "教程/實踐", 最佳實踐: "教程/實踐", 實踐: "教程/實踐",
  產品: "產品更新", 更新: "產品更新", 發布: "模型發布", 模型: "模型發布", 趨勢: "現象/趨勢", 現象: "現象/趨勢", 觀點: "大佬觀點",
  影片生成: "影片", 非ai: "非AI/通用工具", "non-ai": "非AI/通用工具", 通用工具: "非AI/通用工具", 工程工具: "非AI/通用工具",
  安全掃描: "非AI/通用工具", devops: "非AI/通用工具", 行業: "行業動態", 動態: "行業動態",
};

// ── 公司與主體 ──────────────────────────────────────────────────────────────────────────

/**
 * 公司主題：id → 顯示名、卡片上顯示的標籤（null 表示只用 entity:<id> 歸類）、別名。
 * aliases 給結構抽取模型看；orgNames 是這家公司發訊息時開頭用的組織名（公司、實驗室、團隊），
 * 在它自己的主題頁上讀者知道說的是誰，可以省去；otherNames 是公司的其他稱呼（官方賬號名、子品牌）。
 * 所有這些名字都用來認出說的是哪家公司，比如把事實的主體對到發布方。
 */
export const ENTITIES: Record<string, { name: string; displayTag: string | null; aliases: string[]; orgNames?: string[]; otherNames?: string[] }> = {
  "world-labs": { name: "World Labs", displayTag: null, aliases: ["World Labs"] },
  "thinking-machines": { name: "Thinking Machines Lab", displayTag: null, aliases: ["Thinking Machines"] },
  amd: { name: "AMD", displayTag: null, aliases: ["AMD", "Advanced Micro Devices"] },
  openai: { name: "OpenAI", displayTag: "OpenAI", aliases: ["OpenAI", "ChatGPT", "Sora", "Codex", "GPT"], orgNames: ["OpenAI"], otherNames: ["OpenAI Developers"] },
  anthropic: { name: "Anthropic", displayTag: "Anthropic", aliases: ["Anthropic", "Claude"], orgNames: ["Anthropic"], otherNames: ["Claude Code"] },
  google: { name: "Google", displayTag: "Google", aliases: ["Google", "DeepMind", "Gemini", "谷歌"], orgNames: ["Google DeepMind", "Google Research", "Google", "DeepMind", "谷歌"], otherNames: ["Google AI", "Google Labs", "Google Cloud"] },
  deepseek: { name: "DeepSeek", displayTag: "DeepSeek", aliases: ["DeepSeek", "深度求索"], orgNames: ["DeepSeek", "深度求索"] },
  qwen: { name: "千問 Qwen", displayTag: null, aliases: ["Qwen", "通義", "阿里"], orgNames: ["通義千問", "通義實驗室", "阿里巴巴", "阿里雲", "阿里", "Qwen Team", "Qwen", "Alibaba"], otherNames: ["千問", "千問APP", "Alibaba Cloud"] },
  kimi: { name: "月之暗面 / Kimi", displayTag: null, aliases: ["Kimi", "月之暗面", "Moonshot", "Kimi / 月之暗面"], orgNames: ["Moonshot AI", "月之暗面", "Kimi"] },
  minimax: { name: "MiniMax", displayTag: null, aliases: ["MiniMax", "海螺"], orgNames: ["MiniMax", "稀宇科技"] },
  zhipu: { name: "智譜 GLM", displayTag: null, aliases: ["智譜", "GLM", "Z.ai"], orgNames: ["智譜AI", "智譜", "Zhipu AI", "Z.ai"], otherNames: ["Zhipu"] },
  xai: { name: "xAI", displayTag: "xAI", aliases: ["xAI", "Grok"], orgNames: ["SpaceX Super Intelligence", "SpaceXAI", "SpaceXSI", "xAI"], otherNames: ["Grok Bot"] },
  meta: { name: "Meta", displayTag: "Meta", aliases: ["Meta", "Llama"], orgNames: ["Meta Superintelligence Labs", "Meta AI", "Meta"], otherNames: ["AI at Meta", "Muse"] },
  microsoft: { name: "Microsoft", displayTag: "Microsoft", aliases: ["Microsoft", "微軟", "Copilot"], orgNames: ["Microsoft Research", "Microsoft AI", "Microsoft", "微軟"] },
  nvidia: { name: "NVIDIA", displayTag: null, aliases: ["NVIDIA", "輝達"], orgNames: ["NVIDIA", "輝達"] },
  "hugging-face": { name: "Hugging Face", displayTag: "Hugging Face", aliases: ["Hugging Face"], orgNames: ["Hugging Face", "HuggingFace"] },
  cursor: { name: "Cursor", displayTag: null, aliases: ["Cursor", "Anysphere"], orgNames: ["Cursor"] },
  openrouter: { name: "OpenRouter", displayTag: null, aliases: ["OpenRouter"], orgNames: ["OpenRouter"] },
};

/**
 * 身份詞典：摘要和標題裡出現的公司，必須在原文裡也出現過，否則退回原標題、丟掉摘要（防止模型張冠李戴）。
 * 行業沒有這個問題時可以留空陣列。
 */
export const IDENTITY_LEXICON: ReadonlyArray<{ id: string; name: string; patterns: RegExp[] }> = [
  { id: "openai", name: "OpenAI", patterns: [/openai|chatgpt|\bgpt-?[o\d]|\bsora\b|\bcodex\b/i] },
  { id: "anthropic", name: "Anthropic", patterns: [/anthropic|\bclaude\b/i, /\b(?:opus|sonnet|haiku)\s*\d+(?:[.\-]\d+)*\b/i, /\bfable\s*\d+(?:[.\-]\d+)*\b|\bmythos\b/i] },
  { id: "google", name: "Google / Gemini", patterns: [/google|deepmind|\bgemini\b|notebooklm|\bveo\s?\d|\bAlphaFold\b|\bAMIE\b/i] },
  { id: "deepseek", name: "DeepSeek", patterns: [/deepseek|深度求索/i] },
  { id: "xai", name: "xAI / Grok", patterns: [/\bxai\b|\bgrok\b/i] },
  { id: "meta", name: "Meta / Llama", patterns: [/\bMeta\b/, /\bmeta\s?ai\b|\bllama\b/i] },
  { id: "microsoft", name: "Microsoft / Copilot", patterns: [/microsoft|copilot|微軟/i] },
  { id: "nvidia", name: "NVIDIA", patterns: [/nvidia|輝達|\bnemotron\b|\bnemo\b|\bblackwell\b|\brubin(?:\s+ultra)?\b|\bcuda\b/i] },
  { id: "qwen", name: "千問 Qwen", patterns: [/\bqwen|通義|千問/i] },
  { id: "hugging-face", name: "Hugging Face", patterns: [/hugging\s?face/i] },
  { id: "cursor", name: "Cursor", patterns: [/\bCursor\b/] },
  { id: "kimi", name: "月之暗面 / Kimi", patterns: [/\bkimi\b|月之暗面|\bmoonshot\s?ai\b/i] },
  { id: "openrouter", name: "OpenRouter", patterns: [/openrouter/i] },
  { id: "minimax", name: "MiniMax", patterns: [/minimax/i] },
  { id: "zhipu", name: "智譜 GLM", patterns: [/智譜|\bglm-?[4-9]/i] },
  { id: "hunyuan", name: "騰訊混元", patterns: [/混元|hunyuan/i] },
  { id: "doubao", name: "字節豆包", patterns: [/豆包|doubao|字節跳動|bytedance/i] },
  { id: "mistral", name: "Mistral", patterns: [/mistral/i] },
  { id: "perplexity", name: "Perplexity", patterns: [/\bPerplexity\b/] },
  { id: "runway", name: "Runway", patterns: [/\brunway\b/i] },
  { id: "suno", name: "Suno", patterns: [/\bsuno\b/i] },
  { id: "midjourney", name: "Midjourney", patterns: [/midjourney/i] },
  { id: "stability-ai", name: "Stability AI", patterns: [/stability\s?ai/i] },
  { id: "elevenlabs", name: "ElevenLabs", patterns: [/eleven\s?labs/i] },
  { id: "vllm", name: "vLLM", patterns: [/\bvllm\b/i] },
  { id: "ollama", name: "Ollama", patterns: [/\bollama\b/i] },
  { id: "windsurf", name: "Windsurf", patterns: [/windsurf/i] },
  { id: "devin", name: "Devin", patterns: [/\bdevin\b/i] },
  { id: "manus", name: "Manus", patterns: [/\bmanus\b/i] },
  { id: "apple", name: "Apple AI", patterns: [/\bapple\s?(intelligence|silicon|ai)\b|蘋果(智慧|\s?AI)/i] },
  { id: "amazon", name: "Amazon / AWS", patterns: [/amazon|\baws\b|亞馬遜/i] },
  { id: "baidu", name: "百度文心", patterns: [/百度|baidu|文心|\bernie\s?bot\b/i] },
];

/** 這些域名上的文章，發布方就是對應的公司（託管平臺如 GitHub、arXiv 不算）。 */
export const PUBLISHER_DOMAINS: ReadonlyArray<{ entityId: string; domains: readonly string[] }> = [
  { entityId: "openai", domains: ["openai.com"] },
  { entityId: "anthropic", domains: ["anthropic.com", "claude.com"] },
  { entityId: "google", domains: ["deepmind.google", "ai.google", "blog.google"] },
  { entityId: "deepseek", domains: ["deepseek.com"] },
  { entityId: "xai", domains: ["x.ai"] },
  { entityId: "meta", domains: ["ai.meta.com"] },
  { entityId: "microsoft", domains: ["microsoft.com"] },
  { entityId: "nvidia", domains: ["nvidia.com"] },
  { entityId: "qwen", domains: ["qwen.ai"] },
  { entityId: "cursor", domains: ["cursor.com"] },
  { entityId: "openrouter", domains: ["openrouter.ai"] },
];

/** 原文裡的這些寫法也算提到了對應公司。 */
export const IDENTITY_CONTEXT_ALIASES: ReadonlyArray<{ entityId: string; pattern: RegExp }> = [
  { entityId: "meta", pattern: /@AIatMeta\b/i },
  { entityId: "zhipu", pattern: /\bZhipu(?:\s+AI\b|['’]s\b)/i },
];
