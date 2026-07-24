# 開發指南（DEVELOPMENT_GUIDE.md）

> 給下一個開發對話（Claude）與維護者的完整交接文件。
> 最後更新：2026-07-24。開新對話時請先完整讀完本檔，再開始動工。

---

## 1. 專案是什麼

內政部（MOI）新聞輿情工作流程系統，單一 repo、兩套 UI 共用同一層業務邏輯：

- **桌面版**（主要）：PySide6，Windows，使用者為內政部同仁
- **網頁版**：Flask（`app/web/`），部署 Render，功能較窄（匯入→抓取→留用→分群→簡易清單匯出）

流程：Gmail 匯入（網路＋報紙監測報告）→ AI 留用初判 → 正文抓取 → AI 議題分群 → 人工調整（含拖曳排序）→ AI 議題綜整＋立場分析 → Word 晨會報告匯出。回饋閉環：人工修正會記 feedback log，組成 few-shot 注入 prompt。

架構分層與細節見 `CLAUDE.md`（每次對話自動載入）；本檔聚焦「現在做到哪、怎麼繼續」。

## 2. 協作方式（重要，照做）

- **使用者的電腦無法直接存取**。程式碼流動：我改 → commit → **push 到 master** → 使用者用 GitHub Desktop **Pull** → 在 Windows 實測 → 貼 log／截圖回饋。
- 同時維護分支 `claude/record-completeness-check-wsyw03`：每次 push master 後 merge master 進該分支並 push（使用者已授權直接進 master）。
- **絕不**請使用者在對話中貼 API Key。金鑰一律在他的電腦上輸入（設定頁→Windows 認證管理員）。
- 使用者以繁體中文溝通，非工程背景：回覆要有明確步驟（Pull → 重開 → 按哪顆按鈕）、解釋「為什麼」，錯誤訊息要翻成中文行動指引。
- 使用者回報問題的形式多半是「一句話＋log 貼文／截圖」。log 位置：`%APPDATA%\NewsSentimentDesktopV4\logs\app.log`。
- 修任何東西的節奏：**改 → `cd news_sentiment_desktop_v4 && QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q` 全綠（目前 270 個）→ commit（詳細訊息）→ push master → merge 分支**。UI 改動另用 offscreen 渲染截圖驗證（見 §6）。
- 沙盒陷阱：Bash 的 cwd 會不定時跳回 repo 根目錄——每個指令前加 `cd /home/user/-news-sentiment/news_sentiment_desktop_v4`，且**不要**把 pytest 和 commit 用 `&&` 串在一起賭 cwd（曾經 pytest 因 cwd 錯誤沒跑就 commit 了）。

## 3. 目前狀態（2026-07-24）

### 已完成並實測過的主要功能
- **雙供應商**：`settings.api.provider` 決定走 `ModelGateway`（Anthropic）或 `OpenAIGateway`（`app/services/ai/openai_gateway.py`），公開介面完全相同。跨家模型 ID 雙向自動落回預設模型。使用者目前用 **OpenAI（gpt-5.5）**，因 Anthropic 帳戶餘額用完。
- **報紙監測報告匯入**：同寄件者（競業信息 xkm_cs@xkd.com.tw）兩種版型，`gmail_report_parser.parse_report_html()` 自動判別。報紙版標題連結指向 XKM 剪報全文頁（免登入），站點 selector `rmbjbtw.rmb.com.tw → div.dataView` 抓全文。主旨關鍵字支援逗號分隔多組（兩種報告一次匯入）。
- **分群粒度**（fine/standard/coarse，設定頁下拉）注入分群＋跨批整合 prompt；**命名/合併回饋學習**：`human_rename`（topic_naming）與 `human_merge_topic` 現在會注入 few-shot（`clustering_service.build_combined_clustering_examples`）。
- **關鍵字對照表**（settings.keyword_taxonomy）桌面版已接進留用與分群 prompt（`app/services/taxonomy.py` 共用）。
- **議題拖曳排序**：`topics.display_order`（schema v4），調整頁中欄議題清單 InternalMove 拖曳，Word 匯出編號跟隨。
- **UI 主題**：`app/ui/theme.py`（淡色專業風、深藍側欄分組導覽、primary/danger 按鈕、下拉箭頭用 QPainter 產生的 PNG）。所有樣式集中此檔。
- **留用操作**：整格點擊切換、多選批次留用/不留用、空白鍵、預覽正文顯示層清理（clean_body_for_preview）。
- Gmail 設定頁內建 OAuth 申請教學（桌面＋網頁版）。

### 自癒／防護機制（改 gateway 時務必保留）
- 參數自癒：temperature 不支援、max_tokens vs max_completion_tokens（模組層級快取 `_TOKEN_PARAM`/`_NO_TEMPERATURE`）。
- **輸出截斷自癒**（兩家都有）：偵測 `finish_reason=length` / `stop_reason=max_tokens` → 額度×3 重試（上限 32k），學到的額度記 `_LEARNED_MIN_TOKENS[task]`。根因案例：使用者留用細評 max_tokens=1024、批次 20 則 → 整批默默變「不留用」。
- **留用防全滅**：`judge_batch` 漏判超過半批（批次≥4）→ 拋錯標整批失敗，不默默套後備值；粗篩全滅記 warning。
- **模型不存在自癒**（OpenAI）：404 model_not_found → 記 `_UNAVAILABLE_MODELS` → 改預設模型重試。
- 分群 0 議題三種情況都有明確畫面訊息（無可分群新聞／正文全不足／全部分桶失敗）。
- 帳務/金鑰錯誤附中文行動指引（餘額不足、額度不足、key 無效、模型不存在）。
- **金鑰讀取優先序：keyring（設定頁存的）→ 環境變數 → dev fallback**。曾因環境變數優先，殘留的舊 OPENAI_API_KEY 蓋掉新 key。

### 慣例與陷阱（踩過的坑，別再踩）
- `requirements.txt` **只能 ASCII**（使用者 Windows cp950，pip 會炸）。
- Edit 工具改檔前必須先 Read；大量機械式替換用 python heredoc 腳本並 assert 錨點唯一。
- QSS 蓋掉原生元件後，**下拉箭頭/spin 箭頭要自己畫**（QSS border 三角形技巧在 Qt 是色塊，要用 QPainter 產 PNG + url()）；新增狀態標籤一律 `setWordWrap(True)`；遮罩固定 8 星。
- prompt/schema 預設值更新靠 `registry.seed_defaults()` 升級機制傳播（比較 system_prompt/user_template/**tool_schema_json**；只對 is_default 的版本生效）。改預設 prompt 後使用者 Pull 重開即生效。
- 站點 selector 有效正文門檻 20 字（曾因 50 字把照片式短訊誤判）。site_selectors 預設值在載入時會 merge 進使用者存過的設定（`settings_repository.load()`）。
- 報紙新聞標記 `NEWSPAPER_BODY_SOURCE = "報紙監測（無原文連結）"`（gmail_report_parser）——抓取排除無連結列、分群豁免正文門檻都靠字串比對它，**不要改值**（既有 DB 有舊字串）；顯示層另行轉換。
- 重新匯入報紙報告會走 `repair_newspaper_rows()`：補連結、不重複插列、保留人工判斷。
- OpenAI 模型清單只放**驗證過**的型號（曾放猜測的 gpt-5.5-mini 造成整批 404）。
- 供應商切換與逐任務模型是兩層（常見誤解）：provider 決定走哪家，任務模型只挑該家型號；儲存任務模型時已有跨家警告對話框。

## 4. 檔案地圖（最常動的）

| 區域 | 檔案 |
|---|---|
| AI 閘道 | `app/services/ai/model_gateway.py`、`openai_gateway.py`、`model_capabilities.py` |
| 留用 | `app/services/retention/retention_service.py`、`app/workers/retention_worker.py`、`app/ui/pages/retention_page.py`、`app/prompts/retention_prompt.py` |
| 分群 | `app/services/clustering/clustering_service.py`、`app/workers/clustering_worker.py`、`app/prompts/clustering_prompt.py`（含 GRANULARITY_INSTRUCTIONS） |
| Gmail | `app/services/gmail/gmail_report_parser.py`（兩版型解析＋repair）、`gmail_client.py`（多關鍵字查詢）、`gmail_auth.py` |
| 抓取 | `app/services/scraping/body_scraper.py`（site_selectors）、`app/workers/scraping_worker.py` |
| 匯出 | `app/exporters/word_exporter.py`（`export_daily_report` 完整早報、`export_simple_topic_list` 簡易清單） |
| 主題/UI | `app/ui/theme.py`、`app/ui/main_window.py`（NAV_GROUPS）、`app/ui/pages/*` |
| 設定 | `app/models/settings.py`（ApiSettings 含 provider/clustering_granularity）、`app/repositories/settings_repository.py`（load 有 merge 邏輯） |
| 共用 | `app/services/taxonomy.py`、`app/utils/secure_key_store.py`（金鑰優先序）、`app/utils/text_utils.py` |
| 網頁版 | `app/web/routes/*`（build_*_job_inputs 供單步與 pipeline 共用）、`app/web/job_runner.py`（含重複執行防護） |

DB schema v4（`app/repositories/db.py`）：migration 只增不刪，新欄位記得同時加進 CREATE TABLE（新安裝）與 `_run_migrations`（既有安裝）。

## 5. 下一步（依優先序）

1. **【主線】晨會報告格式與內容精修**——原三步計畫（①全改 ChatGPT ②整條流程跑通 ③修報告）只剩這步。①②已完成（ChatGPT 整條流程已驗證到分群前一步；最後一輪留用重試與後續分群結果待使用者回報）。做法：請使用者匯出一份現在的 Word 早報（`export_daily_report`），標註想改的版式/內容點，逐項調 `word_exporter.py` 與綜整 prompt（`summarization_prompt.py`）。報紙新聞現在有全文，也會進綜整。
2. 觀察分群粒度＋命名學習的實際效果，必要時再調 `GRANULARITY_INSTRUCTIONS` 文字或 few-shot 數量（目前 10 則）。
3. 回報未修的架構性事項（使用者知情）：網頁版 Gmail 匯入是同步請求（量大逾時風險，gunicorn timeout 已放寬 300s）；Render 上 Gmail 憑證走明碼檔案 fallback；桌面版殘留 running 的 scraping job 重啟不會自動標失敗。
4. 選擇性：留用基準測試 v2（`benchmark/` 兩支腳本，比較新 prompt 與新供應商的過嚴率）。

## 6. 驗證工具箱

```bash
# 全測試（270 個，必須全綠才 commit）
cd /home/user/-news-sentiment/news_sentiment_desktop_v4
QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q

# UI 改動：offscreen 渲染截圖給使用者確認（比文字描述有效十倍）
QT_QPA_PLATFORM=offscreen python3 - <<'EOF'
import sys, os; sys.path.insert(0, '.')
os.environ['NEWS_SENTIMENT_DATA_DIR'] = '/tmp/.../scratchpad/uipreview'  # 隔離資料目錄
from PySide6.QtWidgets import QApplication
from app.controllers.app_context import AppContext
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme
app = QApplication(sys.argv); apply_theme(app)
win = MainWindow(AppContext()); win.resize(1600, 960)
# 塞示意資料到 ctx.news_repo/topic_repo 後，用 nav item 的 Qt.UserRole(=0x0100) data 切頁
win.grab().save('preview.png')
EOF
```

- 真實資料驗證：使用者上傳過真實監測信（.eml），解析器改動時可請他再提供樣本實測。
- 外部網站（XKM 剪報頁）沙盒可直連，可實抓驗證 selector。
- 測試 fixtures：`tests/conftest.py`（tmp_db_path、各 repo、qapp、fake_anthropic_module）；OpenAI 假模組模式見 `test_openai_gateway.py`；網頁版 fixture 見 `test_web_smoke.py`。
- 模組層級快取（_LEARNED_MIN_TOKENS 等）測試時用 monkeypatch 重置，避免跨測試污染。

## 7. 給下一個對話的開場建議

1. 讀本檔＋`CLAUDE.md`＋`git log --oneline -30`（commit 訊息即變更史，寫得很細）。
2. 問使用者：上輪「重試失敗批次」與後續分群/綜整/匯出的結果如何？（§5-1 的前置確認）
3. 然後直接進主線：晨會報告精修。
