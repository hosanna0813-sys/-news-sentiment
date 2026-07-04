"""測試：可續跑機制 — 續跑時批次內容以 DB 紀錄為準重建，index 對齊不受本次切法影響"""
from __future__ import annotations

import json

from app.models.job import JobRecord, BatchRecord
from app.utils.text_utils import new_id


class _Item:
    def __init__(self, row_id):
        self.row_id = row_id


def test_resume_rebuilds_batches_from_db_records(job_repo, batch_repo):
    # 模擬上次工作：3 批（完成 / retryable / pending）
    job = JobRecord.new("retention", 6)
    job_repo.create(job)
    for idx, status in enumerate(["completed", "retryable", "pending"]):
        ids = [f"r{idx*2}", f"r{idx*2+1}"]
        batch_repo.create(BatchRecord(batch_id=new_id("b_"), job_id=job.job_id,
                                        batch_index=idx, status=status,
                                        item_ids_json=json.dumps(ids)))

    # 本次傳入的項目順序刻意打亂、切法刻意與上次不同（4/2 而非 2/2/2）
    all_items = [_Item(f"r{i}") for i in [5, 3, 1, 0, 2, 4]]
    item_batches = [all_items[:4], all_items[4:]]

    # 與 BatchJobWorker.run() 內續跑重建邏輯一致
    existing = {b.batch_index: b for b in batch_repo.list_by_job(job.job_id)}
    item_lookup = {it.row_id: it for batch in item_batches for it in batch}
    rebuilt = []
    for idx in sorted(existing.keys()):
        ids = json.loads(existing[idx].item_ids_json)
        rebuilt.append([item_lookup[i] for i in ids if i in item_lookup])

    assert [it.row_id for it in rebuilt[0]] == ["r0", "r1"]
    assert [it.row_id for it in rebuilt[1]] == ["r2", "r3"]
    assert [it.row_id for it in rebuilt[2]] == ["r4", "r5"]


def test_list_resumable_only_returns_unfinished(job_repo):
    j1 = JobRecord.new("retention", 10)
    job_repo.create(j1)
    j2 = JobRecord.new("retention", 10)
    job_repo.create(j2)
    job_repo.update(j2.job_id, {"status": "completed"})
    j3 = JobRecord.new("scraping", 5)
    job_repo.create(j3)
    job_repo.update(j3.job_id, {"status": "cancelled"})

    resumable = job_repo.list_resumable()
    ids = {j.job_id for j in resumable}
    assert j1.job_id in ids
    assert j2.job_id not in ids
    assert j3.job_id not in ids

    retention_only = job_repo.list_resumable(job_type="retention")
    assert {j.job_id for j in retention_only} == {j1.job_id}
