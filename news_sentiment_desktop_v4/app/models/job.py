"""
工作佇列 / 批次任務資料模型 — 對應規格書 十五、效能與可靠性

所有耗時工作（留用初判、正文抓取、議題分群、綜整、立場分析）都透過
JobRecord + BatchRecord 追蹤狀態，狀態機為：
    pending -> running -> completed
                       -> failed (可重試 retryable)
                       -> cancelled
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time
import uuid

JOB_STATES = ("pending", "running", "completed", "failed", "cancelled", "retryable")


@dataclass
class JobRecord:
    """代表一個大工作，例如「500 則留用初判」"""
    job_id: str
    job_type: str            # retention / scraping / clustering / summarization / stance
    status: str = "pending"
    total_items: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    progress_current: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    params_json: str = "{}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "JobRecord":
        known = {f: row.get(f) for f in JobRecord.__dataclass_fields__.keys() if f in row}
        return JobRecord(**known)

    @staticmethod
    def new(job_type: str, total_items: int, params_json: str = "{}") -> "JobRecord":
        return JobRecord(job_id=str(uuid.uuid4()), job_type=job_type, total_items=total_items,
                          params_json=params_json)


@dataclass
class BatchRecord:
    """一個 Job 底下的單一批次（例如第 3 批 / 50 則），用於失敗只影響該批"""
    batch_id: str
    job_id: str
    batch_index: int
    status: str = "pending"
    item_ids_json: str = "[]"       # 本批涉及的 row_id / topic_id 清單
    error_type: str = ""            # authentication_error / rate_limit_error /
                                     # overloaded_error / invalid_request_error / timeout / other
    error_detail: str = ""
    retry_count: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: dict) -> "BatchRecord":
        known = {f: row.get(f) for f in BatchRecord.__dataclass_fields__.keys() if f in row}
        return BatchRecord(**known)
