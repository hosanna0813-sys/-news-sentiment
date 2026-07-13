"""系統設定模型 — 對應規格書 四、Word 輸出設定、正文抓取設定"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class ModelTaskConfig:
    """各 AI 任務使用的模型與參數（可設定，不可硬編碼）"""
    task: str
    model_id: str
    max_tokens: int = 4096
    temperature: float = 0.3
    use_extended_thinking: bool = False
    use_message_batches: bool = False


DEFAULT_TASK_MODELS = [
    ModelTaskConfig(task="retention_prefilter", model_id="claude-haiku-4-5", max_tokens=1024,
                     temperature=0.0),
    ModelTaskConfig(task="retention_judgement", model_id="claude-sonnet-5", max_tokens=4096,
                     temperature=0.0, use_message_batches=True),
    ModelTaskConfig(task="topic_clustering", model_id="claude-sonnet-5", max_tokens=4096,
                     temperature=0.2),
    ModelTaskConfig(task="topic_merge", model_id="claude-sonnet-5", max_tokens=4096, temperature=0.2),
    ModelTaskConfig(task="topic_naming", model_id="claude-sonnet-5", max_tokens=512, temperature=0.2),
    ModelTaskConfig(task="topic_summarization", model_id="claude-opus-4-8", max_tokens=8192,
                     temperature=0.3),
    ModelTaskConfig(task="stance_analysis", model_id="claude-opus-4-8", max_tokens=4096,
                     temperature=0.1),
    ModelTaskConfig(task="rule_draft", model_id="claude-opus-4-8", max_tokens=4096, temperature=0.3),
    ModelTaskConfig(task="prompt_tuning_propose", model_id="claude-opus-4-8", max_tokens=8192,
                     temperature=0.3),
]


@dataclass
class ApiSettings:
    # AI 供應商（V4.3.0）："anthropic"（Claude）或 "openai"（ChatGPT）。
    # 切換後所有分析呼叫（留用/分群/綜整/立場/規則/調校）都改走該供應商。
    provider: str = "anthropic"
    # OpenAI 預設模型：任務模型設定仍是 claude-* 時，OpenAI 供應商自動改用此模型
    openai_default_model: str = "gpt-5.5"
    default_model: str = "claude-sonnet-5"
    request_timeout_sec: int = 60
    max_retries: int = 5
    retry_backoff_base_sec: float = 2.0
    batch_size_retention: int = 10
    batch_size_clustering: int = 15
    # 分群粒度（V4.4.0）："fine"（同一具體事件才合併）/"standard"/"coarse"（積極合併）
    clustering_granularity: str = "standard"
    enable_message_batches_api: bool = False
    retention_priority_threshold: int = 3  # 留用初判 v2：優先級（1-5星）達此門檻才留用
    retention_max_concurrency: int = 4  # 留用初判批次平行處理數上限


@dataclass
class ScrapingSettings:
    per_domain_delay_sec: float = 2.0
    max_concurrent_workers: int = 4
    request_timeout_sec: int = 15
    max_retries: int = 2
    user_agent: str = "NewsSentimentDesktop/4.0 (+research use)"
    respect_robots_txt: bool = True
    verify_ssl: bool = True  # 公司代理/防火牆做 TLS 檢查的環境可關閉（有安全風險，預設開啟）
    use_browser_rendering: bool = False  # requests 抓不到主文時，改用 Playwright 渲染重抓
    browser_timeout_sec: int = 45
    gne_noise_nodes: dict = field(default_factory=dict)  # {domain: [xpath, ...]} GNE 雜訊節點
    # 站點專屬主文 selector（V4.2.0）：命中時 requests 直接抽取，省 Playwright 成本。
    # 預設值依常見版型推測，實際命中情況請以站點成功率儀表板驗證後調整。
    site_selectors: dict = field(default_factory=lambda: {
        "setn.com": "#Content1",              # 三立新聞網
        "ftvnews.com.tw": "#newscontent",     # 民視新聞
        "mirrormedia.mg": "article",          # 鏡週刊（多為付費牆，命中機會低）
        # 競業信息（XKM）剪報全文頁——報紙監測報告的標題連結指向這裡，
        # 已用真實頁面驗證 div.dataView 即剪報全文（每頁恰一個）
        "rmbjbtw.rmb.com.tw": "div.dataView",
    })


@dataclass
class WordExportSettings:
    logo_path: str = ""
    header_text: str = ""
    footer_text: str = ""
    date_format: str = "%Y年%m月%d日"
    font_name: str = "標楷體"
    font_size_pt: int = 12
    heading_style: str = "Heading 1"
    paragraph_spacing_pt: int = 6
    include_news_links: bool = True
    include_body_excerpts: bool = True
    include_missing_body_list: bool = True


@dataclass
class GmailSettings:
    """Gmail 新聞來源匯入設定（OAuth 憑證本身存於 keyring，見 secure_key_store.py）。
    實際擷取的時間區間（起訖日期時間）每次點擊匯入時由對話框指定，不在此持久化，
    因為監測窗口每次執行都會往前推移，固定值意義不大。"""
    sender_email_filter: str = ""   # 例："xkm_cs@xkd.com.tw"
    subject_keyword: str = ""       # 選填，例："內政部新聞專屬監測報告"


@dataclass
class AppSettings:
    api: ApiSettings = field(default_factory=ApiSettings)
    scraping: ScrapingSettings = field(default_factory=ScrapingSettings)
    word_export: WordExportSettings = field(default_factory=WordExportSettings)
    gmail: GmailSettings = field(default_factory=GmailSettings)
    # 議題／關鍵字對照表（網頁版新增）：使用者自訂的業務關注議題與關鍵字清單
    # （純文字，可用 KEYPO 慣用的布林語法 | & ~N），作為留用初判／議題分群
    # AI 判斷時的參考資料，不做程式端的關鍵字比對解析——來源文字常有不平衡
    # 括號、不一致的分隔符號等人工謄寫的雜訊，硬解析容易悄悄出錯；改為原文
    # 注入 AI prompt，讓模型自行理解語意反而更穩妥。
    keyword_taxonomy: str = ""
    task_models: list = field(default_factory=lambda: [asdict(m) for m in DEFAULT_TASK_MODELS])

    def to_dict(self) -> dict:
        return asdict(self)
