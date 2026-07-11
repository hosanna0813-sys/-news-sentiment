"""正文抓取頁 — 分群需要新聞正文才能判斷關聯性。

雲端 instance 上只做 requests+BeautifulSoup 這一段（BodyScraper，已存在於
app/services/scraping/body_scraper.py），略過桌面版的 Playwright 瀏覽器渲染
備援（額外佔用記憶體/磁碟，非本次範圍，見 README 排除範圍說明）。
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from app.web.server import get_context
from app.web.job_runner import start_batch_job, find_running_job_id, BatchOutcome
from app.repositories.news_repository import NewsRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.services.scraping.body_scraper import BodyScraper
from app.utils.text_utils import title_body_overlap

scraping_bp = Blueprint("scraping", __name__)

BATCH_SIZE = 5


@scraping_bp.route("/scraping")
def index():
    ctx = get_context()
    pending = ctx.news_repo.list_retained_without_body()
    done = [it for it in ctx.news_repo.list_all() if it.retained and it.has_body]
    return render_template("scraping.html", pending_count=len(pending), done_count=len(done),
                            job_id=request.args.get("job_id"))


def build_scraping_job_inputs(ctx):
    """回傳 (batches, process_fn)；沒有待抓取新聞時回傳 ([], None)。
    與 retention.py / clustering.py 的 build_*_job_inputs() 同樣理由：
    /scraping/run 與「一鍵完成」流程（app/web/routes/pipeline.py）共用同一份，
    不重複維護。"""
    items = ctx.news_repo.list_retained_without_body()
    if not items:
        return [], None

    scraping_settings = ctx.settings.scraping
    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]

    def process(batch_items):
        thread_news_repo = NewsRepository()
        scraper = BodyScraper(
            per_domain_delay_sec=scraping_settings.per_domain_delay_sec,
            timeout_sec=scraping_settings.request_timeout_sec,
            user_agent=scraping_settings.user_agent,
            respect_robots_txt=scraping_settings.respect_robots_txt,
            verify_ssl=scraping_settings.verify_ssl,
            site_selectors=scraping_settings.site_selectors,
        )
        updates = []
        success_count = 0
        skipped_count = 0
        for it in batch_items:
            if not it.url:
                updates.append({"row_id": it.row_id, "body_fetch_status": "失敗",
                                 "body_fetch_detail": "無網址可抓取", "body_source": "無正文"})
                continue
            outcome = scraper.fetch(it.url)
            if outcome.status == "成功":
                suspicious_reason = ""
                if outcome.word_count < 80:
                    suspicious_reason = f"正文長度異常短（{outcome.word_count} 字）"
                elif not title_body_overlap(it.title, outcome.body_text):
                    suspicious_reason = "正文與標題無關鍵字重疊，疑似抽錯內文"
                if suspicious_reason:
                    updates.append({"row_id": it.row_id, "body_text": outcome.body_text,
                                     "body_source": "網頁抓取正文", "body_fetch_status": "可疑",
                                     "body_fetch_detail": suspicious_reason,
                                     "body_quality_score": 0.1, "body_word_count": outcome.word_count})
                else:
                    success_count += 1
                    updates.append({"row_id": it.row_id, "body_text": outcome.body_text,
                                     "body_source": "網頁抓取正文", "body_fetch_status": "成功",
                                     "body_fetch_detail": outcome.detail,
                                     "body_quality_score": outcome.quality_score,
                                     "body_word_count": outcome.word_count})
            else:
                if outcome.status == "略過":
                    skipped_count += 1
                updates.append({"row_id": it.row_id, "body_fetch_status": outcome.status,
                                 "body_fetch_detail": outcome.detail})
        thread_news_repo.update_fields_bulk(updates)
        return BatchOutcome(success=True, success_count=success_count, skipped_count=skipped_count)

    return batches, process


@scraping_bp.route("/scraping/run", methods=["POST"])
def run():
    ctx = get_context()
    # 已有抓取（或一鍵完成）在跑：導回既有進度條，不重複開工作
    existing = find_running_job_id("scraping") or find_running_job_id("pipeline")
    if existing:
        return redirect(url_for("scraping.index", job_id=existing))
    batches, process = build_scraping_job_inputs(ctx)
    if not batches:
        return redirect(url_for("scraping.index"))
    job_id = start_batch_job("scraping", batches, process, JobRepository(), BatchRepository())
    return redirect(url_for("scraping.index", job_id=job_id))
