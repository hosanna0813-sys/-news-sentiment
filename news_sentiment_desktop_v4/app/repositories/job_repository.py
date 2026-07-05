"""Job / Batch Repository — 對應規格十五：所有耗時工作可取消、可續跑"""
from __future__ import annotations

import time
from typing import List, Optional, Dict, Any

from app.models.job import JobRecord, BatchRecord
from app.repositories.db import get_connection


class JobRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def create(self, job: JobRecord) -> None:
        d = job.to_dict()
        with self.conn:
            self.conn.execute(
                "INSERT INTO jobs (job_id,job_type,status,total_items,success_count,failed_count,"
                "skipped_count,progress_current,started_at,finished_at,cancel_requested,created_at,"
                "params_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["job_id"], d["job_type"], d["status"], d["total_items"], d["success_count"],
                 d["failed_count"], d["skipped_count"], d["progress_current"], d["started_at"],
                 d["finished_at"], int(d["cancel_requested"]), d["created_at"], d["params_json"]),
            )

    def update(self, job_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ",".join([f"{k}=?" for k in fields.keys()])
        with self.conn:
            self.conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id=?",
                               list(fields.values()) + [job_id])

    def request_cancel(self, job_id: str) -> None:
        self.update(job_id, {"cancel_requested": 1})

    def is_cancel_requested(self, job_id: str) -> bool:
        cur = self.conn.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,))
        row = cur.fetchone()
        return bool(row and row["cancel_requested"])

    def get(self, job_id: str) -> Optional[JobRecord]:
        cur = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        row = cur.fetchone()
        return JobRecord.from_row(dict(row)) if row else None

    def list_resumable(self, job_type: Optional[str] = None) -> List[JobRecord]:
        """回傳可續跑的工作（狀態非 completed/cancelled）"""
        if job_type:
            cur = self.conn.execute(
                "SELECT * FROM jobs WHERE job_type=? AND status NOT IN ('completed','cancelled') "
                "ORDER BY created_at DESC", (job_type,))
        else:
            cur = self.conn.execute(
                "SELECT * FROM jobs WHERE status NOT IN ('completed','cancelled') ORDER BY created_at DESC")
        return [JobRecord.from_row(dict(r)) for r in cur.fetchall()]

    def list_all(self) -> List[JobRecord]:
        cur = self.conn.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        return [JobRecord.from_row(dict(r)) for r in cur.fetchall()]

    def mark_stale_running_jobs_as_failed(self) -> int:
        """啟動時清理用：把上次程序意外中止（例如雲端部署重新啟動）時卡在
        running 的工作標記為 failed，回傳受影響筆數。只有網頁版會在啟動時
        呼叫這個方法——桌面版的 running 工作設計上本來就要能在下次啟動後
        續跑（BatchJobWorker 的 resume_job_id 機制），不能被這裡誤判成失敗。"""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET status='failed', finished_at=? WHERE status='running'",
                (time.time(),))
        return cur.rowcount

    def delete_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM jobs")


class BatchRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def create(self, batch: BatchRecord) -> None:
        d = batch.to_dict()
        with self.conn:
            self.conn.execute(
                "INSERT INTO batches (batch_id,job_id,batch_index,status,item_ids_json,error_type,"
                "error_detail,retry_count,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d["batch_id"], d["job_id"], d["batch_index"], d["status"], d["item_ids_json"],
                 d["error_type"], d["error_detail"], d["retry_count"], d["started_at"], d["finished_at"]),
            )

    def update(self, batch_id: str, fields: Dict[str, Any]) -> None:
        set_clause = ",".join([f"{k}=?" for k in fields.keys()])
        with self.conn:
            self.conn.execute(f"UPDATE batches SET {set_clause} WHERE batch_id=?",
                               list(fields.values()) + [batch_id])

    def list_by_job(self, job_id: str) -> List[BatchRecord]:
        cur = self.conn.execute("SELECT * FROM batches WHERE job_id=? ORDER BY batch_index", (job_id,))
        return [BatchRecord.from_row(dict(r)) for r in cur.fetchall()]

    def list_pending_or_retryable(self, job_id: str) -> List[BatchRecord]:
        cur = self.conn.execute(
            "SELECT * FROM batches WHERE job_id=? AND status IN ('pending','retryable') "
            "ORDER BY batch_index", (job_id,))
        return [BatchRecord.from_row(dict(r)) for r in cur.fetchall()]

    def delete_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM batches")
