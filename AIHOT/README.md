# MediaHOT

AI Media Studio 內容產線的**素材收集**部分：每天從一批 AI 與科技信源收資料，聚成事件、排出熱度，整理成每日 inbox；創作者勾選題目後，自動建題、擷取原文、盤點素材，交給後面的寫稿與製作。

這是 Albert Chiang 負責的模組；**收集、補料、inbox 的程式現在是 Python**（`../src/newsroom/`），這個資料夾保留 AIHOT 的網站與後端。**這份只放授權聲明。指令、環境變數、後端改 Python 之後每個步驟怎麼做，都在外層 [../README.md](../README.md)；進度看 [PROGRESS.md](PROGRESS.md)。** 整個專案的需求、介面契約與分工見 [../docs/](../docs/)。

## 基於 AIHOT

MediaHOT 是 [AIHOT](https://github.com/KKKKhazix/AIHOT) 開源引擎（MIT 授權，Copyright (c) 2026 數字生命卡茲克）的修改版。信源收集、去重、事件歸組、評分精選、熱度榜、網站與後臺都來自 AIHOT；原作者的版權宣告保留在 [LICENSE](LICENSE) 與 [NOTICE](NOTICE)。AIHOT 的名稱與 Logo 不在授權內，這個專案不使用它們。

## 授權

MIT，見 [LICENSE](LICENSE)。引擎部分的版權屬於原作者；我們的修改一併以 MIT 發布。
