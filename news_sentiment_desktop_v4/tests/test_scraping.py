"""測試：正文抓取狀態保存（規格八：成功正文永久保存、失敗保留 Excel 摘要不覆蓋）"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from app.services.scraping.body_scraper import BodyScraper, FetchOutcome
from app.models.news import NewsItem


class _FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_success_extracts_article_body():
    html = """
    <html><body>
    <nav>導覽列雜訊</nav>
    <article>
        <h1>標題</h1>
        <p>這是第一段正文內容，內容足夠長以通過字數門檻測試這是第一段正文內容。</p>
        <p>這是第二段正文內容，繼續補充事件細節與相關背景說明資訊。</p>
        <p>延伸閱讀：其他相關報導連結列表在此，不應被納入正文。</p>
        <p>這段文字在延伸閱讀標記之後，不應該出現在擷取結果中。</p>
    </article>
    </body></html>
    """
    scraper = BodyScraper(per_domain_delay_sec=0, respect_robots_txt=False)
    with patch.object(scraper, "_session_get", return_value=_FakeResp(200, html)):
        outcome = scraper.fetch("https://example.com/news/1")

    assert outcome.status == "成功"
    assert "第一段正文內容" in outcome.body_text
    assert "延伸閱讀" not in outcome.body_text
    assert "在延伸閱讀標記之後" not in outcome.body_text
    assert outcome.word_count > 0


def test_fetch_403_classified_as_failure():
    scraper = BodyScraper(per_domain_delay_sec=0, respect_robots_txt=False)
    with patch.object(scraper, "_session_get", return_value=_FakeResp(403, "")):
        outcome = scraper.fetch("https://example.com/blocked")
    assert outcome.status == "失敗"
    assert "403" in outcome.detail


def test_fetch_failure_does_not_overwrite_excel_summary(news_repo, tmp_db_path):
    """失敗時保留 Excel 摘要，不能覆蓋 body_text（規格八）"""
    item = NewsItem(row_id="r1", title="測試", summary="Excel摘要內容",
                     excel_body="", body_text="", url="https://example.com/x")
    news_repo.upsert_one(item)

    scraper = BodyScraper(per_domain_delay_sec=0, respect_robots_txt=False)
    with patch.object(scraper, "_session_get", return_value=_FakeResp(404, "")):
        outcome = scraper.fetch(item.url)

    assert outcome.status == "失敗"
    # 模擬 worker 邏輯：失敗時只更新狀態欄位，不觸碰 body_text / summary
    news_repo.update_fields(item.row_id, {
        "body_fetch_status": outcome.status, "body_fetch_detail": outcome.detail,
    })
    reloaded = news_repo.get(item.row_id)
    assert reloaded.body_fetch_status == "失敗"
    assert reloaded.summary == "Excel摘要內容"  # 摘要未被覆蓋
    assert reloaded.body_text == ""             # 未被硬塞錯誤內容
