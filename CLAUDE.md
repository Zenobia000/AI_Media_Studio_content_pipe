# 開發用 CLAUDE.md

給在這個 repo 裡工作的 agent 看的。專案是什麼、誰負責什麼，看 [README.md](README.md)。

## 要看哪裡

| 要知道什麼 | 看 |
|---|---|
| 需求與介面格式 | [docs/spec/00_overview.md](docs/spec/00_overview.md)、[docs/spec/07_interfaces.md](docs/spec/07_interfaces.md) |
| 架構與技術選型 | [docs/architecture/architecture.md](docs/architecture/architecture.md)、[docs/architecture/tech-selection.md](docs/architecture/tech-selection.md) |
| 分支與 PR 規則 | [docs/process/branching-strategy.md](docs/process/branching-strategy.md) |
| skill 怎麼對應口語需求 | [skills/RESOLVER.md](skills/RESOLVER.md)；每個 skill 的規則在它自己的 `SKILL.md` |
| 後端（Python）怎麼跑、和原版差在哪 | [docs/process/backend-python-handover.md](docs/process/backend-python-handover.md) |
| `newsroom` 指令、手動安裝、環境變數 | [docs/process/newsroom-commands.md](docs/process/newsroom-commands.md) |
| 在另一台電腦上跑起來（安裝、模型、檢查環境） | [docs/process/setup-new-machine.md](docs/process/setup-new-machine.md)；`python check_setup.py` |
| 進度 | [AIHOT/PROGRESS.md](AIHOT/PROGRESS.md) |

## 規則

- 程式在 `src/newsroom/`（Python 3.12），測試在 `tests/`。安裝用 `python -m pip install -e ".[dev]" -c constraints.txt`；改了 `pyproject.toml` 的依賴，照 `constraints.txt` 開頭的說明重新產生它。提交前跑 `python -m ruff check src tests` 和 `python -m mypy src`，兩個都要過。
- 測試：`python -m pytest tests -q`。要資料庫的測試需要 `DATABASE_URL`（庫名以 `_test` 或 `_ci` 結尾）；和 Node 對照的測試還需要 `PARITY_DATABASE_URL` 與 `AIHOT/node_modules`，沒有時會略過。
- `AIHOT/` 是 AIHOT 的網站與原本的 TypeScript 後端，`AIHOT/AGENTS.md` 的規則適用於那裡。已發布的資料庫遷移（`AIHOT/database/migrations/`）不能改，修正放進新檔案。
- 工具輸出依 IF-020（`--json`）：結束碼 `0` 成功、`1` 失敗、`2` 輸入有誤、`3` 被阻擋。
- 不要 push、不要刪除 Node 後端，除非使用者明確要求。
