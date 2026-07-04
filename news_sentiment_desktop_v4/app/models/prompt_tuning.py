"""Prompt 調校建議草案模型 — retention_judgement 專用的 AI 提案 + 驗證結果"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import time

PROMPT_TUNING_STATES = (
    "待驗證",      # 已產生提案，尚未驗證
    "驗證中",      # worker 執行中
    "已驗證",      # 驗證完成，指標可供檢視，等待採用/拒絕
    "已套用",      # 使用者按下套用，已寫入正式 prompt
    "已拒絕",      # 使用者按下拒絕
    "驗證失敗",    # 驗證過程 API 錯誤，可重新驗證
)


@dataclass
class PromptTuningDraft:
    draft_id: str
    task: str = "retention_judgement"          # 目前固定，欄位保留供未來其他任務沿用
    based_on_version: int = 0                  # 產生提案當下，retention_judgement 使用中的 PromptConfig.version
    proposed_system_prompt: str = ""
    proposed_user_template: str = ""
    rationale: str = ""                        # AI 說明觀察到的修正模式與調整理由
    status: str = "待驗證"
    validation_metrics_json: str = "{}"
    generated_by_model: str = ""
    correction_count_used: int = 0             # 產生提案時參考了多少筆人工修正紀錄
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "PromptTuningDraft":
        known = {f: row.get(f) for f in PromptTuningDraft.__dataclass_fields__.keys() if f in row}
        return PromptTuningDraft(**known)
