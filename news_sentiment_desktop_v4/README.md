# 新聞輿情系統 Desktop V4.0（Claude 版）

Windows 原生桌面應用程式，使用 **PySide6** 建置，整合 **Anthropic Claude API**
完成新聞匯入、留用判斷、正文抓取、議題分群、人工調整、議題綜整、立場分析、
Word 早報匯出，以及回饋／案例／規則庫管理的完整輿情工作流程。

---

## 1. 安裝與啟動

### 需求
- Windows 10 / 11
- Python 3.10 ～ 3.12（需可從命令列執行 `python`）
- 一組 Anthropic API Key（於 https://console.anthropic.com 取得）

### 啟動方式

1. 將整個 `news_sentiment_desktop_v4` 資料夾複製到本機任意位置。
2. 直接雙擊 **`run_desktop.bat`**（第一次執行會自動建立虛擬環境 `.venv`
   並安裝 `requirements.txt` 內的套件，之後啟動即可，過程可能需要幾分鐘）。
3. 若需要查看詳細除錯訊息（例如 API 呼叫失敗、抓取失敗等），改用
   **`run_desktop_debug.bat`**，錯誤訊息會顯示在該視窗中，並同時寫入
   `%APPDATA%\NewsSentimentDesktopV4\logs\app.log`。
4. 開啟後，先進入「系統設定」分頁輸入並測試 Anthropic API Key，再依左側
   工作流程導覽（1→9）依序操作。

### 資料存放位置
所有資料庫、log、匯出檔案、Prompt 版本歷史都存放在：
```
%APPDATA%\NewsSentimentDesktopV4\
├── news_sentiment.db      # 主資料庫（新聞、議題、立場、回饋、規則、Prompt、工作佇列）
├── logs\app.log            # 應用程式 log（絕不含 API Key 明碼）
├── exports\                # Word 早報預設輸出位置
└── prompt_versions\        # Prompt 版本備份
```
API Key 本身**不會**存在上述資料夾的一般檔案中，而是透過 `keyring` 套件寫入
**Windows 認證管理員（Credential Manager）**，底層由作業系統以 DPAPI 加密。

---

## 2. 專案結構

```
news_sentiment_desktop_v4/
├── app/
│   ├── ui/                # PySide6 介面（main_window + pages + widgets）
│   ├── controllers/        # AppContext：組裝 Repository / ModelGateway 的根
│   ├── services/
│   │   ├── ai/              # ModelGateway、模型能力設定
│   │   ├── importer/        # Excel/CSV 匯入
│   │   ├── retention/       # 留用初判
│   │   ├── scraping/        # 正文抓取
│   │   ├── clustering/      # 議題分群
│   │   ├── summarization/   # 議題綜整（含 map-reduce）
│   │   ├── stance/          # 立場分析
│   │   └── feedback/        # 回饋 log / 規則草案
│   ├── repositories/       # SQLite 存取層（news / topic / job / prompt / settings ...）
│   ├── models/              # dataclass 資料模型
│   ├── workers/             # QThread 背景工作（進度/取消/續跑）
│   ├── prompts/             # 各任務預設 Prompt + Tool Schema + 版本管理
│   ├── exporters/           # Word 早報匯出（python-docx）
│   └── utils/                # 路徑、log、安全金鑰儲存、文字工具
├── tests/                   # pytest 自動化測試
├── data/                     # （保留給未來擴充；使用者資料一律存於 %APPDATA%）
├── run_desktop.py            # 程式進入點
├── run_desktop.bat
├── run_desktop_debug.bat
└── requirements.txt
```

架構採分層設計：**UI → Controllers(AppContext) → Services → Repositories → SQLite**，
所有 Anthropic API 呼叫皆經由唯一的 `ModelGateway`（`app/services/ai/model_gateway.py`），
沒有任何頁面直接呼叫 API。

---

## 3. 本版本的完成度與誠實範疇說明

依規格書第七項「不可用先做假功能、再說已完成的方式交付」，以下誠實列出目前狀態：

### 已完整實作並可執行
- Excel（多工作表）／CSV 匯入、欄位自動對應、KEYPO 常見欄位別名、row_id 恆唯一、
  重複資料偵測、匯入摘要統計。
- 留用初判 AI 批次呼叫（Tool Use 結構化輸出）、單批失敗回退、人工覆蓋、
  QAbstractTableModel 局部更新（勾選不跳回首列、不整表重載）。
- 正文抓取：JSON-LD articleBody 優先、主文容器辨識、延伸閱讀停止標記、
  robots.txt 檢查、付費牆關鍵字偵測、每網域限速、403/404/5xx 分類、
  失敗不覆蓋既有摘要。
- 議題分群：候選分桶 → AI 分批分群 → 跨批次合併 → 正文不足者不強行併入。
- 人工議題調整：建立新議題、加入既有議題、標示不納入、拆分、改名、合併、
  刪除空議題，並即時寫回 `final_topic_id` 與 feedback log。
- 議題綜整（含超長正文的 map-reduce 分段流程）與立場分析（固定三類、
  僅在有明確立場時才標記 `has_identifiable_stance`）。
- 回饋 log／案例庫／規則草案三層資料模型、AI 規則草案生成、
  人工採用／編輯／停用／刪除。
- Word 早報匯出（python-docx，含 Logo／頁首頁尾／字型／段落間距等樣式設定）。
- Job / Batch 狀態機（pending/running/completed/failed/cancelled/retryable）
  搭配 SQLite 持久化，支援可續跑（重新啟動後，尚未完成的批次會被偵測並繼續）。
- ModelGateway：Tool Use 結構化輸出、json_mode 備援、指數退避重試、
  錯誤分類（authentication/rate_limit/overloaded/invalid_request/timeout/other）、
  模型參數相容性過濾（`model_capabilities.py`，避免對不支援 extended thinking
  的模型送出 `thinking` 參數）。
- API Key 加密儲存（Windows Credential Manager / DPAPI，透過 `keyring`）、
  遮罩顯示、一鍵清除、連線測試。
- Prompt 版本管理：可編輯、還原預設、版本歷史（每次儲存為新版本並保留舊版）。
- pytest 自動化測試（見下方第 5 節），且已在開發過程中以 mock 方式手動驗證過
  匯入、資料庫寫入、Word 匯出、正文擷取、ModelGateway 重試與模型相容性等核心
  邏輯確實可正確執行（非僅語法通過）。

### 本版本的簡化範疇（誠實標示，非隱藏）
- **人工議題調整介面**：規格要求「可視化 Kanban／拖曳式工作區」。本版本以
  「未分類清單 ↔ 目前選定議題成員清單（雙向拖曳，拖放後即時寫回資料庫與
  feedback log）+ 明確操作按鈕（建立/加入/拆分/合併/改名/刪除）」的三欄
  設計實作，功能完整對應規格十的 8 項操作，但**尚非同時顯示所有議題欄位的
  完整多欄 Kanban 視覺呈現**。如需完整多欄 Kanban，建議下一階段再擴充
  `topic_adjustment_page.py`。
- **Message Batches API**：設定頁已提供開關與資料模型欄位，`ModelGateway`
  目前預設走即時 Messages API；Batches API 的非同步輪詢流程（送出→輪詢→
  取回結果）尚未實作，屬於下一階段工作項目，已在程式碼與本 README 中如實
  標示，不會假裝已支援。
- **DPAPI／Credential Manager**：實作透過 `keyring` 套件（Windows 上其
  backend 即為 Credential Manager，底層 DPAPI 加密），這是業界標準做法且
  符合規格用詞；但由於本開發環境為 Linux 沙盒，`keyring` 在 Windows 上的
  實際行為**請您在拿到專案後於 Windows 環境親自驗證一次**（呼叫「測試連線」
  與「儲存/清除 Key」）。
- **PyInstaller 打包 exe**：`requirements.txt` 已包含 `pyinstaller`，但本
  沙盒無 Windows 環境可實際打包驗證，未附打包好的 `.spec` 設定檔。建議在
  確認功能無誤後，於 Windows 上執行：
  ```
  pyinstaller --name NewsSentimentDesktopV4 --windowed --onefile run_desktop.py
  ```
  再依實際需要調整 `--add-data`（例如 prompts 預設資料）。
- **本沙盒開發環境沒有 Windows、沒有網路、也未安裝 PySide6 / anthropic /
  keyring / pytest**，因此：
  - 所有檔案已通過 `python -m py_compile` 語法檢查（80 個檔案全數通過）。
  - 匯入、SQLite 讀寫、正文擷取（bs4）、Word 匯出（python-docx）、
    ModelGateway 重試與模型相容性邏輯，已用可在本沙盒安裝的套件
    （`openpyxl`、`beautifulsoup4`、`python-docx`）搭配手動 mock 實際
    **執行驗證**，結果附於開發紀錄中，並非僅憑閱讀程式碼推測正確。
  - PySide6 介面本身（拖曳、勾選互動、視窗排版）**尚未在真正的 Qt 事件迴圈
    下人工操作驗證**，請您在 Windows 上啟動後，依左側 1→9 逐步操作一次，
    若發現任何介面互動問題，請回饋給我，我可以立即針對該頁面修正。

---

## 4. 系統設定建議

首次使用請依序完成：
1.「Anthropic API」分頁：貼上 API Key → 儲存 → 測試連線。
2.「任務模型設定」：確認留用初判＝Haiku、分群＝Sonnet、綜整/立場/規則＝Opus
   （可依需求調整）。
3.「正文抓取設定」：依需求調整每網域延遲秒數（預設 2 秒）。
4.「Word 輸出樣式」：可上傳機關 Logo、設定頁首頁尾文字與字型。

---

## 5. 執行測試

```bat
call .venv\Scripts\activate.bat
pytest tests -v
```

測試涵蓋（對應規格十七第 7 點要求項目）：
- `test_excel_import.py`：Excel 多工作表讀取
- `test_large_import.py`：5,000 則匯入 + 重複 news_id／相同資料列處理
- `test_retention_table_model.py`：留用勾選不跳回首列、不整表重載
- `test_scraping.py`：正文抓取狀態保存、失敗不覆蓋既有摘要
- `test_ai_failure_fallback.py`：AI 失敗回退（認證錯誤不重試、限流錯誤重試後
  耗盡、單批失敗只影響該批）
- `test_topic_adjustment.py`：議題移動／合併／拆分，不留重複歸屬或幽靈資料
- `test_word_export.py`：Word 匯出（含「無明確立場時不顯示立場區塊」）
- `test_model_gateway_compatibility.py`：ModelGateway 對不同模型（haiku/
  sonnet/opus）的參數相容性處理（mock Anthropic API 回應）

需要 PySide6 事件迴圈的測試（`test_retention_table_model.py`）使用
`qapp` fixture，若環境未安裝 PySide6 會自動 `skip` 而非失敗。

---

## 6. 資料升級與既有資料保護

`app/repositories/db.py` 內建 `schema_version` 機制：程式啟動時只會
`CREATE TABLE IF NOT EXISTS` 與必要的 `ALTER`，絕不 `DROP` 既有資料表；
未來若需升級資料格式，請在 `_run_migrations()` 中新增對應的遷移步驟。

---

## 7a. 網頁版（部署到 Render）

除了 PySide6 桌面版，本專案另外提供一個**網頁版**（`app/web/`），適合團隊共用：
不同人打開同一個網址即可操作，資料共用同一顆 SQLite DB。網頁版的範圍是「Gmail
匯入 → 抓正文 → AI 留用初判 → AI 議題分群 + 人工調整 → 下載 Word 新聞議題清單」，
**不含**桌面版的議題綜整（摘要）與立場分析。所有步驟都要使用者手動按按鈕觸發，
沒有背景自動排程。

### 與桌面版的差異

| | 桌面版 | 網頁版 |
|---|---|---|
| UI | PySide6 視窗 | 瀏覽器（Flask） |
| 新聞來源 | Excel/CSV 匯入 或 Gmail | 只有 Gmail |
| AI 分析範圍 | 完整 9 步（含綜整、立場分析） | 留用判斷 → 分群 → 人工調整 |
| 匯出 | 完整早報（含摘要/立場） | 簡易議題清單（`export_simple_topic_list`） |
| 帳號 | 單機單人 | 共用密碼（無個別帳號） |
| Gmail OAuth | Desktop App 類型，本機開瀏覽器 | Web Application 類型，固定 redirect URI |
| 秘密儲存 | keyring（Windows Credential Manager） | 部署平台環境變數 |

網頁版與桌面版共用同一套 `app/services`／`app/repositories`／`app/models`／
`app/prompts`／`app/exporters/word_exporter.py`，AI 判斷/分群邏輯完全不重寫。

### 本機測試網頁版

```bash
pip install -r requirements-web.txt   # 只測網頁版用這個較輕量的清單即可
                                        # （已裝過桌面版 requirements.txt 也可以，是超集）
export WEB_SHARED_PASSWORD=your-password
export FLASK_SECRET_KEY=any-random-string
export ANTHROPIC_API_KEY=sk-ant-...
export GMAIL_OAUTH_CLIENT_ID=...        # 選填，測試 Gmail 連接才需要
export GMAIL_OAUTH_CLIENT_SECRET=...
python run_web.py
```
瀏覽器會自動開啟 `http://127.0.0.1:5000`。本機用 http 測試 Gmail OAuth 時，
`run_web.py` 已自動設定 `OAUTHLIB_INSECURE_TRANSPORT=1`（僅本機測試用，正式
部署一律走 https，不受影響）。

### 部署到 Render

1. **Google Cloud Console**：建立一組 **Web application** 類型的 OAuth 2.0
   Client（新聞輿情桌面版用的是 Desktop app 類型，網頁版不能沿用同一組）。
   Authorized redirect URI 填：`https://<你的 Render 服務網址>/gmail/oauth/callback`
   （服務網址要等下一步 Render 建立服務後才知道，可以先用預留網址建立、之後
   到 Google Cloud Console 補上正確網址）。
2. **Render**：用 `render.yaml`（Blueprint）建立服務（New → Blueprint，指向
   這個 GitHub repo）。注意 `render.yaml` 放在 `news_sentiment_desktop_v4/`
   子目錄下（repo 根目錄只有這個資料夾），render.yaml 內已設定
   `rootDir: news_sentiment_desktop_v4`，build/start 指令都會在該子目錄下
   執行；若 Render 介面要求指定 render.yaml 路徑，選
   `news_sentiment_desktop_v4/render.yaml`。Render 會建立一個掛了 1GB
   持久磁碟（`/var/data`）的 web service。
3. 在 Render Dashboard 的環境變數頁面填入（`render.yaml` 裡標示
   `sync: false` 的都需要手動填）：
   - `ANTHROPIC_API_KEY`
   - `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET`（步驟 1 建立的）
   - `WEB_SHARED_PASSWORD`（團隊共用的登入密碼）
   - `FLASK_SECRET_KEY` 由 Render 自動產生，不用手動填
4. 部署完成後，打開服務網址 → 輸入共用密碼登入 → 到「設定」頁按「連接
   Gmail」完成一次性 OAuth 授權、填寫寄件者信箱與主旨關鍵字 → 之後每天
   上首頁按「一鍵完成」（見下）或依序手動操作「匯入 → 抓正文 → 留用初判 →
   議題分群 → 匯出」。

### 一鍵完成

首頁提供「一鍵完成」表單（起訖時間 + 一個按鈕），背景依序自動跑完匯入→抓
正文→留用初判→議題分群，跑完直接導向議題分群頁做人工調整，不必依序手動點
四個步驟。進度條會顯示目前跑到哪個階段；任一階段失敗（例如 Gmail 找不到符
合條件的信件）會停在該階段並顯示原因，不會靜默卡住。

### 議題／關鍵字彙整表（提升 AI 判斷精準度）

設定頁新增一個文字區塊，可貼上業務關注議題與關鍵字對照表（可直接沿用
KEYPO 的布林檢索語法 `|`／`&`／`~N`）。這份清單不會在程式端做關鍵字比對
解析——來源常有人工謄寫的不平衡括號、不一致分隔符號，硬解析容易悄悄出錯；
而是原文注入留用初判與議題分群的 AI prompt，讓模型參考語意判斷，兩者共用
同一份設定（`app/web/routes/retention.py::build_keyword_context()`）。

### 議題分群頁的「未留用新聞」欄位

分群頁左側除了「未分類新聞」，另外還有一欄「未留用新聞」（AI 或人工判斷為
不留用者），可直接拖曳搶救到未分類或某個議題（拖曳時會自動把該則新聞的
留用狀態改回留用），不必先跳回留用初判頁勾選再回來分群。反向拖到「未留用」
欄位則會標記為人工不留用並移出所屬議題。

### 已知限制（有意簡化，非疏漏）

- 只支援單一 instance（`-w 1`）：SQLite 檔案鎖 + 單一持久磁碟不支援水平擴展，
  小團隊內部工具用途足夠。
- 不做 Playwright 瀏覽器渲染抓取（雲端 instance 較輕量），只做
  `requests + BeautifulSoup` 一段式抓取——這也是為什麼部署用
  `requirements-web.txt`（不含 PySide6/Playwright/GNE/PyInstaller）而不是
  桌面版的完整 `requirements.txt`：Render 的無頭建置環境沒有這些套件需要的
  GUI/瀏覽器系統函式庫，硬裝只會讓 build 失敗。
- 只有一組共用密碼，沒有個別帳號與操作紀錄歸屬。

### Build 失敗排查

- **`Exited with status 1 while building your code`**：先看 Render 的 build log
  是哪個套件裝到一半失敗（通常是誤用了完整的 `requirements.txt`，或
  `render.yaml` 沒有生效導致用了預設的 build 指令）。確認 Render 服務設定裡
  的 Build Command 是 `pip install -r requirements-web.txt`（不是
  `requirements.txt`），且 Root Directory／`rootDir` 有指到
  `news_sentiment_desktop_v4`。若是舊的 Blueprint（在 `rootDir` 加入前建立
  的），到 Render Dashboard 手動把 Root Directory 改成
  `news_sentiment_desktop_v4`，或刪掉服務用最新的 `render.yaml` 重新建立
  Blueprint。

---

## 8. 更新紀錄

### v4.2.0（本次版本）
兩大方向六項功能，目標是降低第五步人工負擔、提升爬取層可靠度。

**一、減少人工調整分群的負擔**
1. **低信心清單**：分群 Prompt 要求模型誠實給信心分數（明確 0.85+、不確定
   0.7 以下），逐則落庫（含合併分支，修正先前合併路徑漏寫信心的問題）。
   議題調整頁中低信心新聞以淡黃底＋「⚠ [信心 x.xx]」標示，並提供
   「只顯示低信心新聞」勾選與統計數字——編輯只需優先確認標黃項目，
   不必逐篇檢查。人工指定歸屬後信心歸零、標記自動消失。
2. **人工調整回饋閉環（few-shot）**：分群時自動讀取回饋 log 中的人工分群
   修正（拖曳改群、合併等），組成最多 10 條「新聞《標題》AI 原歸 A →
   人工改為 B」範例注入 prompt，讓模型學習編輯的歸類偏好，無需 fine-tuning。
3. **增量分群**：分群頁新增「增量分群」勾選（偵測到既有議題時自動預設勾選）。
   啟用時只處理尚未歸入議題的新聞，並把既有議題（含範例標題）注入 prompt，
   要求模型優先歸入既有議題（直接沿用 topic_id），真正的新事件才建新議題
   ——每天例行跑批時，人工確認過的議題結構不會被重跑打散。取消勾選則
   維持原本的全量重新分群。

**二、爬取層可靠度**
4. **站點成功率儀表板**：新增 scrape_stats 資料表逐站點記錄成功率、平均
   耗時、最後成功時間、連續失敗次數。抓取頁改為雙分頁（本次結果／儀表板），
   連續失敗 ≥3 次的站點整列標紅並於頁面頂部主動警示「可能已改版或封鎖」，
   不必等編輯發現摘要品質下降才回頭查。統計於「清除已匯入新聞」時一併重置。
5. **正文品質檢查**：抓取成功後檢查（a）字數異常短（<80 字）（b）正文與
   標題無任何關鍵字重疊（CJK 雙字詞比對，標題過短不誤殺）。命中者標記為
   新狀態「可疑」——正文保留供人工檢視，但**不會進入分群與綜整**，避免
   抽錯的 cookie 聲明、廣告文字汙染摘要；可疑亦計入站點失敗統計。
6. **站點專屬 selector**：設定新增 site_selectors（domain → CSS selector），
   requests+BeautifulSoup 命中時直接抽主文（含停止標記截斷與字數門檻），
   省下 Playwright 啟動成本；未命中自動回退通用擷取 → 瀏覽器渲染。
   內建三立（#Content1）、民視（#newscontent）、鏡週刊（article）預設值
   ——**此為依常見版型的推測值，實際命中率請以儀表板觀察後調整**
   （設定檔 %APPDATA% 中 settings 的 scraping.site_selectors）。

相容性：使用者自訂過的分群 Prompt 模板若沒有新佔位符
（{existing_topics_section}／{human_examples_section}），以 safe_format
處理不會出錯，只是不注入新區塊；預設模板經自動升級機制生效。
全部六項功能均以 mock／實際 SQLite 驗證通過（增量沿用議題、few-shot 注入、
可疑排除與統計、selector 擷取、連續失敗警示與歸零）。

### v4.1.7（本次修正版）
修正步驟七「回饋與規則草案」無法產生有效規則的問題。三個原因與對應修正：
1. **雜訊淹沒訊號**：留用初判時每則新聞都會記一筆 AI 自身判斷（ai_judge）
   回饋，全部餵給模型導致真正的「人工修正」被淹沒。現在只送人工修正紀錄
   （action 以 human_ 開頭或有人工最終值者），上限最近 300 筆；若完全沒有
   人工修正紀錄，頁面會明確提示先到留用頁／議題調整頁累積修正，不再空轉
   呼叫 API。
2. **Payload 過大**：ai_original_value 原本送出完整 JSON 全文，現在各欄位
   截斷至 120 字元，只保留歸納所需資訊。
3. **無效草案未過濾**：模型回傳缺 name 或 rule_text 的項目原本照存，現在
   一律略過並記錄警告，清單中只會出現可用的規則。
另強化規則草案 Prompt：給出四類具體規則範例（留用排除／保留、議題合併、
命名規範）、要求 rule_text 為可直接執行的指令句、每條規則至少 2 筆紀錄
支持、找不到模式時回空不硬湊。預設 Prompt 自動升級機制會讓新 Prompt
在未經人工修改的情況下自動生效。已以「93 筆 ai_judge 雜訊 + 3 筆人工修正」
的 mock 情境驗證：過濾、截斷、無效草案略過、純雜訊時不呼叫 API 均正確。

### v4.1.6（本次修正版）
修正大型議題綜整持續逾時的問題（timeout ×5 後該議題失敗）。
原因：新聞量大的議題交由 Opus 綜整時，單次請求的生成時間超過連線逾時上限，
非串流模式下整個請求被切斷（Anthropic 官方明確建議 long requests 應使用
streaming）。修正：`ModelGateway` 所有請求改為**優先使用串流模式**
（`messages.stream` + `get_final_message()`），逐段接收回應、讀取逾時以
每段計算，長時間生成不再被整段切斷；SDK 過舊不支援串流時自動回退為
非串流呼叫。已驗證：串流優先、舊 SDK 回退、串流模式下參數自癒（temperature
剝除重送）仍正常運作。對呼叫端完全透明，回傳結構不變。

### v4.1.5（本次修正版）
1. **Playwright atexit 安全網**：新增全域活動實例登錄，Python 程序結束時
   （包含未預期例外導致的異常結束路徑）強制關閉所有存活的瀏覽器與 driver，
   補上「程式異常結束時瀏覽器未收乾淨 → 殘留 Node driver 在管線斷開後
   送事件 → EPIPE 未處理例外」的最後一條路徑（堆疊特徵：
   BrowserContextDispatcher.sendEvent）。
2. 包含 v4.1.4 全部內容（模型輸出防禦性驗證，修正
   `'str' object has no attribute 'setdefault'` 分群崩潰）。

### v4.1.4（本次修正版）
修正議題分群崩潰：`'str' object has no attribute 'setdefault'`。
原因是模型輸出未完全遵守 Tool Use schema（topics 陣列中出現字串而非物件，
json_mode 降級備援時尤其可能發生），程式直接以物件方式操作導致崩潰。
修正方式：對所有「解析模型結構化輸出」的位置加入防禦性驗證與正規化——
分群（cluster_batch）、跨批次整合（merge_candidate_topics）逐筆檢查型別、
正規化成員清單（字串自動轉單元素列表）、略過無成員項目並記錄警告；
留用初判、立場分析、規則草案同樣過濾非物件項目。不合格式的個別項目
只會被略過，不再讓整批工作崩潰。已以混雜格式（字串／缺成員／字串成員／
正常物件）的 mock 輸出驗證通過。

### v4.1.3（本次修正版）
1. **API 參數棄用自癒機制**（修正 `temperature is deprecated for this model`
   造成分群全批失敗的問題）：
   - `ModelGateway` 收到 400 且訊息指出某已送參數 deprecated / not supported
     時，自動記錄「該模型不支援該參數」至執行期能力快取，剝除該參數後立即
     重送；後續同模型的所有呼叫直接不送該參數，不再重複失敗。
   - `invalid_request_error`（400）不再進入一般重試迴圈——請求本身不合法，
     重試結果不會改變，原本每批白白重試 5 次、空等約 30 秒的問題已消除。
   - 非參數類的 400（如 schema 錯誤）不會被誤判為參數問題，直接回報。
   - map-reduce 中間摘要改經由新的 `gateway.call_text()`，同樣受自癒保護
     （原本直接呼叫 client，會漏接此防護）。
   - 全部行為（偵測、剝除重送、學習快取、不重試）均以 mock 驗證通過，
     新增 `tests/test_param_self_healing.py`。
2. **留用初判調整（原本太保守、幾乎全留）**：預設 Prompt 改為對娛樂八卦、
   股市、促銷工商稿、生活消費、體育賽事、重複稿等明確無公共性內容
   「果斷建議不留用」；僅在內容確實可能涉及公共事務但資訊不足時才保留待確認，
   並明確以「是否需要出現在輿情早報」為判斷標準。
3. **議題分群調整（原本拆太細）**：
   - 分群 Prompt 加入數量級距指引（10～20 則通常僅 2～5 個議題；議題數
     超過新聞數三分之一即為拆分過度）與「懷疑相關時一律先合併」原則。
   - 跨批次整合 Prompt 改為「積極合併」導向，並補上每個候選議題的
     範例新聞標題（原本只送議題名稱＋數量，資訊不足導致難以正確合併）。
4. **預設 Prompt 自動升級**：啟動時若某任務目前啟用的 Prompt 仍是
   「未經人工修改的系統預設」，且程式內建預設已更新，會自動寫入新預設為
   新版本並啟用（版本歷史保留）；**使用者自行修改過的 Prompt 完全不受影響**。
   已驗證：升級、不重複升級、使用者版本保留。

### v4.1.1（本次修正版）
針對 Windows 上 Playwright 驅動 EPIPE 崩潰（`Error: EPIPE: broken pipe`，
堆疊特徵 RouteDispatcher / _requestInterceptor，驅動 Node 程序死亡導致後續
瀏覽器渲染全數失敗、程式結束後仍拋出未處理例外）的根本修正與多層防護：
1. **消除崩潰根源**：資源阻擋不再使用 `page.route()` 請求攔截（攔截器會在
   瀏覽器關閉／程式結束階段對已斷開的 driver 管線寫入，正是 EPIPE 來源），
   改用 Chromium 啟動參數 `--blink-settings=imagesEnabled=false` 停用圖片
   載入，同樣達到加速渲染效果但無攔截器生命週期問題；context 另設定
   `service_workers="block"` 減少背景連線造成的關閉競態。
2. **崩潰自動重啟**：渲染時偵測到 EPIPE／broken pipe／connection closed／
   target closed 等驅動死亡特徵時，自動完整重啟 Playwright（每則新聞最多
   重啟一次，避免無限循環），重啟成功後以新瀏覽器重試該則新聞。
3. **定期回收**：每抓取 20 則（可調，`recycle_every`）自動重啟瀏覽器，
   預防驅動長時間累積狀態導致崩潰。
4. **關閉流程強化**：page → context → browser → driver 逐層關閉、每層獨立
   防護，並加入短暫緩衝讓 driver 送完未竟訊息。
5. **程式退出最後防線**：主視窗 `closeEvent` 呼叫抓取頁 `shutdown_cleanup()`，
   先取消進行中的抓取工作再確保瀏覽器與 driver 關閉，避免程式結束後殘留
   Node 程序崩潰。
6. **truststore 支援**：`requirements.txt` 加入 `truststore`，抓取模組啟動時
   自動注入，讓 Python 的 SSL 驗證改用作業系統憑證庫——公司代理/防火牆的
   TLS 檢查憑證通常已在 Windows 憑證存放區，注入後 requests 即可正常信任，
   比停用 SSL 驗證安全。
7. **SSL 錯誤納入瀏覽器渲染 fallback 觸發條件**：SSL 憑證錯誤屬憑證信任
   問題（Chromium 使用系統憑證庫可正常連線），非反爬蟲規避，故允許升級
   以瀏覽器渲染重抓；robots/付費牆/403/逾時仍不觸發。
崩潰特徵偵測、自動重啟、定期回收邏輯均已以 mock 驗證通過。

### v4.1.0
1. **新增瀏覽器渲染備援抓取（Playwright + GNE）**：
   - 新增 `app/services/scraping/playwright_scraper.py`：以 Playwright（sync API，
     適配 QThread）啟動無頭 Chromium 渲染 JS 後，交由 GNE 自動擷取中文新聞正文，
     適用於鏡週刊、三立新聞網、民視新聞等 requests 抓不到主文的 JS 渲染型網站。
   - **兩段式策略**：一律先走原有的快速抓取（requests + BeautifulSoup），
     只有在失敗原因為「無法辨識乾淨主文容器」時才升級用瀏覽器渲染重抓；
     robots.txt 禁止、付費牆、403、SSL、逾時等**合規性或連線層失敗不會觸發**
     第二段（不做反爬蟲規避）。瀏覽器渲染結果仍套用延伸閱讀停止標記截斷、
     付費牆偵測、每網域限速與字數/品質檢查。
   - 成功者 `body_source` 標記為「網頁抓取正文（瀏覽器渲染）」以供追溯；
     兩段皆失敗時，詳細原因會同時保留兩段的失敗訊息。
   - 於「系統設定 → 正文抓取設定」以開關啟用（預設關閉）；瀏覽器逾時秒數
     可調；GNE 雜訊節點（noise_node_list）為可設定值（per-domain）。
   - 瀏覽器實例整批共用、首次需要時才啟動（延遲啟動）、工作結束自動關閉；
     playwright/gne 未安裝或 Chromium 未下載時優雅降級為僅第一段抓取並記錄警告。
   - `run_desktop.bat` 首次啟動會自動執行 `playwright install chromium`
     （約 150MB，一次性；按 Ctrl+C 略過也不影響其他功能）。
2. **新增「停用 SSL 憑證驗證」選項**（系統設定 → 正文抓取設定）：
   公司網路代理/防火牆進行 TLS 檢查的環境會導致大量「SSL 憑證錯誤」，
   可暫時停用驗證（有安全風險，預設維持開啟）；SSL 錯誤訊息現在會附上
   此解決指引。
3. 新增 `tests/test_browser_fallback.py`（fallback 觸發條件：內容擷取失敗
   才升級、合規性失敗不升級）。
4. `requirements.txt` 加入 `playwright`、`gne`。

### v4.0.1
1. **修正拖曳未持久化的 bug**：議題調整頁原本兩清單間拖曳只有視覺效果、
   不寫回資料庫。新增 `DropListWidget`（`app/ui/widgets/drop_list_widget.py`），
   拖放完成後即時更新 `final_topic_id` 並記錄 feedback log；拖回未分類清單
   則清除歸屬。
2. **正文抓取錯誤分類補齊**：SSL 憑證錯誤、逾時、連線失敗現在各自有明確的
   失敗原因文字（原本籠統歸為「連線錯誤」）。
3. **Tool Use 失敗自動降級**：`ModelGateway.call_with_tool` 在模型多次未依
   Tool Use 回傳結構化資料（parse_error）時，自動降級改用 json_mode
  （system prompt 強制只回傳 JSON + 應用層嚴格解析）備援一次，仍失敗才回報
   錯誤（`stop_reason` 會標示 `json_mode_fallback` 以供追溯）。
4. **啟動時主動偵測可續跑工作**：主視窗啟動時檢查資料庫內未完成的工作，
   彈出提示並列出各工作進度；使用者到對應頁面再按執行即可續跑。
5. **續跑批次對齊修正**：`BatchJobWorker` 續跑時改以資料庫內既有批次的
   item_ids 重建批次內容，確保與上次的 batch_index 完全對齊，不受本次
   清單順序或批次切法影響；若批次內新聞已不在目前清單（例如被改為不留用），
   該批自動標記完成略過。
6. **留用初判頁新增「重試失敗批次」按鈕**，並在按下「執行 AI 留用初判」時
   自動偵測並接續上次未完成的同類型工作。
7. 修正主視窗初始化順序（status_bar 需先於中央區建立）；續跑偵測改為視窗
   顯示後才彈出。
8. 新增 `tests/test_job_resume.py`（續跑批次重建對齊、list_resumable 過濾）。
9. `run_desktop.bat` / `run_desktop_debug.bat` 改為純 ASCII 內容，
   修正中文 Windows 環境下的批次檔編碼錯誤；`requirements.txt` 放寬
   PySide6 版本上限至 `<7.0` 以支援較新的 Python 版本。

### 已知未實作項目
- Message Batches API 輪詢流程
- 完整多欄 Kanban 視覺呈現
- PyInstaller 實機打包驗證
- PlaywrightScraper 未在真實 Windows + Chromium 環境實測（邏輯層的 fallback
  觸發條件已通過測試；實際渲染效果請以鏡週刊/三立/民視等網站驗證）


---

## 7. 後續建議優先處理項目

1. 在真正的 Windows + PySide6 環境完整跑過一次九步工作流程，回報任何
   介面操作上的問題（尤其是拖曳與勾選互動）。
2. 視實際新聞量與預算，評估是否要把留用初判／議題分群改為 Message Batches
   API（目前為架構預留、尚未串接輪詢流程）。
3. 若需要更接近規格描述的多欄 Kanban 視覺效果，可在
   `app/ui/pages/topic_adjustment_page.py` 基礎上擴充為多個並排的議題欄。
4. 以真實 KEYPO／Excel 匯出檔案實際測試欄位對應是否需要新增別名
   （`app/services/importer/excel_importer.py` 的 `FIELD_ALIASES`）。
