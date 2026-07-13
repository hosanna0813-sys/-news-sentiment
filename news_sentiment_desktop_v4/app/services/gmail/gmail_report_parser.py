"""
競業信息知識管理系統（XKM）「新聞專屬監測報告」信件內文解析

同一寄件者有兩種版型，parse_report_html() 會自動判別：

1. 網路新聞監測報告——每則新聞的詳情區塊：
    {標題}
    【{YYYY-MM-DD} {媒體來源} - {版面} {記者}】
    {正文...}
    [可能夾雜 Prismintelligence / NewsScope AI 推薦問答區塊 —— 廠商加值功能，非新聞內容]
    Source   <- 超連結，指向原始新聞網址
    【Back】  <- 回目錄錨點，標記本則新聞結束

2. 報紙新聞監測報告（V4.4.1 新增）——純表格，欄位：
    則數｜監測日期｜媒體｜媒體名稱｜主版位｜版位｜訊息標題｜記者｜廣告效益
   以「{區塊名}－共N則」單欄列分區（內政部／三大報頭版／社論投書）。
   標題儲存格的超連結指向 XKM 系統的剪報全文頁（免登入可讀），當作 url 交給
   正文抓取階段取回剪報全文（該站的主文容器 div.dataView 已列入
   ScrapingSettings.site_selectors 預設）；個別列沒有連結時退回以標題參與
   留用初判與議題分群。匯入後標記 body_source=NEWSPAPER_BODY_SOURCE。

網路版解析策略：**依真實 HTML 文件順序（`soup.descendants`）單一遍歷，維護一個
「目前是否有新聞正在處理中」的狀態機**，而非分開抓文字結構跟全域 Source 連結清單
後再用位置索引 zip 對應——後者一旦文件中有任何一則新聞缺少 Source 連結，後面所有
新聞的連結就會系統性錯位一格（已用真實資料驗證到這個問題）。狀態機作法讓每則新聞
的 URL 只認自己這一段範圍內第一個命中的 Source 連結，缺連結時只有那一則留空，
不會波及其他則。

單則格式不符時只記警告並跳過，不中斷整批（比照 model_gateway / clustering 對模型
輸出的防禦性解析風格）。
"""
from __future__ import annotations

import re
from typing import List, Optional, Dict, Any

from app.models.news import NewsItem
from app.utils.text_utils import new_id, normalize_whitespace, word_count_cjk_aware, title_body_overlap
from app.utils.logging_setup import get_logger

logger = get_logger("gmail_report_parser")

_BRACKET_RE = re.compile(r"^【(\d{4}-\d{2}-\d{2})\s+(.+?)\s*-\s*(\S+)\s+(\S+)】$")

# 報紙監測新聞的 body_source 標記——正文抓取（無網址可抓）與分群的「正文不足」
# 判斷（改以標題參與）都依這個值辨識報紙來源，見 scraping_worker.py 與
# clustering_service.split_insufficient_body()
NEWSPAPER_BODY_SOURCE = "報紙監測（無原文連結）"

# 報紙版表格：區塊標題列（例：內政部－共24則）與資料列首欄（例：1.）
_NEWSPAPER_SECTION_RE = re.compile(r"^(.+?)[－\-—]共\s*(\d+)\s*則$")
_NEWSPAPER_ROW_INDEX_RE = re.compile(r"^\d+\.?$")
_NEWSPAPER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_NOISE_LINE_PREFIXES = (
    "Prismintelligence", "NewsScope", "您的專屬推薦", "Source", "top", "【Back】",
)


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return any(stripped.startswith(p) for p in _NOISE_LINE_PREFIXES)


def _extract_articles(soup) -> List[Dict[str, Any]]:
    """依文件順序走訪整份 HTML，逐一切出每則新聞的中繼資料/標題/正文/URL。"""
    from bs4 import NavigableString, Tag

    articles: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_text = ""  # 目前不在任何新聞區塊內時，最近一次看到的非空白文字（用來判斷標題）

    def close_current():
        nonlocal current
        if current is not None:
            articles.append(current)
            current = None

    for node in soup.descendants:
        if isinstance(node, NavigableString):
            text = str(node).strip()
            if not text:
                continue
            m = _BRACKET_RE.match(text)
            if m:
                close_current()  # 防禦：理論上不應該發生（前一則該已被【Back】關閉）
                title = last_text if last_text and not _is_noise_line(last_text) else ""
                current = {
                    "published_at": m.group(1), "source": m.group(2),
                    "channel": m.group(3), "author": m.group(4),
                    "title": title, "body_lines": [], "url": "",
                }
                last_text = ""
                continue
            if text == "【Back】":
                close_current()
                last_text = ""
                continue
            if current is not None:
                if not _is_noise_line(text):
                    current["body_lines"].append(text)
            else:
                last_text = text
        elif isinstance(node, Tag) and node.name == "a":
            if current is not None and not current["url"]:
                if (node.get_text() or "").strip() == "Source":
                    current["url"] = node.get("href", "")

    close_current()  # 防禦：文件結尾沒有【Back】收尾的情況
    return articles


def _extract_newspaper_articles(soup) -> List[Dict[str, Any]]:
    """報紙版表格解析：逐 <tr> 走訪，單欄的「{區塊名}－共N則」列切換目前區塊，
    9 欄以上且首欄是流水號（1.）的列視為一則新聞。表頭列（含「訊息標題」）與
    目錄導覽列（多欄但非流水號開頭）自然被略過。"""
    articles: List[Dict[str, Any]] = []
    section = ""
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        if len(cells) == 1:
            m = _NEWSPAPER_SECTION_RE.match(cells[0].replace(" ", ""))
            if m:
                section = m.group(1)
            continue
        if len(cells) < 9 or not _NEWSPAPER_ROW_INDEX_RE.match(cells[0]):
            continue
        date, media_type, media_name = cells[1], cells[2], cells[3]
        main_page, page_section, title, author = cells[4], cells[5], cells[6], cells[7]
        if not _NEWSPAPER_DATE_RE.match(date):
            logger.warning(f"報紙報告資料列日期格式不符（{date!r}），略過：{title[:30]!r}")
            continue
        if not title.strip():
            continue
        # 標題儲存格內的超連結指向 XKM 系統的剪報全文頁（免登入可讀），
        # 當作這則新聞的 url——後續正文抓取直接從該頁取得剪報全文
        url = ""
        tds = tr.find_all(["td", "th"])
        if len(tds) >= 7:
            link = tds[6].find("a")
            if link is not None:
                url = (link.get("href") or "").strip()
        articles.append({
            "published_at": date, "source": media_name.strip(),
            "channel": f"{media_type.strip()} {main_page.strip()} {page_section.strip()}".strip(),
            "author": author.strip(), "title": normalize_whitespace(title),
            "section": section, "url": url,
        })
    return articles


def _newspaper_articles_to_items(articles: List[Dict[str, Any]],
                                  import_batch_id: str) -> List[NewsItem]:
    items: List[NewsItem] = []
    for art in articles:
        url = art.get("url", "")
        items.append(NewsItem(
            row_id=new_id("row_"),
            import_batch_id=import_batch_id,
            source_sheet="Gmail報紙",
            title=art["title"],
            summary="",
            source=art["source"],
            published_at=art["published_at"],
            author=art["author"],
            url=url,                      # XKM 剪報全文頁（標題儲存格的超連結）
            channel=art["channel"],       # 例：報紙 A01 要聞（版位是留用判斷的重要訊號）
            tags=art["section"],          # 內政部／三大報頭版／社論投書
            excel_body="",
            body_text="",
            body_source=NEWSPAPER_BODY_SOURCE,
            # 有連結：走正文抓取（XKM 頁的 div.dataView 有剪報全文，見
            # ScrapingSettings.site_selectors 預設）；沒連結：標「略過」不進抓取，
            # 留用與分群退回以標題參與判斷
            body_fetch_status="未抓取" if url else "略過",
            body_fetch_detail="" if url else "報紙監測報告此則無全文連結，留用與分群以標題參與判斷",
            body_word_count=0,
            retained=True,
            retention_status="待確認",
        ))
    return items


def repair_newspaper_rows(news_repo, items: List[NewsItem]):
    """重新匯入同一封報紙監測報告時的修補與去重：

    V4.4.1 之前匯入的報紙新聞沒有全文連結（url 為空、抓取狀態「略過」），
    正文抓取會照設計跳過它們。使用者重新匯入同一封信時，不應插入 49 則重複列，
    而是把既有列補上 XKM 全文連結（留用判斷、議題歸屬等人工成果完全保留），
    讓它們能進入正文抓取。

    比對鍵：標題＋監測日期＋媒體名稱（報紙報告內此組合唯一）。
    回傳（仍需新增的項目清單, 修補連結筆數）。"""
    if not any(it.body_source == NEWSPAPER_BODY_SOURCE for it in items):
        return items, 0
    existing_index = {}
    for ex in news_repo.list_all():
        if ex.body_source == NEWSPAPER_BODY_SOURCE:
            existing_index.setdefault((ex.title, ex.published_at, ex.source), ex)

    to_insert: List[NewsItem] = []
    repaired = 0
    for it in items:
        if it.body_source != NEWSPAPER_BODY_SOURCE:
            to_insert.append(it)
            continue
        ex = existing_index.get((it.title, it.published_at, it.source))
        if ex is None:
            to_insert.append(it)
            continue
        # 同一則報紙新聞已存在：不重複插入；舊列缺連結而這次解析到了 → 補上
        if it.url and not ex.url:
            fields = {"url": it.url, "body_fetch_detail": "重新匯入補上全文連結"}
            if not ex.body_text:
                fields["body_fetch_status"] = "未抓取"   # 解除「略過」，讓抓取階段接手
            news_repo.update_fields(ex.row_id, fields)
            repaired += 1
    if repaired:
        logger.info(f"重新匯入報紙監測報告：修補 {repaired} 則既有新聞的全文連結（未新增重複列）")
    return to_insert, repaired


def parse_report_html(html: str, import_batch_id: str = "") -> List[NewsItem]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise RuntimeError("尚未安裝 beautifulsoup4，請執行 pip install -r requirements.txt") from e

    soup = BeautifulSoup(html, "html.parser")
    articles = _extract_articles(soup)

    if not articles:
        # 版型自動判別：不是網路版（沒有【日期 來源 - 版面 記者】中繼資料列）時，
        # 改試報紙版表格。兩種報告來自同一寄件者，匯入同一時段常會兩封都撈到。
        newspaper = _extract_newspaper_articles(soup)
        if newspaper:
            items = _newspaper_articles_to_items(newspaper, import_batch_id)
            with_url = sum(1 for it in items if it.url)
            logger.info(f"Gmail 報紙監測報告解析完成，共 {len(items)} 則"
                         f"（{with_url} 則有 XKM 全文連結，可抓取剪報正文）")
            return items
        logger.warning("Gmail 報告內文找不到任何符合格式的「【日期 來源 - 版面 記者】」中繼資料列，"
                        "也不是報紙監測報告的表格版型，需對照真實 HTML 調整解析規則")
        return []

    items: List[NewsItem] = []
    for idx, art in enumerate(articles):
        if not art["title"]:
            logger.warning(f"第 {idx + 1} 則找不到標題（緊鄰中繼資料列前一行為空或為雜訊行），略過")
            continue
        if not art["url"]:
            logger.warning(f"第 {idx + 1} 則（{art['title']}）找不到對應的 Source 連結，url 留空")

        body = normalize_whitespace("\n".join(art["body_lines"]))
        word_count = word_count_cjk_aware(body)
        suspicious = word_count < 80 or not title_body_overlap(art["title"], body)

        items.append(NewsItem(
            row_id=new_id("row_"),
            import_batch_id=import_batch_id,
            source_sheet="Gmail",
            title=art["title"],
            summary="",
            source=art["source"].strip(),
            published_at=art["published_at"],
            author=art["author"].strip(),
            url=art["url"],
            channel=art["channel"].strip(),
            tags="",
            excel_body=body,
            body_text=body,
            body_source="Gmail正文",
            body_fetch_status="可疑" if suspicious else "成功",
            body_word_count=word_count,
            retained=True,
            retention_status="待確認",
        ))

    logger.info(f"Gmail 報告解析完成，共 {len(items)} 則（原始中繼資料列 {len(articles)} 個）")
    return items
