"""Prompt 版本管理模型 — 對應規格書 十六、Prompt 管理"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


PROMPT_TASKS = (
    "retention_prefilter",      # 留用初判階段一：粗篩
    "retention_judgement",      # 留用初判階段二：MOI 政策關注度評分
    "topic_clustering",         # 議題分群
    "topic_merge",               # 跨批次議題整合
    "topic_naming",              # 議題命名
    "topic_summarization",       # 議題綜整
    "stance_analysis",           # 立場分析
    "rule_draft",                 # 規則草案
)


@dataclass
class PromptConfig:
    task: str                    # 對應 PROMPT_TASKS
    system_prompt: str
    user_template: str           # 可用 {placeholder} 樣式
    tool_schema_json: str = "{}"  # 對應 Tool Use schema（若適用）
    version: int = 1
    enabled: bool = True
    last_modified_at: float = field(default_factory=time.time)
    is_default: bool = False     # 標記這是否為系統預設版本（用於「還原預設」）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "PromptConfig":
        known = {f: row.get(f) for f in PromptConfig.__dataclass_fields__.keys() if f in row}
        return PromptConfig(**known)
