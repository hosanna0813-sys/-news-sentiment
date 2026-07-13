"""測試：報紙新聞監測報告匯入（V4.4.1）

XKM 同一寄件者的第二種版型——純表格（則數/監測日期/媒體/媒體名稱/主版位/版位/
訊息標題/記者/廣告效益），沒有原文連結、沒有內文。已用真實信件驗證解析
（49 則、三個區塊全數正確），這裡用合成 HTML 覆蓋回歸。
"""
from __future__ import annotations

from app.models.news import NewsItem
from app.services.gmail.gmail_report_parser import parse_report_html, NEWSPAPER_BODY_SOURCE
from app.services.clustering.clustering_service import split_insufficient_body


def _row(idx, media, page, section, title, author, ad="－"):
    cells = [f"{idx}.", "2026-07-13", "報紙", media, page, section, title, author, ad]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


_NEWSPAPER_HTML = f"""
<html><body><table>
<tr><td>20260713 內政部_報紙新聞監測新聞專屬監測報告 (總計 3 則)</td></tr>
<tr><td>內政部-共 2 則</td><td>社論投書-共 1 則</td></tr>
<tr><td>則數</td><td>監測日期</td><td>媒體</td><td>媒體名稱</td><td>主版位</td>
    <td>版位</td><td>訊息標題</td><td>記者</td><td>廣告效益</td></tr>
<tr><td>內政部－共2則</td></tr>
{_row(1, "中國時報", "A09", "中彰投新聞", "台中 不爽想開槍 國道警控同仁恐嚇霸凌 調查中", "温予菱")}
{_row(2, "聯合報", "A12", "司法", "與警員口角 小隊長拍槍套涉恐嚇", "游振昇", "93352")}
<tr><td>社論投書－共1則</td></tr>
{_row(1, "聯合報", "A02", "焦點", "社論 台灣需要「跟老天對賭」的政府與決策？", "社論")}
</table></body></html>
"""


def test_newspaper_report_parsed_with_sections_and_no_url():
    items = parse_report_html(_NEWSPAPER_HTML, import_batch_id="b1")
    assert len(items) == 3
    assert [it.tags for it in items] == ["內政部", "內政部", "社論投書"]

    first = items[0]
    assert first.title.startswith("台中")
    assert first.source == "中國時報"
    assert first.channel == "報紙 A09 中彰投新聞"   # 版位是留用判斷的重要訊號
    assert first.author == "温予菱"
    assert first.published_at == "2026-07-13"
    assert first.url == ""                            # 報紙報告沒有原文連結
    assert first.body_text == ""
    assert first.body_source == NEWSPAPER_BODY_SOURCE
    assert first.body_fetch_status == "略過"
    assert first.import_batch_id == "b1"


def test_newspaper_header_and_nav_rows_are_ignored():
    """表頭列（則數/監測日期/...）與目錄導覽列（多欄統計）不可被誤認成新聞"""
    items = parse_report_html(_NEWSPAPER_HTML)
    assert all("則數" not in it.title and "共 2 則" not in it.title for it in items)


def test_web_format_detection_unaffected():
    """網路版信件（有【日期 來源 - 版面 記者】格式）仍走原本的狀態機解析"""
    web_html = """
    <html><body>
    <p>測試標題一</p>
    <p>【2026-07-13 自由時報 - 焦點 記者甲】</p>
    <p>這是正文內容，測試標題一的細節說明。</p>
    <a href="https://example.com/news/1">Source</a>
    <p>【Back】</p>
    </body></html>
    """
    items = parse_report_html(web_html)
    assert len(items) == 1
    assert items[0].title == "測試標題一"
    assert items[0].url == "https://example.com/news/1"
    assert items[0].body_source == "Gmail正文"


def test_unrecognized_html_returns_empty():
    assert parse_report_html("<html><body><p>不是監測報告</p></body></html>") == []


def test_newspaper_items_are_clusterable_by_title():
    """報紙新聞沒有正文屬設計使然，split_insufficient_body 應放行參與分群，
    不可落入「正文不足待人工確認」"""
    newspaper = NewsItem(row_id="n1", title="報紙新聞標題", source="聯合報",
                          published_at="2026-07-13", body_text="",
                          body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="略過")
    web_no_body = NewsItem(row_id="w1", title="網路新聞標題", source="自由時報",
                            published_at="2026-07-13", body_text="", body_source="無正文")
    ok, insufficient = split_insufficient_body([newspaper, web_no_body])
    assert [it.row_id for it in ok] == ["n1"]
    assert [it.row_id for it in insufficient] == ["w1"]


def test_newspaper_items_excluded_from_scraping(job_repo, batch_repo, tmp_db_path):
    """報紙新聞沒有網址可抓，不進抓取批次（否則每次執行都被標「無網址可抓取」失敗）"""
    from app.services.scraping.body_scraper import BodyScraper
    from app.workers.scraping_worker import build_scraping_worker
    newspaper = NewsItem(row_id="n1", title="報紙新聞", source="聯合報",
                          published_at="2026-07-13",
                          body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="略過")
    web_item = NewsItem(row_id="w1", title="網路新聞", source="自由時報",
                         published_at="2026-07-13", url="https://example.com/1")
    worker = build_scraping_worker([newspaper, web_item], BodyScraper(),
                                     job_repo, batch_repo, db_path=tmp_db_path)
    batched_ids = [it.row_id for batch in worker.item_batches for it in batch]
    assert batched_ids == ["w1"]
