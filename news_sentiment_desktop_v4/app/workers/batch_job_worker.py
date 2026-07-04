"""
BatchJobWorker — 所有耗時 AI／抓取工作的共用 QThread 基礎類別

對應規格書 十五、效能與可靠性：
    - 顯示目前工作名稱、進度（如 128/500）、成功/失敗/跳過數量
    - 可取消
    - 可在下次啟動後續跑
    - 不可讓整個 GUI 凍結（在 QThread 中執行，透過 Signal 回主執行緒更新 UI）

用法：
    worker = BatchJobWorker(job_type="retention", batches=[...], process_batch_fn=fn,
                             job_repo=..., batch_repo=..., resume_job_id=None)
    worker.progress.connect(...)
    worker.finished_job.connect(...)
    worker.start()

process_batch_fn(batch_items) -> BatchOutcome：由呼叫端提供實際處理邏輯
（例如呼叫 retention_service.judge_batch 並寫回 NewsRepository）。
process_batch_fn 拋出例外視為該批失敗，不影響其他批次。

max_concurrency（預設 1，維持既有循序行為，不影響爬蟲/分群/綜整/立場/規則草案
五種既有工作類型）大於 1 時，改用 ThreadPoolExecutor 同時送出多批的
process_batch_fn；job_repo/batch_repo 的狀態寫入與 progress/batch_failed signal
的發送仍固定在本 QThread（呼叫端執行緒）依完成順序處理，只有 process_batch_fn
本身真正平行執行——因此 process_batch_fn 內部若要寫 DB，須自行建立
thread-local 連線（不可使用呼叫端在其他執行緒建立的 repo 物件）。
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass
from typing import Callable, List, Any, Optional

from PySide6.QtCore import QThread, Signal

from app.models.job import JobRecord, BatchRecord
from app.repositories.job_repository import JobRepository, BatchRepository
from app.utils.text_utils import new_id
from app.utils.logging_setup import get_logger

logger = get_logger("batch_job_worker")


@dataclass
class BatchOutcome:
    success: bool
    error_type: str = ""
    error_detail: str = ""
    success_count: int = 0
    skipped_count: int = 0


class BatchJobWorker(QThread):
    # (job_id, job_type, current, total, success, failed, skipped, message)
    progress = Signal(str, str, int, int, int, int, int, str)
    batch_failed = Signal(str, int, str, str)   # job_id, batch_index, error_type, error_detail
    finished_job = Signal(str, str)             # job_id, final_status

    def __init__(self, job_type: str, item_batches: List[List[Any]],
                 process_batch_fn: Callable[[List[Any]], BatchOutcome],
                 job_repo: JobRepository, batch_repo: BatchRepository,
                 resume_job_id: Optional[str] = None, job_label_fn: Optional[Callable[[Any], str]] = None,
                 max_retry_per_batch: int = 2, max_concurrency: int = 1, parent=None):
        super().__init__(parent)
        self.job_type = job_type
        self.item_batches = item_batches
        self.process_batch_fn = process_batch_fn
        self.job_repo = job_repo
        self.batch_repo = batch_repo
        self.resume_job_id = resume_job_id
        self.job_label_fn = job_label_fn or (lambda it: getattr(it, "row_id", str(it)))
        self.max_retry_per_batch = max_retry_per_batch
        self.max_concurrency = max(1, max_concurrency)
        self._cancel = False
        self.job_id: str = resume_job_id or ""

    def request_cancel(self) -> None:
        self._cancel = True
        if self.job_id:
            self.job_repo.request_cancel(self.job_id)

    def run(self) -> None:
        total_items = sum(len(b) for b in self.item_batches)

        if self.resume_job_id:
            self.job_id = self.resume_job_id
            job = self.job_repo.get(self.job_id)
            if job is None:
                job = JobRecord.new(self.job_type, total_items)
                self.job_repo.create(job)
        else:
            job = JobRecord.new(self.job_type, total_items)
            self.job_id = job.job_id
            self.job_repo.create(job)

        self.job_repo.update(self.job_id, {"status": "running", "started_at": time.time()})

        existing_batches = {b.batch_index: b for b in self.batch_repo.list_by_job(self.job_id)}
        if not existing_batches:
            for idx, batch_items in enumerate(self.item_batches):
                item_ids = [self.job_label_fn(it) for it in batch_items]
                br = BatchRecord(batch_id=new_id("b_"), job_id=self.job_id, batch_index=idx,
                                  item_ids_json=json.dumps(item_ids, ensure_ascii=False))
                self.batch_repo.create(br)
                existing_batches[idx] = br
        else:
            # 續跑：以 DB 內既有批次的 item_ids 為準重建批次內容，
            # 確保 batch_index 與上次完全對齊（不受本次傳入 item_batches 切法影響）
            item_lookup = {self.job_label_fn(it): it
                            for batch in self.item_batches for it in batch}
            rebuilt = []
            for idx in sorted(existing_batches.keys()):
                ids = json.loads(existing_batches[idx].item_ids_json)
                rebuilt.append([item_lookup[i] for i in ids if i in item_lookup])
            self.item_batches = rebuilt
            total_items = sum(len(b) for b in self.item_batches)

        counters = {
            "success": self.job_repo.get(self.job_id).success_count,
            "failed": self.job_repo.get(self.job_id).failed_count,
            "skipped": self.job_repo.get(self.job_id).skipped_count,
            "progress": self.job_repo.get(self.job_id).progress_current,
        }

        # 先過濾出這次真的要處理的批次（跳過已完成／已無內容的批次），
        # 序列與平行兩種路徑共用同一份清單與取消檢查邏輯
        to_run = []
        for idx, batch_items in enumerate(self.item_batches):
            record = existing_batches.get(idx)
            if record is None:
                continue  # 防禦：DB 無對應批次紀錄（理論上不會發生）
            if record.status == "completed":
                continue  # 續跑：已完成批次略過
            if not batch_items:
                # 續跑時該批新聞已不存在於目前清單（例如被人工改為不留用），標記完成略過
                self.batch_repo.update(record.batch_id, {"status": "completed",
                                                          "finished_at": time.time()})
                continue
            to_run.append((idx, batch_items, record))

        if self._cancel or self.job_repo.is_cancel_requested(self.job_id):
            self.job_repo.update(self.job_id, {"status": "cancelled", "finished_at": time.time()})
            self.finished_job.emit(self.job_id, "cancelled")
            return

        if self.max_concurrency <= 1:
            cancelled = self._run_sequential(to_run, total_items, counters)
        else:
            cancelled = self._run_concurrent(to_run, total_items, counters)
        if cancelled:
            return

        final_status = "completed"
        # 注意：即使部分批次失敗，整體工作仍標記為 completed（因為已成功的批次結果已保存），
        # 失敗的批次個別標記為 retryable，可由使用者觸發「重試失敗項目」
        self.job_repo.update(self.job_id, {"status": final_status, "finished_at": time.time()})
        self.finished_job.emit(self.job_id, final_status)

    def _handle_outcome(self, idx: int, batch_items: List[Any], record: BatchRecord,
                         outcome: BatchOutcome, total_items: int, counters: dict) -> None:
        if outcome.success:
            self.batch_repo.update(record.batch_id, {"status": "completed", "finished_at": time.time()})
            counters["success"] += outcome.success_count
            counters["skipped"] += outcome.skipped_count
        else:
            self.batch_repo.update(record.batch_id, {
                "status": "retryable", "error_type": outcome.error_type,
                "error_detail": outcome.error_detail, "finished_at": time.time(),
                "retry_count": record.retry_count + 1,
            })
            counters["failed"] += len(batch_items)
            self.batch_failed.emit(self.job_id, idx, outcome.error_type, outcome.error_detail)
            logger.warning(f"批次 {idx} 失敗 ({outcome.error_type}): {outcome.error_detail}")

        counters["progress"] += len(batch_items)
        self.job_repo.update(self.job_id, {
            "progress_current": counters["progress"], "success_count": counters["success"],
            "failed_count": counters["failed"], "skipped_count": counters["skipped"],
        })
        self.progress.emit(self.job_id, self.job_type, counters["progress"], total_items,
                            counters["success"], counters["failed"], counters["skipped"],
                            f"已完成批次 {idx + 1}/{len(self.item_batches)}")

    def _run_sequential(self, to_run: List[tuple], total_items: int, counters: dict) -> bool:
        """回傳是否因取消而提前結束（True=已發出 cancelled 狀態，呼叫端不應再覆蓋成 completed）"""
        for idx, batch_items, record in to_run:
            if self._cancel or self.job_repo.is_cancel_requested(self.job_id):
                self.job_repo.update(self.job_id, {"status": "cancelled", "finished_at": time.time()})
                self.finished_job.emit(self.job_id, "cancelled")
                return True
            self.batch_repo.update(record.batch_id, {"status": "running", "started_at": time.time()})
            self.progress.emit(self.job_id, self.job_type, counters["progress"], total_items,
                                counters["success"], counters["failed"], counters["skipped"],
                                f"處理批次 {idx + 1}/{len(self.item_batches)}")
            try:
                outcome = self.process_batch_fn(batch_items)
            except Exception as e:
                outcome = BatchOutcome(success=False, error_type="other", error_detail=str(e))
            self._handle_outcome(idx, batch_items, record, outcome, total_items, counters)
        return False

    def _run_concurrent(self, to_run: List[tuple], total_items: int, counters: dict) -> bool:
        """回傳是否因取消而提前結束。已送入執行緒池的批次仍會跑完（無法中途打斷 API 呼叫），
        但尚未開始的批次會在 safe_process 內看到取消旗標後直接跳過，不再送出新的 API 呼叫。"""
        for idx, batch_items, record in to_run:
            self.batch_repo.update(record.batch_id, {"status": "running", "started_at": time.time()})
        self.progress.emit(self.job_id, self.job_type, counters["progress"], total_items,
                            counters["success"], counters["failed"], counters["skipped"],
                            f"平行處理 {len(to_run)} 個批次中（同時 {self.max_concurrency} 批）...")

        def safe_process(batch_items):
            # 注意：這裡只能檢查記憶體旗標 self._cancel，不可呼叫 self.job_repo（sqlite3 連線
            # 不可從多個執行緒同時存取，即使 check_same_thread=False 仍可能拋出
            # "bad parameter or other API misuse"）。job_repo 的取消狀態查詢留給
            # run() 所在的主執行緒處理（見迴圈結束後的檢查）。
            if self._cancel:
                return BatchOutcome(success=False, error_type="cancelled", error_detail="工作已取消")
            try:
                return self.process_batch_fn(batch_items)
            except Exception as e:
                return BatchOutcome(success=False, error_type="other", error_detail=str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            future_map = {executor.submit(safe_process, batch_items): (idx, batch_items, record)
                          for idx, batch_items, record in to_run}
            for future in concurrent.futures.as_completed(future_map):
                idx, batch_items, record = future_map[future]
                self._handle_outcome(idx, batch_items, record, future.result(), total_items, counters)

        if self._cancel or self.job_repo.is_cancel_requested(self.job_id):
            self.job_repo.update(self.job_id, {"status": "cancelled", "finished_at": time.time()})
            self.finished_job.emit(self.job_id, "cancelled")
            return True
        return False
