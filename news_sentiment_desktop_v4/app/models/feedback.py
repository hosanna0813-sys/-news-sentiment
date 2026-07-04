"""回饋 log、案例庫、規則庫資料模型 — 對應規格書 十三"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


@dataclass
class FeedbackLogEntry:
    """A. 原始回饋 log（完整保存，不直接當規則）"""
    feedback_id: str
    batch_id: str
    entity_type: str        # retention / clustering / topic_naming / stance
    entity_id: str          # news row_id 或 topic_id
    ai_original_value: str = ""
    human_final_value: str = ""
    action: str = ""        # e.g. "merge" / "split" / "drag" / "rename" / "override"
    reason: str = ""        # 可選修正原因
    operator: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "FeedbackLogEntry":
        known = {f: row.get(f) for f in FeedbackLogEntry.__dataclass_fields__.keys() if f in row}
        return FeedbackLogEntry(**known)


@dataclass
class CaseRecord:
    """B. 案例庫：經人工確認、有代表性的案例"""
    case_id: str
    case_type: str          # merge / split / naming / retention
    description: str = ""
    source_feedback_ids: str = ""   # 逗號分隔
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "CaseRecord":
        known = {f: row.get(f) for f in CaseRecord.__dataclass_fields__.keys() if f in row}
        return CaseRecord(**known)


@dataclass
class RuleDraft:
    """規則草案 / 規則庫（C 層，需人工核准才生效）"""
    rule_id: str
    name: str
    scope: str = ""                  # 適用範圍
    rule_text: str = ""              # 建議合併/拆分/命名/留用規則
    supporting_case_count: int = 0
    representative_cases: str = ""   # 逗號分隔的 case_id
    risk_notes: str = ""             # 風險或例外情況
    priority: str = "中"             # 建議優先級：高/中/低
    status: str = "draft"            # draft / adopted / disabled / deleted
    version: int = 1
    generated_by_model: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "RuleDraft":
        known = {f: row.get(f) for f in RuleDraft.__dataclass_fields__.keys() if f in row}
        return RuleDraft(**known)
