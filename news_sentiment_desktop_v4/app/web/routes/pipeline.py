"""一鍵完成：匯入 → 抓正文 → 留用初判 → 議題分群，串成一個背景流程，跑完後
直接把使用者帶到議題分群頁做人工調整——不必依序手動點四個步驟的按鈕。

每一段都直接呼叫對應頁面已經寫好的 build_*_job_inputs()（見
retention.py / clustering.py / scraping.py），核心批次處理邏輯只有一份，
這裡單純負責「依序跑、跑完換下一段」的排程；用 job_runner.run_batch_job_sync()
（而不是 start_batch_job()）是因為這個流程本身已經在自己的背景執行緒裡，
不需要再巢狀開一個執行緒。

重要：build_*_job_inputs(ctx) 內部直接讀 ctx.news_repo / ctx.topic_repo /
ctx.prompt_repo——這在各自的路由（/scraping/run 等）裡沒問題，因為那些呼叫
都在處理當次 HTTP request 的主執行緒上執行，跟 ctx 這些 repo 物件當初建立時
是同一個執行緒。但「一鍵完成」的四個階段全部要在一個背景執行緒裡依序跑完，
若直接把 ctx 傳進去，這些 repo 綁定的 SQLite 連線就會被背景執行緒與主執行緒
（同時處理其他頁面請求，例如使用者剛好在瀏覽 /clustering）並行存取同一個
connection 物件，可能導致間歇性查詢失敗、悄悄跳過某個階段。_ThreadLocalCtx
提供在背景執行緒內重新建立的 repo（各自透過 get_connection() 取得正確的
thread-local 連線），settings／gateway 是純記憶體物件、沒有連線綁定，可以
安全沿用主執行緒的 ctx。
"""
from __future__ import annotations

import json
import threading
import time

from flask import Blueprint, flash, redirect, request, url_for

from app.web.server import get_context
from app.web.job_runner import run_batch_job_sync, has_any_running_job
from app.web.routes.import_gmail import parse_taipei_datetime
from app.web.routes.retention import build_retention_job_inputs
from app.web.routes.clustering import build_clustering_job_inputs
from app.web.routes.scraping import build_scraping_job_inputs
from app.repositories.news_repository import NewsRepository
from app.repositories.topic_repository import TopicRepository
from app.repositories.settings_repository import PromptRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.models.job import JobRecord
from app.services.gmail.gmail_importer import import_from_gmail, GmailImportError
from app.utils.logging_setup import get_logger

logger = get_logger("web_pipeline")
pipeline_bp = Blueprint("pipeline", __name__)

STAGE_LABELS = ["匯入 Gmail 信件", "抓取新聞正文", "AI 留用初判", "AI 議題分群"]


class _ThreadLocalCtx:
    """給背景執行緒用的輕量代理，只重建 build_*_job_inputs() 實際用得到的
    repo（news/topic/prompt），settings 與 gateway 沿用主執行緒的 ctx。"""

    def __init__(self, ctx):
        self.settings = ctx.settings
        self.gateway = ctx.gateway
        self.news_repo = NewsRepository()
        self.topic_repo = TopicRepository()
        self.prompt_repo = PromptRepository()


def _set_stage(job_repo, job_id, stage_index, label, sub_job_id=None, **extra_fields):
    """sub_job_id：這個階段實際批次工作（scraping/retention/clustering）的
    job_id，讓前端可以另外輪詢它自己的批次進度（第幾批、成功/失敗筆數），
    而不是只看到一句話從頭到尾都不變的階段名稱。"""
    params = {"stage_label": label, "stage_index": stage_index, "stage_count": len(STAGE_LABELS)}
    if sub_job_id:
        params["sub_job_id"] = sub_job_id
    job_repo.update(job_id, {
        "progress_current": stage_index,
        "params_json": json.dumps(params, ensure_ascii=False),
        **extra_fields,
    })


@pipeline_bp.route("/pipeline/run", methods=["POST"])
def run():
    ctx = get_context()
    # 一鍵完成會依序動到所有資料表——任何工作還在跑都不該再疊一個流程上去
    if has_any_running_job():
        flash("目前已有工作正在執行中，請等它完成後再啟動一鍵完成", "error")
        return redirect(url_for("dashboard.index"))
    try:
        start_dt = parse_taipei_datetime(request.form["start_dt"])
        end_dt = parse_taipei_datetime(request.form["end_dt"])
    except (KeyError, ValueError):
        flash("請填寫正確的起訖時間", "error")
        return redirect(url_for("dashboard.index"))

    job = JobRecord.new("pipeline", len(STAGE_LABELS))
    JobRepository().create(job)
    _set_stage(JobRepository(), job.job_id, 0, STAGE_LABELS[0], status="running", started_at=time.time())

    def _run():
        # job_repo/batch_repo 在背景執行緒內重新建立（thread-local 連線），
        # 不沿用主執行緒建立的物件，理由見檔案開頭說明。
        job_repo = JobRepository()
        batch_repo = BatchRepository()
        thread_ctx = _ThreadLocalCtx(ctx)
        try:
            # 1. 匯入
            _set_stage(job_repo, job.job_id, 0, "連接 Gmail、搜尋符合條件的信件中...")
            result = import_from_gmail(thread_ctx.settings.gmail, start_dt, end_dt)
            from app.services.gmail.gmail_report_parser import repair_newspaper_rows
            to_insert, _repaired = repair_newspaper_rows(thread_ctx.news_repo, result.items)
            thread_ctx.news_repo.upsert_many(to_insert)
            _set_stage(job_repo, job.job_id, 1, f"已成功匯入 {len(result.items)} 則新聞，開始抓取正文")

            # 2. 抓正文
            batches, process = build_scraping_job_inputs(thread_ctx)
            if batches:
                run_batch_job_sync(
                    "scraping", batches, process, job_repo, batch_repo,
                    on_job_created=lambda sub_id: _set_stage(
                        job_repo, job.job_id, 1, "抓取新聞正文中", sub_job_id=sub_id),
                )
            scraped_count = sum(1 for it in thread_ctx.news_repo.list_all() if it.has_body)
            _set_stage(job_repo, job.job_id, 2, f"已取得正文 {scraped_count} 則，開始 AI 留用初判")

            # 3. 留用初判
            batches, process = build_retention_job_inputs(thread_ctx)
            if batches:
                run_batch_job_sync(
                    "retention", batches, process, job_repo, batch_repo,
                    on_job_created=lambda sub_id: _set_stage(
                        job_repo, job.job_id, 2, "AI 留用初判中", sub_job_id=sub_id),
                )
            retained_count = sum(1 for it in thread_ctx.news_repo.list_all() if it.retained)
            _set_stage(job_repo, job.job_id, 3, f"留用初判完成，共留用 {retained_count} 則，開始 AI 議題分群")

            # 4. 議題分群（預設增量，維持已存在的議題結構）
            batches, process = build_clustering_job_inputs(thread_ctx, incremental=True)
            if batches:
                run_batch_job_sync(
                    "clustering", batches, process, job_repo, batch_repo,
                    on_job_created=lambda sub_id: _set_stage(
                        job_repo, job.job_id, 3, "AI 議題分群中", sub_job_id=sub_id),
                )
            topic_count = len(thread_ctx.topic_repo.list_active())
            summary = f"完成！留用 {retained_count} 則，共分成 {topic_count} 個議題"
            if retained_count and not topic_count:
                summary += "（沒有議題可能是留用新聞的正文都不足，可到議題分群頁人工確認）"
            _set_stage(job_repo, job.job_id, 4, summary, status="completed", finished_at=time.time())
        except GmailImportError as e:
            logger.warning(f"一鍵完成：Gmail 匯入失敗: {e}")
            _set_stage(job_repo, job.job_id, 0, f"匯入失敗：{e}", status="failed", finished_at=time.time())
        except Exception as e:
            logger.exception("一鍵完成流程發生未預期錯誤")
            _set_stage(job_repo, job.job_id, 0, f"發生未預期錯誤：{e}", status="failed", finished_at=time.time())

    threading.Thread(target=_run, name=f"pipeline-{job.job_id[:8]}", daemon=True).start()
    return redirect(url_for("clustering.index", job_id=job.job_id))
