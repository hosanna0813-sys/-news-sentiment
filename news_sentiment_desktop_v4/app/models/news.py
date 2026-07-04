"""
新聞資料模型 (News Item)

對應規格書 五、匯入新聞 與 六、AI 留用初判 與 八、正文抓取 的欄位定義。
使用 dataclass，並提供 to_dict / from_row 轉換方法，方便與 SQLite repository 互轉。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


def now_ts() -> float:
    return time.time()


@dataclass
class NewsItem:
    # ---- 識別 ----
    row_id: str                      # 匯入時建立的唯一 ID（即使內容重複也不可衝突）
    news_id: Optional[str] = None    # 原始資料裡的 news_id（可能為空或重複）
    import_batch_id: str = ""
    source_sheet: str = ""           # 來自哪個 Excel 工作表

    # ---- 原始欄位 ----
    title: str = ""
    summary: str = ""                # Excel 摘要／既有內容
    source: str = ""
    published_at: str = ""
    author: str = ""
    url: str = ""
    channel: str = ""
    tags: str = ""
    excel_body: str = ""             # Excel 原本就有的正文（若有）

    # ---- 留用初判（六） ----
    retained: bool = True
    retention_status: str = "待確認"  # 留用 / AI建議不留用 / 人工不留用 / 待確認
    retention_reason: str = ""
    ai_retention_confidence: float = 0.0
    duplicate_group_id: str = ""
    manual_note: str = ""
    retention_judged_at: Optional[float] = None
    retention_judged_by: str = ""    # "ai" / "human"

    # ---- 留用初判 v2：MOI 政策關注度評分 ----
    score_business_relevance: float = 0.0     # 0-40
    score_response_requirement: float = 0.0   # 0-20
    score_political_sensitivity: float = 0.0  # 0-15
    score_media_attention: float = 0.0        # 0-15
    score_public_impact: float = 0.0          # 0-10
    score_executive_bonus: float = 0.0        # 0-20（加分項）
    score_final: float = 0.0                  # AI 直接回傳的總分
    priority_stars: int = 0                   # 1-5
    should_respond: bool = False
    is_moi_core_business: bool = False        # 是否為內政部所屬機關本業／設施／執法成果／出資建設，獨立於評分階梯的留用訊號
    recommended_action: str = ""              # None/Monitor/Prepare QA/Prepare Press Release/Prepare Social Media/Minister Talking Points
    action_reasoning: str = ""                # 建議行動的理由

    # ---- 正文抓取（八） ----
    body_text: str = ""
    body_source: str = "無正文"       # Excel正文 / 網頁抓取正文 / 無正文
    body_fetch_status: str = "未抓取"  # 未抓取/成功/失敗/略過
    body_fetch_detail: str = ""
    body_fetched_at: Optional[float] = None
    body_quality_score: float = 0.0
    body_word_count: int = 0

    # ---- 議題分群（九） ----
    initial_topic_id: str = ""
    initial_topic_name: str = ""
    final_topic_id: str = ""
    final_topic_name: str = ""
    clustering_reason: str = ""
    clustering_confidence: float = 0.0
    evidence_news_ids: str = ""      # 逗號分隔

    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "NewsItem":
        known = {f: row.get(f) for f in NewsItem.__dataclass_fields__.keys() if f in row}
        return NewsItem(**known)

    @property
    def has_body(self) -> bool:
        return bool(self.body_text and self.body_word_count >= 30)
