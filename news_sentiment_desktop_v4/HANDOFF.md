# 專案交接摘要(Claude Code 自動讀取)

新聞輿情系統 Desktop V4(PySide6 + Anthropic API)。功能與架構詳見 README.md。
本檔記錄 2026-07-03 與 Claude Code 協作的修改內容與待辦事項,供跨機器接續開發。

## ⚠ 換電腦必做(資料不在專案資料夾內!)

1. **應用程式資料**位於 `%APPDATA%\NewsSentimentDesktopV4\`(資料庫、Prompt 版本、
   設定、匯出檔),**不會**隨專案資料夾複製。要保留資料請把該資料夾也複製到
   新電腦的相同位置;不複製則等於全新開始(Prompt 修改版本 v5 也會遺失,
   見下方「Prompt 狀態」一節,可依說明重建)。
2. **API Key** 存在 Windows 認證管理員(keyring),不會跟著走,需在
   「系統設定 → Anthropic API」重新輸入。
3. 環境建置:`python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`
   → `.venv\Scripts\python -m playwright install chromium` → `python run_desktop.py --debug`。
   (或直接執行 run_desktop.bat)
4. 測試:`.venv\Scripts\python -m pytest tests -v`(目前 42 個全數通過)。

## 已完成的修改(本次協作)

1. **Playwright EPIPE 崩潰修正**(已在真實使用驗證):
   - `batch_job_worker.py` 新增 `cleanup_fn` 參數,於 `run()` 的 finally 在
     **worker 執行緒上**執行收尾(Playwright sync API 物件綁定建立執行緒,
     不可接 finished_job signal 清理——會排到主執行緒且屆時 worker 已結束)。
   - `scraping_worker.py` 瀏覽器關閉改走 cleanup_fn。
   - `playwright_scraper.py` 跨執行緒關閉失敗時改以 `taskkill /T` 終止 driver 程序樹。
2. **Prompt 編輯器全面升級**(`settings_page.py`):
   - 新增 Tool Schema(JSON)編輯區(含格式驗證)——schema 的 required 決定
     模型必填欄位,prompt 文字無法覆蓋它,這是先前「改 prompt 但 Word 沒變」的根因。
   - 版本歷史下拉+「啟用此版本」;佔位符缺漏警告;「儲存後需重跑對應步驟」提示。
3. **Word 匯出版型**(`word_exporter.py`):
   - 引用新聞清單標題預設摺疊(w15:collapsed);新聞標題本身為超連結,不另列網址。
   - 全文標楷體(含 w:eastAsia 中文字型,標題/清單樣式一併套用);
     預設值與使用者 DB 設定均已改為標楷體。
   - 補輸出 key_actors(主要行動者與發言)與 possible_impact(先前有存但沒印)。
   - 移除「150 字摘要:」等標籤前綴,只印內容;空欄位自動省略。
   - 「主要論述與立場」標題下 = 行動者清單(每位一行,含編號自動斷行)+ 立場條目。
4. **模型輸出清洗**:`text_utils.strip_model_artifacts()` 清除滲漏的 XML/工具標記
   (如 `</summary_150>`、`<parameter name=...>`)與字面 `\n`,已套用於
   `model_gateway` 全部三個輸出路徑(tool use / json_mode / call_text)。
5. **摘要 180 字限制**:prompt 要求 + `topic_analysis_worker` 落庫前
   `truncate_at_sentence(輸入, 180)` 句尾截斷保險。
6. **議題調整頁正文編輯**(`topic_adjustment_page.py`):右側正文可直接編輯,
   儲存後狀態設為成功(正文不足的新聞人工補完即可進綜整),記 feedback log
   (action=human_edit_body)。

## Prompt 狀態(存於 %APPDATA% 資料庫,不在程式碼內)

- 使用者的 `topic_summarization` 啟用版本為 **v5**:模板以欄位名稱明確對應
  (summary_150 必填不可留空、key_actors 每位一行、其餘欄位可留空)。
  v4 曾因模板只寫「摘要」未指名欄位,導致 4/7 議題 summary_150 空白。
- 程式內建預設 prompt(`app/prompts/summarization_prompt.py`)已同步加入
  180 字限制與 key_actors 換行要求。

## 待辦事項(依優先序)

1. **【驗證】v5 prompt 是否解決摘要空白**:重跑步驟 6 綜整後檢查
   `topics.summary_150` 不再為空且 ≤180 字。
2. **【已規劃待核可】留用初判大量「模型未回傳判斷(保守保留)」**:
   推測原因 = retention 預設 max_tokens=1024、每批 20 則,輸出被截斷。
   計畫:(a) 先確診 log 中 stop_reason 是否 max_tokens、缺漏是否集中批次尾端;
   (b) max_tokens 依批次大小動態計算;(c) 回傳缺漏的項目自動小批補判,
   「保守保留」降為最後防線;(d) mock 測試。
3. **【已確診待核可】付費牆誤判**:現行邏輯「整頁 HTML 含關鍵字即放棄」,
   已實測 2/4 誤判(中時評論、經濟日報均免費可讀,被頁面訂閱按鈕/推廣浮塊誤觸)。
   計畫:改為先擷取正文,成功且夠長(≥200字)即成功;僅擷取失敗或異常短時
   才檢查「正文區文字」是否含付費牆關鍵字,並把命中關鍵字寫進失敗原因。
   `body_scraper.py` 與 `playwright_scraper.py` 兩路徑都要改+測試。
4. **真付費牆(鏡週刊等)**:若單位有訂閱帳號,可做「站點登入 cookie」功能;
   無帳號則維持現行(保留 Excel 摘要)。待使用者決定。
5. README 既有未實作項目:Message Batches API 輪詢、完整多欄 Kanban、
   PyInstaller 實機打包。

## 其他備註

- 任務模型:topic_summarization 目前設為 claude-sonnet-5(使用者自行調整過);
  gateway 已自動學會該模型不支援 temperature(執行期快取,重啟後會再學一次,無害)。
- 開發流程慣例:改動後跑 `pytest tests`;修 bug 先讀 `%APPDATA%\...\logs\app.log`;
  使用者偏好「先討論方案、核可後才動手」。
