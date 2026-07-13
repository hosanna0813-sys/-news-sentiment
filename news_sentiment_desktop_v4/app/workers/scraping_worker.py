"""正文抓取 Worker — 對應規格八：僅抓取已留用新聞，可暫停/取消/續跑/重試

V4.1.0 新增：兩段式抓取策略
    第一段：BodyScraper（requests + BeautifulSoup，快速）
    第二段（可選）：PlaywrightScraper（瀏覽器渲染 + GNE），僅在第一段因
    「無法辨識乾淨主文容器」失敗、且設定啟用 use_browser_rendering 時觸發。
    robots.txt 禁止／付費牆／403 等合規性失敗不會觸發第二段（不做規避）。
"""
from __future__ import annotations

from typing import List, Optional

from app.models.news import NewsItem
from app.repositories.news_repository import NewsRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.repositories.scrape_stats_repository import ScrapeStatsRepository
from app.services.scraping.body_scraper import BodyScraper, FetchOutcome
from app.workers.batch_job_worker import BatchJobWorker, BatchOutcome
from app.utils.logging_setup import get_logger
import time

logger = get_logger("scraping_worker")

# 只有這類失敗才值得用瀏覽器渲染重試：
#   - 內容擷取層失敗（無法辨識乾淨主文容器）
#   - SSL 憑證錯誤（多為 Python certifi 不認得公司代理憑證；Chromium 使用
#     作業系統憑證庫，能正常建立信任連線，屬憑證信任問題而非反爬蟲規避）
# 合規性失敗（robots/付費牆/403）不進第二段
_RENDER_RETRY_HINTS = ("未取得可用正文", "無法辨識乾淨主文容器", "SSL 憑證錯誤")


def _should_try_browser(outcome: FetchOutcome) -> bool:
    return outcome.status == "失敗" and any(h in outcome.detail for h in _RENDER_RETRY_HINTS)


def build_scraping_worker(items: List[NewsItem], scraper: BodyScraper,
                           job_repo: JobRepository, batch_repo: BatchRepository,
                           batch_size: int = 5,
                           resume_job_id: Optional[str] = None,
                           browser_scraper_factory=None,
                           stats_repo=None, db_path=None) -> BatchJobWorker:
    """
    browser_scraper_factory: 可選，回傳已 start() 的 PlaywrightScraper 的工廠函式。
    stats_repo: 可選，ScrapeStatsRepository——記錄各站點成功率/耗時/連續失敗（V4.2.0）。
    db_path: 供背景執行緒重建 thread-local NewsRepository/ScrapeStatsRepository 用
    （不接受呼叫端傳入的 repo 物件——sqlite3 連線不可跨執行緒共用同一物件）。
    """
    from urllib.parse import urlparse
    from app.utils.text_utils import title_body_overlap
    from app.services.gmail.gmail_report_parser import NEWSPAPER_BODY_SOURCE
    # 報紙監測新聞本來就沒有原文連結（設計如此，非資料缺漏），排除在抓取之外，
    # 避免每次執行都被重複標記「無網址可抓取」失敗
    to_fetch = [it for it in items if it.body_fetch_status != "成功"
                and it.body_source not in ("Excel正文", NEWSPAPER_BODY_SOURCE)]
    batches = [to_fetch[i:i + batch_size] for i in range(0, len(to_fetch), batch_size)]

    # 以 dict 包裝讓閉包可改寫；瀏覽器實例跨批次共用，工作結束時由 worker 收尾
    browser_state = {"scraper": None, "failed": False}

    def _get_browser_scraper():
        if browser_scraper_factory is None or browser_state["failed"]:
            return None
        if browser_state["scraper"] is None:
            try:
                browser_state["scraper"] = browser_scraper_factory()
            except Exception as e:
                browser_state["failed"] = True
                logger.warning(f"瀏覽器渲染抓取器啟動失敗，本次工作僅使用一般抓取: {e}")
                return None
        return browser_state["scraper"]

    def process(batch_items: List[NewsItem]) -> BatchOutcome:
        # 在背景執行緒內重新建立 thread-local repo，不沿用呼叫端在主執行緒建立
        # 的 news_repo/stats_repo（sqlite3 連線不可跨執行緒共用同一物件，比照
        # retention_worker.py 的既有慣例）。
        thread_news_repo = NewsRepository(db_path)
        thread_stats_repo = ScrapeStatsRepository(db_path) if stats_repo is not None else None
        updates = []
        success_count = 0
        skipped_count = 0
        for it in batch_items:
            if not it.url:
                updates.append({
                    "row_id": it.row_id, "body_fetch_status": "失敗",
                    "body_fetch_detail": "無網址可抓取", "body_source": "無正文",
                })
                continue

            start_ts = time.time()
            outcome = scraper.fetch(it.url)
            body_source = "網頁抓取正文"

            # 第二段：瀏覽器渲染 fallback（僅限內容擷取層失敗）
            if _should_try_browser(outcome):
                browser = _get_browser_scraper()
                if browser is not None:
                    rendered = browser.fetch(it.url)
                    if rendered.status == "成功":
                        outcome = rendered
                        body_source = "網頁抓取正文（瀏覽器渲染）"
                    else:
                        outcome = FetchOutcome(
                            status=rendered.status if rendered.status == "略過" else "失敗",
                            detail=f"{outcome.detail}；瀏覽器渲染亦未成功：{rendered.detail}")

            # 正文品質檢查（V4.2.0）：與標題無關鍵字重疊或長度異常短 → 標記可疑，
            # 不進入分群/綜整階段（避免抓錯內文的髒資料汙染摘要）
            if outcome.status == "成功":
                suspicious_reason = ""
                if outcome.word_count < 80:
                    suspicious_reason = f"正文長度異常短（{outcome.word_count} 字）"
                elif not title_body_overlap(it.title, outcome.body_text):
                    suspicious_reason = "正文與標題無關鍵字重疊，疑似抽錯內文"
                if suspicious_reason:
                    outcome = FetchOutcome(status="可疑", detail=suspicious_reason,
                                            body_text=outcome.body_text,
                                            quality_score=0.1, word_count=outcome.word_count)

            elapsed = time.time() - start_ts
            domain = urlparse(it.url).netloc
            if thread_stats_repo is not None and domain:
                try:
                    thread_stats_repo.record(domain, outcome.status, elapsed, outcome.detail)
                except Exception as e:
                    logger.debug(f"站點統計寫入失敗: {e}")

            if outcome.status == "成功":
                success_count += 1
                updates.append({
                    "row_id": it.row_id, "body_text": outcome.body_text,
                    "body_source": body_source,
                    "body_fetch_status": "成功", "body_fetch_detail": outcome.detail,
                    "body_fetched_at": time.time(), "body_quality_score": outcome.quality_score,
                    "body_word_count": outcome.word_count,
                })
            elif outcome.status == "可疑":
                # 正文保留供人工檢視，但狀態標記可疑（後續分群/綜整會排除）
                updates.append({
                    "row_id": it.row_id, "body_text": outcome.body_text,
                    "body_source": body_source,
                    "body_fetch_status": "可疑", "body_fetch_detail": outcome.detail,
                    "body_fetched_at": time.time(), "body_quality_score": outcome.quality_score,
                    "body_word_count": outcome.word_count,
                })
            else:
                if outcome.status == "略過":
                    skipped_count += 1
                # 失敗時保留 Excel 摘要，不能覆蓋 body_text（規格八）
                updates.append({
                    "row_id": it.row_id, "body_fetch_status": outcome.status,
                    "body_fetch_detail": outcome.detail, "body_fetched_at": time.time(),
                })
        thread_news_repo.update_fields_bulk(updates)
        return BatchOutcome(success=True, success_count=success_count, skipped_count=skipped_count)

    # 工作結束（完成/取消/例外）時關閉瀏覽器。
    # V4.2.1：改走 BatchJobWorker 的 cleanup_fn（在 worker 執行緒的 finally 執行），
    # 不可接 finished_job signal——slot 會排到主執行緒且屆時 worker 執行緒已結束，
    # Playwright sync API 物件綁定建立執行緒，跨執行緒關閉會造成 driver EPIPE 崩潰。
    def _cleanup():
        s = browser_state.get("scraper")
        if s is not None:
            try:
                s.close()
            except Exception as e:
                logger.warning(f"關閉瀏覽器渲染抓取器時發生錯誤: {e}")
            browser_state["scraper"] = None

    worker = BatchJobWorker(
        job_type="scraping", item_batches=batches, process_batch_fn=process,
        job_repo=job_repo, batch_repo=batch_repo, resume_job_id=resume_job_id,
        job_label_fn=lambda it: it.row_id, cleanup_fn=_cleanup,
    )
    return worker
