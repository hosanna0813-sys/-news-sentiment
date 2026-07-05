"""一鍵完成：匯入 → 抓正文 → 留用初判 → 議題分群，串成一個背景流程，跑完後
直接把使用者帶到議題分群頁做人工調整——不必依序手動點四個步驟的按鈕。

每一段都直接呼叫對應頁面已經寫好的 build_*_job_inputs()（見
retention.py / clustering.py / scraping.py），核心批次處理邏輯只有一份，
這裡單純負責「依序跑、跑完換下一段」的排程；用 job_runner.run_batch_job_sync()
（而不是 start_batch_job()）是因為這個流程本身已經在自己的背景執行緒裡，
不需要再巢狀開一個執行緒。
"""
from __future__ import annotations

import datetime
import json
import threading
import time

from flask import Blueprint, flash, redirect, request, url_for

from app.web.server import get_context
from app.web.job_runner import run_batch_job_sync
from app.web.routes.retention import build_retention_job_inputs
from app.web.routes.clustering import build_clustering_job_inputs
from app.web.routes.scraping import build_scraping_job_inputs
from app.repositories.news_repository import NewsRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.models.job import JobRecord
from app.services.gmail.gmail_importer import import_from_gmail, GmailImportError
from app.utils.logging_setup import get_logger

logger = get_logger("web_pipeline")
pipeline_bp = Blueprint("pipeline", __name__)

STAGE_LABELS = ["匯入 Gmail 信件", "抓取新聞正文", "AI 留用初判", "AI 議題分群"]


def _set_stage(job_repo, job_id, stage_index, label, **extra_fields):
    job_repo.update(job_id, {
        "progress_current": stage_index,
        "params_json": json.dumps({"stage_label": label, "stage_index": stage_index,
                                    "stage_count": len(STAGE_LABELS)}, ensure_ascii=False),
        **extra_fields,
    })


@pipeline_bp.route("/pipeline/run", methods=["POST"])
def run():
    ctx = get_context()
    try:
        start_dt = datetime.datetime.fromisoformat(request.form["start_dt"])
        end_dt = datetime.datetime.fromisoformat(request.form["end_dt"])
    except (KeyError, ValueError):
        flash("請填寫正確的起訖時間", "error")
        return redirect(url_for("dashboard.index"))

    job_repo = JobRepository()
    batch_repo = BatchRepository()
    job = JobRecord.new("pipeline", len(STAGE_LABELS))
    job_repo.create(job)
    _set_stage(job_repo, job.job_id, 0, STAGE_LABELS[0], status="running", started_at=time.time())

    def _run():
        try:
            # 1. 匯入
            result = import_from_gmail(ctx.settings.gmail, start_dt, end_dt)
            NewsRepository().upsert_many(result.items)
            _set_stage(job_repo, job.job_id, 1, STAGE_LABELS[1])

            # 2. 抓正文
            batches, process = build_scraping_job_inputs(ctx)
            if batches:
                run_batch_job_sync("scraping", batches, process, job_repo, batch_repo)
            _set_stage(job_repo, job.job_id, 2, STAGE_LABELS[2])

            # 3. 留用初判
            batches, process = build_retention_job_inputs(ctx)
            if batches:
                run_batch_job_sync("retention", batches, process, job_repo, batch_repo)
            _set_stage(job_repo, job.job_id, 3, STAGE_LABELS[3])

            # 4. 議題分群（預設增量，維持已存在的議題結構）
            batches, process = build_clustering_job_inputs(ctx, incremental=True)
            if batches:
                run_batch_job_sync("clustering", batches, process, job_repo, batch_repo)

            _set_stage(job_repo, job.job_id, 4, "完成", status="completed", finished_at=time.time())
        except GmailImportError as e:
            logger.warning(f"一鍵完成：Gmail 匯入失敗: {e}")
            _set_stage(job_repo, job.job_id, 0, f"匯入失敗：{e}", status="failed", finished_at=time.time())
        except Exception as e:
            logger.exception("一鍵完成流程發生未預期錯誤")
            _set_stage(job_repo, job.job_id, 0, f"發生未預期錯誤：{e}", status="failed", finished_at=time.time())

    threading.Thread(target=_run, name=f"pipeline-{job.job_id[:8]}", daemon=True).start()
    return redirect(url_for("clustering.index", job_id=job.job_id))
