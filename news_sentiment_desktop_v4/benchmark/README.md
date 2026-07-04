# 留用初判基準測試：Claude API vs OpenAI API

用您資料庫裡「人工覆核過的留用決定」當標準答案，讓兩家的模型盲測同一批新聞，
比較誰更接近人工判斷。prompt 與 schema 與正式流程完全相同，公平對測。

## 事前準備

1. **OpenAI API Key**：到 platform.openai.com 註冊、儲值（10 美元很夠），建立 API Key
2. **安裝 openai 套件**（在專案資料夾開命令視窗）：
   ```bat
   .venv\Scripts\pip install openai
   ```
3. Anthropic 金鑰不用設定——腳本會自動從 Windows 認證管理員讀取（跟主程式同一把）

## 步驟 1：匯出測試資料集（分層抽樣）

```bat
.venv\Scripts\python benchmark\export_benchmark_dataset.py
```

- 自動讀取 `%APPDATA%\NewsSentimentDesktopV4\news_sentiment.db`
- **修正樣本**（AI 判錯、被人工改過的「難題」）全數保留
- **對照樣本**（AI 判對的）留用/不留用各隨機抽 60 則
- 輸出 `benchmark\benchmark_dataset.json`，畫面會顯示各類數量

## 步驟 2：執行盲測

先設定 OpenAI 金鑰（每次開新的命令視窗都要設一次）：

```bat
set OPENAI_API_KEY=sk-你的金鑰
```

建議先小額試跑（只測 20 則，確認兩邊都能通）：

```bat
.venv\Scripts\python benchmark\run_retention_benchmark.py --limit 20 --runs 1 ^
    --model anthropic:claude-sonnet-5 ^
    --model openai:OpenAI模型ID
```

> OpenAI 模型 ID 請到 platform.openai.com/docs/models 查當天最新的
> 旗艦（回應品質優先）與中階（性價比）型號，把上面的 `OpenAI模型ID` 換掉。

試跑正常後，跑正式測試（3 輪，全部資料）：

```bat
.venv\Scripts\python benchmark\run_retention_benchmark.py ^
    --model anthropic:claude-sonnet-5 ^
    --model openai:OpenAI模型ID
```

想估算 OpenAI 費用，補上牌價（美元/百萬 tokens，輸入價,輸出價）：

```bat
    --price OpenAI模型ID=1.25,10
```

## 步驟 3：看報告

- `benchmark\benchmark_result_report.md`：評分總表（可直接貼回對話讓 Claude 幫你解讀）
- `benchmark\benchmark_result_raw.json`：每一輪每一則的原始判斷

### 指標怎麼看

| 指標 | 意義 |
|---|---|
| 過嚴率 ⬇ | 該留的漏掉幾 %（**最重要**，漏報代價最高） |
| 過寬率 ⬇ | 不該留的誤留幾 %（雜訊） |
| 難題救回率 ⬆ | 之前 AI 判錯的樣本，這次答對幾 % |
| 對照保持率 ⬆ | 本來判對的沒有被改壞幾 % |
| 缺漏率 ⬇ | 送 N 則少回幾則（工程可靠度） |
| 判斷翻盤率 ⬇ | 同一則新聞多輪重跑，判斷不一致的比例 |

## 常用變化

- 加測其他模型：多加幾個 `--model anthropic:claude-opus-4-8` 之類
- 每批則數：`--batch-size 20`（預設 10，與正式流程相同）
- 星等門檻：`--threshold 3`（預設 3，與系統設定相同；想比較不同門檻的影響，
  把 `benchmark_result_raw.json` 貼回對話，可以直接離線重算，不用重花 API 錢）
