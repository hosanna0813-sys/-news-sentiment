"""議題 (Topic) 資料模型 — 對應規格書 九、十、十一、十二"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


@dataclass
class Topic:
    topic_id: str
    topic_name: str
    status: str = "active"          # active / merged / deleted
    merged_into: Optional[str] = None

    # 綜整結果（十一）
    summary_150: str = ""
    summary_300: str = ""
    summary_full: str = ""
    development_progress: str = ""   # 事件發展與關鍵進度
    core_disputes: str = ""          # 核心爭點
    key_actors: str = ""             # 主要行動者與發言
    possible_impact: str = ""        # 可能後續影響
    cited_news_count: int = 0

    has_identifiable_stance: bool = False  # 十二：整個議題群若無立場，不顯示立場區塊

    summarized_at: Optional[float] = None
    summarized_by_model: str = ""

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "Topic":
        known = {f: row.get(f) for f in Topic.__dataclass_fields__.keys() if f in row}
        return Topic(**known)


@dataclass
class Stance:
    """立場分析結果 — 對應規格書 十二"""
    stance_id: str
    topic_id: str
    stance_type: str        # 支持 / 反對／質疑 / 官方回應
    speaker: str = ""
    organization: str = ""
    claim: str = ""
    evidence_news_id: str = ""
    evidence_excerpt: str = ""
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)
    # 人工修正紀錄
    human_modified: bool = False
    human_modified_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "Stance":
        known = {f: row.get(f) for f in Stance.__dataclass_fields__.keys() if f in row}
        return Stance(**known)
