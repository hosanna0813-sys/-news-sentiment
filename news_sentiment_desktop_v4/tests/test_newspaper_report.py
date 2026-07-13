"""測試：報紙新聞監測報告匯入（V4.4.1）

XKM 同一寄件者的第二種版型——純表格（則數/監測日期/媒體/媒體名稱/主版位/版位/
訊息標題/記者/廣告效益）。標題儲存格的超連結指向 XKM 剪報全文頁（免登入），
當作 url 交給正文抓取（div.dataView 站點 selector）。已用真實信件驗證解析
（49 則、三個區塊、49 個連結全數正確）與真實頁面抓取，這裡用合成 HTML 覆蓋回歸。
"""
from __future__ import annotations

from app.models.news import NewsItem
from app.services.gmail.gmail_report_parser import parse_report_html, NEWSPAPER_BODY_SOURCE
from app.services.clustering.clustering_service import split_insufficient_body

_XKM_URL = "http://rmbjbtw.rmb.com.tw/INF_List_NP.asp?SortStr=INF&ID=NPL1&ToUrl=1&Title="


def _row(idx, media, page, section, title, author, ad="－", url=_XKM_URL):
    title_cell = f"<a href='{url}' target='_blank'>{title}</a>" if url else title
    cells = [f"{idx}.", "2026-07-13", "報紙", media, page, section, title_cell, author, ad]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


_NEWSPAPER_HTML = f"""
<html><body><table>
<tr><td>20260713 內政部_報紙新聞監測新聞專屬監測報告 (總計 3 則)</td></tr>
<tr><td>內政部-共 2 則</td><td>社論投書-共 1 則</td></tr>
<tr><td>則數</td><td>監測日期</td><td>媒體</td><td>媒體名稱</td><td>主版位</td>
    <td>版位</td><td>訊息標題</td><td>記者</td><td>廣告效益</td></tr>
<tr><td>內政部－共2則</td></tr>
{_row(1, "中國時報", "A09", "中彰投新聞", "台中 不爽想開槍 國道警控同仁恐嚇霸凌 調查中", "温予菱")}
{_row(2, "聯合報", "A12", "司法", "與警員口角 小隊長拍槍套涉恐嚇", "游振昇", "93352", url="")}
<tr><td>社論投書－共1則</td></tr>
{_row(1, "聯合報", "A02", "焦點", "社論 台灣需要「跟老天對賭」的政府與決策？", "社論")}
</table></body></html>
"""


def test_newspaper_report_parsed_with_sections_and_urls():
    items = parse_report_html(_NEWSPAPER_HTML, import_batch_id="b1")
    assert len(items) == 3
    assert [it.tags for it in items] == ["內政部", "內政部", "社論投書"]

    first = items[0]
    assert first.title.startswith("台中")
    assert first.source == "中國時報"
    assert first.channel == "報紙 A09 中彰投新聞"   # 版位是留用判斷的重要訊號
    assert first.author == "温予菱"
    assert first.published_at == "2026-07-13"
    assert first.url == _XKM_URL                      # 標題超連結＝XKM 剪報全文頁
    assert first.body_text == ""
    assert first.body_source == NEWSPAPER_BODY_SOURCE
    assert first.body_fetch_status == "未抓取"        # 有連結 → 交給正文抓取
    assert first.import_batch_id == "b1"


def test_newspaper_row_without_link_marked_skipped():
    """個別列沒有全文連結時不進抓取（標「略過」），留用/分群退回以標題判斷"""
    items = parse_report_html(_NEWSPAPER_HTML)
    no_link = items[1]
    assert no_link.title.startswith("與警員口角")
    assert no_link.url == ""
    assert no_link.body_fetch_status == "略過"


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


def test_xkm_site_selector_extracts_dataview_body():
    """XKM 剪報頁預設 selector（div.dataView）——已用真實頁面驗證，這裡回歸合成頁"""
    from app.models.settings import ScrapingSettings
    from app.services.scraping.body_scraper import BodyScraper
    cfg = ScrapingSettings()
    assert cfg.site_selectors.get("rmbjbtw.rmb.com.tw") == "div.dataView"
    scraper = BodyScraper(site_selectors=cfg.site_selectors)
    body_text = "剪報全文第一段內容。" * 8   # selector 擷取要求至少 50 字才視為有效主文
    page = ("<html><body><div class='header'>登入 友善列印 關閉視窗</div>"
            f"<div class='dataView'>{body_text}</div>"
            "<div class='footer'>版權聲明</div></body></html>")
    body = scraper._extract_by_site_selector(_XKM_URL, page)
    assert "剪報全文第一段內容" in body
    assert "友善列印" not in body


def test_site_selector_defaults_merged_into_saved_settings(tmp_db_path):
    """既有安裝存過設定後，程式新版新增的預設站點 selector 仍要能送達
    （儲存的 site_selectors 會整包覆蓋預設 dict，load 時補回缺少的預設站點）"""
    from app.repositories.settings_repository import AppSettingsRepository
    from app.models.settings import AppSettings
    repo = AppSettingsRepository(tmp_db_path)
    old = AppSettings()
    old.scraping.site_selectors = {"setn.com": "#Content1"}   # 模擬舊版存檔（無 XKM）
    repo.save(old)

    loaded = repo.load()
    assert loaded.scraping.site_selectors["rmbjbtw.rmb.com.tw"] == "div.dataView"
    assert loaded.scraping.site_selectors["setn.com"] == "#Content1"   # 使用者值不被覆蓋


def test_reimport_repairs_missing_urls_without_duplicates(news_repo):
    """V4.4.1 之前匯入的報紙新聞沒有連結（url 空、狀態「略過」）。重新匯入
    同一封報告時：既有列補上連結並解除略過（人工留用成果保留），不插入重複列；
    真正的新新聞照常新增。"""
    from app.services.gmail.gmail_report_parser import repair_newspaper_rows
    old_row = NewsItem(row_id="old1", title="與警員口角 小隊長拍槍套涉恐嚇",
                        source="聯合報", published_at="2026-07-13",
                        body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="略過",
                        retained=False, retention_status="人工不留用",
                        retention_judged_by="human")
    news_repo.upsert_one(old_row)

    reimported = [
        NewsItem(row_id="new1", title="與警員口角 小隊長拍槍套涉恐嚇",
                  source="聯合報", published_at="2026-07-13", url=_XKM_URL,
                  body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="未抓取"),
        NewsItem(row_id="new2", title="另一則新的報紙新聞",
                  source="中國時報", published_at="2026-07-13", url=_XKM_URL,
                  body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="未抓取"),
    ]
    to_insert, repaired = repair_newspaper_rows(news_repo, reimported)

    assert repaired == 1
    assert [it.row_id for it in to_insert] == ["new2"]   # 既有列不重複插入
    fixed = news_repo.get("old1")
    assert fixed.url == _XKM_URL
    assert fixed.body_fetch_status == "未抓取"            # 解除「略過」，可進抓取
    assert fixed.retention_judged_by == "human"           # 人工判斷完全保留
    assert fixed.retention_status == "人工不留用"


def test_repair_is_noop_for_web_news(news_repo):
    """網路新聞監測報告不受修補邏輯影響（原樣插入）"""
    from app.services.gmail.gmail_report_parser import repair_newspaper_rows
    web_items = [NewsItem(row_id="w1", title="網路新聞", source="自由時報",
                           published_at="2026-07-13", url="https://example.com/1",
                           body_source="Gmail正文")]
    to_insert, repaired = repair_newspaper_rows(news_repo, web_items)
    assert repaired == 0 and to_insert == web_items


def test_newspaper_items_without_body_are_clusterable_by_title():
    """報紙新聞抓取前（或抓取失敗）沒有正文，split_insufficient_body 應放行
    以標題參與分群，不可落入「正文不足待人工確認」"""
    newspaper = NewsItem(row_id="n1", title="報紙新聞標題", source="聯合報",
                          published_at="2026-07-13", body_text="",
                          body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="未抓取")
    web_no_body = NewsItem(row_id="w1", title="網路新聞標題", source="自由時報",
                            published_at="2026-07-13", body_text="", body_source="無正文")
    ok, insufficient = split_insufficient_body([newspaper, web_no_body])
    assert [it.row_id for it in ok] == ["n1"]
    assert [it.row_id for it in insufficient] == ["w1"]


def test_scraping_includes_linked_newspaper_and_excludes_unlinked(job_repo, batch_repo, tmp_db_path):
    """有 XKM 連結的報紙新聞照常進抓取批次；沒連結的列排除（否則每次執行
    都被標「無網址可抓取」失敗）"""
    from app.services.scraping.body_scraper import BodyScraper
    from app.workers.scraping_worker import build_scraping_worker
    linked = NewsItem(row_id="n1", title="有連結報紙新聞", source="聯合報",
                       published_at="2026-07-13", url=_XKM_URL,
                       body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="未抓取")
    unlinked = NewsItem(row_id="n2", title="無連結報紙新聞", source="聯合報",
                         published_at="2026-07-13",
                         body_source=NEWSPAPER_BODY_SOURCE, body_fetch_status="略過")
    web_item = NewsItem(row_id="w1", title="網路新聞", source="自由時報",
                         published_at="2026-07-13", url="https://example.com/1")
    worker = build_scraping_worker([linked, unlinked, web_item], BodyScraper(),
                                     job_repo, batch_repo, db_path=tmp_db_path)
    batched_ids = {it.row_id for batch in worker.item_batches for it in batch}
    assert batched_ids == {"n1", "w1"}
