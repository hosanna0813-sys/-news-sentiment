"""測試：BatchJobWorker 平行處理（max_concurrency）— 呼叫 worker.run() 直接同步執行
（不透過 QThread.start()，避免測試依賴真正的事件迴圈；run() 本身是純同步方法）"""
from __future__ import annotations

import threading
import time

from app.workers.batch_job_worker import BatchJobWorker, BatchOutcome


def test_concurrent_mode_actually_overlaps_batches(job_repo, batch_repo):
    """用共享計數器量測「同時在執行中」的批次數，證明真的有平行執行，不是換皮的循序執行"""
    active = {"count": 0, "max_seen": 0}
    lock = threading.Lock()

    def process(batch_items):
        with lock:
            active["count"] += 1
            active["max_seen"] = max(active["max_seen"], active["count"])
        time.sleep(0.05)
        with lock:
            active["count"] -= 1
        return BatchOutcome(success=True, success_count=len(batch_items))

    batches = [[f"item{i}"] for i in range(6)]
    worker = BatchJobWorker(job_type="test", item_batches=batches, process_batch_fn=process,
                             job_repo=job_repo, batch_repo=batch_repo, max_concurrency=4)
    worker.run()

    assert active["max_seen"] > 1, "應該要有多個批次同時在執行中，否則等於還是循序處理"


def test_max_concurrency_defaults_to_one():
    worker = BatchJobWorker(
        job_type="test", item_batches=[["a"]],
        process_batch_fn=lambda b: BatchOutcome(success=True, success_count=len(b)),
        job_repo=None, batch_repo=None)
    assert worker.max_concurrency == 1


def test_sequential_default_processes_all_batches(job_repo, batch_repo):
    processed = []

    def process(batch_items):
        processed.append(list(batch_items))
        return BatchOutcome(success=True, success_count=len(batch_items))

    batches = [["a", "b"], ["c", "d"], ["e"]]
    worker = BatchJobWorker(job_type="test", item_batches=batches, process_batch_fn=process,
                             job_repo=job_repo, batch_repo=batch_repo)
    statuses = []
    worker.finished_job.connect(lambda job_id, status: statuses.append(status))
    worker.run()

    assert statuses == ["completed"]
    assert sorted(sum(processed, [])) == ["a", "b", "c", "d", "e"]
    records = {b.batch_index: b.status for b in batch_repo.list_by_job(worker.job_id)}
    assert all(s == "completed" for s in records.values())
    assert len(records) == 3


def test_concurrent_processes_all_batches_with_correct_counts(job_repo, batch_repo):
    def process(batch_items):
        time.sleep(0.01)  # 模擬 API 呼叫耗時，驗證平行執行確實會重疊
        return BatchOutcome(success=True, success_count=len(batch_items))

    batches = [[f"item{i}"] for i in range(8)]
    worker = BatchJobWorker(job_type="test", item_batches=batches, process_batch_fn=process,
                             job_repo=job_repo, batch_repo=batch_repo, max_concurrency=4)
    statuses = []
    worker.finished_job.connect(lambda job_id, status: statuses.append(status))
    worker.run()

    assert statuses == ["completed"]
    job = job_repo.get(worker.job_id)
    assert job.success_count == 8
    assert job.failed_count == 0
    records = {b.batch_index: b.status for b in batch_repo.list_by_job(worker.job_id)}
    assert len(records) == 8
    assert all(s == "completed" for s in records.values())


def test_concurrent_marks_only_failing_batch_retryable_others_completed(job_repo, batch_repo):
    def process(batch_items):
        if batch_items[0] == "bad":
            return BatchOutcome(success=False, error_type="rate_limit_error", error_detail="boom")
        return BatchOutcome(success=True, success_count=len(batch_items))

    batches = [["ok1"], ["bad"], ["ok2"], ["ok3"]]
    worker = BatchJobWorker(job_type="test", item_batches=batches, process_batch_fn=process,
                             job_repo=job_repo, batch_repo=batch_repo, max_concurrency=3)
    worker.run()

    by_index = {b.batch_index: b.status for b in batch_repo.list_by_job(worker.job_id)}
    assert by_index[0] == "completed"
    assert by_index[1] == "retryable"
    assert by_index[2] == "completed"
    assert by_index[3] == "completed"
    job = job_repo.get(worker.job_id)
    assert job.status == "completed"  # 整體工作仍是 completed，個別批次標記 retryable 供重試
    assert job.failed_count == 1
    assert job.success_count == 3


def test_concurrent_exception_in_process_fn_marks_batch_retryable_not_crash(job_repo, batch_repo):
    def process(batch_items):
        if batch_items[0] == "boom":
            raise RuntimeError("unexpected error")
        return BatchOutcome(success=True, success_count=len(batch_items))

    batches = [["ok"], ["boom"]]
    worker = BatchJobWorker(job_type="test", item_batches=batches, process_batch_fn=process,
                             job_repo=job_repo, batch_repo=batch_repo, max_concurrency=2)
    worker.run()

    by_index = {b.batch_index: b.status for b in batch_repo.list_by_job(worker.job_id)}
    assert by_index[0] == "completed"
    assert by_index[1] == "retryable"
