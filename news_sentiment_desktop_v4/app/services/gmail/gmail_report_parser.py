"""
競業信息知識管理系統（XKM）「新聞專屬監測報告」信件內文解析

觀察到的每則新聞結構（詳情區塊，非目錄表格）：
    {標題}
    【{YYYY-MM-DD} {媒體來源} - {版面} {記者}】
    {正文...}
    [可能夾雜 Prismintelligence / NewsScope AI 推薦問答區塊 —— 廠商加值功能，非新聞內容]
    Source   <- 超連結，指向原始新聞網址
    【Back】  <- 回目錄錨點，標記本則新聞結束

解析策略：**依真實 HTML 文件順序（`soup.descendants`）單一遍歷，維護一個「目前是否
有新聞正在處理中」的狀態機**，而非分開抓文字結構跟全域 Source 連結清單後再用位置
索引 zip 對應——後者一旦文件中有任何一則新聞缺少 Source 連結，後面所有新聞的連結
就會系統性錯位一格（已用真實資料驗證到這個問題）。狀態機作法讓每則新聞的 URL 只認
自己這一段範圍內第一個命中的 Source 連結，缺連結時只有那一則留空，不會波及其他則。

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


def parse_report_html(html: str, import_batch_id: str = "") -> List[NewsItem]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise RuntimeError("尚未安裝 beautifulsoup4，請執行 pip install -r requirements.txt") from e

    soup = BeautifulSoup(html, "html.parser")
    articles = _extract_articles(soup)

    if not articles:
        logger.warning("Gmail 報告內文找不到任何符合格式的「【日期 來源 - 版面 記者】」中繼資料列，"
                        "可能是版型與預期不符，需對照真實 HTML 調整解析規則")
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
