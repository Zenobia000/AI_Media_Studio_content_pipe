# SPK-FET-2：瀏覽器錄影畫質（併入 T-106）

日期：2026-11-03｜做的人：Albert Chiang｜實作：`AIHOT/modules/inbox/layer4.ts`

## 做了什麼

用 Playwright（Chromium，`recordVideo`）開網址 → 等載入 → 平滑捲動 → 錄 20 秒 → ffmpeg 轉成 1920×1080、30fps、H.264、yuv420p、無聲的 MP4，再用 ffmpeg `blackdetect` 算黑畫面比例。

## 試了什麼、看到什麼

- 環境：Linux 容器（Node 24、Playwright 1.61.1 的 Chromium headless shell、ffmpeg）。
- 測試頁：我自己寫的一頁（三個彩色區塊、每 0.1 秒跳動的計數器），本機伺服器提供，錄 8 秒。
- 結果：錄影本身約 8.9 秒完成；輸出 h264／1920×1080／30fps，長度 8.67 秒；黑畫面比例 0；抽 1、4、7 秒三格，畫面清楚、有捲動、計數器在動。
- Playwright 原生錄影是 **25fps**，輸出 30fps 是 ffmpeg 補幀（重複幀），動作快的 Demo 會看得出不夠順。

## 沒有做、所以不能下結論的

- **沒有比較 Xvfb＋x11grab。** 票上要比 Playwright screencast 與 Xvfb＋x11grab，我只試了前者。Playwright 的 `recordVideo` 沒有 60fps 或 2× DPR 的選項，要 60fps 或高 DPI 才需要走 Xvfb 路線，這一段還沒驗證。
- **沒有用 3 個真的互動 Demo 驗收。** 只用了一頁自己寫的測試頁，沒有 WebGL、影片、載入動畫的頁面；「畫面清楚」只看了 3 格，需要你用真正會用到的 Demo 頁看過。
- 需要登入、有 cookie 牆或 bot 檢查的頁面沒有試；程式不繞過，會記成缺口。
- 沒有在 Windows 上試過（只在 Linux 容器）。

## 結論（暫時）

單一通用流程可行，Playwright 路線夠用於一般網頁。要不要因為 60fps 或高 DPI 改走 Xvfb，等真 Demo 實測後再決定。
