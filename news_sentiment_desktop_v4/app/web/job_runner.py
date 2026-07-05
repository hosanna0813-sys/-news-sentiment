"""
背景批次工作執行器 — 網頁版對應桌面版 app/workers/batch_job_worker.py 的角色。

桌面版的 BatchJobWorker 繼承 QThread、以 Signal 回報進度給 PySide6 主執行緒；
網頁版沒有 Qt event loop，改用一般 threading.Thread 執行，進度直接寫進既有的
JobRepository / BatchRepository（純 SQLite，桌面版也是拿這兩個 repo 存進度，
本來就不依賴 QThread），前端用 /jobs/<job_id>/status 端點輪詢同一份資料表即可，
不需要另外設計一套進度回報機制。

與桌面版 BatchJobWorker 的差異（有意簡化，非疏漏）：
    - 只支援序列處理（不做 ThreadPoolExecutor 平行批次）：網頁版單次工作的
      新聞量是一天份，序列處理的等待時間可接受，換取程式更單純。
    - 不支援續跑（resume_job_id）：每次按「執行」都是全新的一次性工作；
      使用者一天只會按一次，跟桌面版「大量新聞、可能中途關閉程式」的情境不同。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, List, Optional

from app.models.job import JobRecord, BatchRecord
from app.repositories.job_repository import JobRepository, BatchRepository
from app.utils.text_utils import new_id
from app.utils.logging_setup import get_logger

logger = get_logger("web_job_runner")


class BatchOutcome:
    def __init__(self, success: bool, error_type: str = "", error_detail: str = "",
                 success_count: int = 0, skipped_count: int = 0):
        self.success = success
        self.error_type = error_type
        self.error_detail = error_detail
        self.success_count = success_count
        self.skipped_count = skipped_count


def start_batch_job(job_type: str, item_batches: List[List[Any]],
                     process_batch_fn: Callable[[List[Any]], BatchOutcome],
                     job_repo: JobRepository, batch_repo: BatchRepository,
                     job_label_fn: Optional[Callable[[Any], str]] = None) -> str:
    """建立 Job/Batch 紀錄並立即在背景執行緒開始處理，回傳 job_id 供前端輪詢。"""
    job_label_fn = job_label_fn or (lambda it: getattr(it, "row_id", str(it)))
    total_items = sum(len(b) for b in item_batches)

    job = JobRecord.new(job_type, total_items)
    job_repo.create(job)
    job_repo.update(job.job_id, {"status": "running", "started_at": time.time()})

    batch_records = []
    for idx, batch_items in enumerate(item_batches):
        item_ids = [job_label_fn(it) for it in batch_items]
        br = BatchRecord(batch_id=new_id("b_"), job_id=job.job_id, batch_index=idx,
                          item_ids_json=json.dumps(item_ids, ensure_ascii=False))
        batch_repo.create(br)
        batch_records.append((idx, batch_items, br))

    def _run():
        counters = {"success": 0, "failed": 0, "skipped": 0, "progress": 0}
        for idx, batch_items, record in batch_records:
            batch_repo.update(record.batch_id, {"status": "running", "started_at": time.time()})
            try:
                outcome = process_batch_fn(batch_items)
            except Exception as e:
                logger.exception(f"批次 {idx}（job_type={job_type}）發生未預期錯誤")
                outcome = BatchOutcome(success=False, error_type="other", error_detail=str(e))

            if outcome.success:
                batch_repo.update(record.batch_id, {"status": "completed", "finished_at": time.time()})
                counters["success"] += outcome.success_count
                counters["skipped"] += outcome.skipped_count
            else:
                batch_repo.update(record.batch_id, {
                    "status": "retryable", "error_type": outcome.error_type,
                    "error_detail": outcome.error_detail, "finished_at": time.time(),
                })
                counters["failed"] += len(batch_items)
                logger.warning(f"批次 {idx} 失敗（{job_type}, {outcome.error_type}）: {outcome.error_detail}")

            counters["progress"] += len(batch_items)
            job_repo.update(job.job_id, {
                "progress_current": counters["progress"], "success_count": counters["success"],
                "failed_count": counters["failed"], "skipped_count": counters["skipped"],
            })

        job_repo.update(job.job_id, {"status": "completed", "finished_at": time.time()})

    threading.Thread(target=_run, name=f"webjob-{job_type}-{job.job_id[:8]}", daemon=True).start()
    return job.job_id


def job_status_dict(job_repo: JobRepository, job_id: str) -> Optional[dict]:
    job = job_repo.get(job_id)
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "total_items": job.total_items,
        "progress_current": job.progress_current,
        "success_count": job.success_count,
        "failed_count": job.failed_count,
        "skipped_count": job.skipped_count,
    }
