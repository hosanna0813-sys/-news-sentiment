"""測試：瀏覽器渲染 fallback 觸發條件（V4.1.0）— 只有內容擷取層失敗才升級，合規性失敗不升級"""
from __future__ import annotations

from app.services.scraping.body_scraper import FetchOutcome
from app.workers.scraping_worker import _should_try_browser


def test_content_extraction_failure_triggers_browser():
    o = FetchOutcome(status="失敗", detail="未取得可用正文（無法辨識乾淨主文容器）")
    assert _should_try_browser(o) is True


def test_ssl_error_triggers_browser():
    """SSL 憑證錯誤屬憑證信任問題（Chromium 用系統憑證庫可正常連線），應觸發瀏覽器渲染"""
    o = FetchOutcome(status="失敗", detail="SSL 憑證錯誤（若您位於公司網路...）: cert verify failed")
    assert _should_try_browser(o) is True


def test_compliance_failures_do_not_trigger_browser():
    # robots / 付費牆 / 403 / 逾時等不應觸發瀏覽器渲染（不做規避）
    cases = [
        FetchOutcome(status="略過", detail="robots.txt 禁止抓取"),
        FetchOutcome(status="失敗", detail="偵測到登入牆／付費牆標記，不強行擷取"),
        FetchOutcome(status="失敗", detail="403 Forbidden（可能為反爬蟲或需登入）"),
        FetchOutcome(status="失敗", detail="逾時（超過 15 秒無回應）"),
        FetchOutcome(status="失敗", detail="404 Not Found"),
    ]
    for o in cases:
        assert _should_try_browser(o) is False, f"不應觸發: {o.detail}"


def test_success_does_not_trigger_browser():
    o = FetchOutcome(status="成功", detail="", body_text="正文", word_count=100)
    assert _should_try_browser(o) is False
