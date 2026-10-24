# 開發用 CLAUDE.md

這是 AI Media Studio 內容產線的開發 repo（工作區用的 CLAUDE.md 由 `newsroom init` 產生）。

## 先讀
- 產品需求：docs/product/PRD.md
- 規格（SRS）：docs/spec/00_overview.md；模組介面契約：docs/spec/07_interfaces.md
- 架構與程式碼目錄：docs/architecture/architecture.md（§4.1）
- 票：docs/tickets/README.md；分支策略：docs/process/branching-strategy.md

## 規則
- Python 3.11+，用 poetry；`poetry run ruff check .`、`poetry run mypy`、`poetry run pytest` 都要通過。
- 測試必須能離線執行（外部服務用 fixture 或錄製回應）。
- 一張票一條分支：`<type>/t-xxx-<描述>`，PR 回 `dev`，Conventional Commits。
- 改 `schemas/` 要三人都核准。
- Skills 放 `skills/<name>/SKILL.md`，規則見 docs/spec/01_core.md 與 skills/RESOLVER.md；CLI 只做確定性工具，編排由 skill 負責。
