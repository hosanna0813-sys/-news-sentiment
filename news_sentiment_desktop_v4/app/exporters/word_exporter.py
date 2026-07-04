"""
Word 早報匯出 — 對應規格書 十四

使用 python-docx。每個議題輸出：
    議題名稱 / 新聞數量與時間範圍 / 150字摘要 / 300字或完整摘要 /
    事件發展與關鍵進度 / 主要論述與立場（僅在有明確立場時顯示：支持/反對質疑/官方回應）/
    引用新聞清單（標題、來源、時間、網址）

可設定：Logo、頁首頁尾、日期格式、字型、字級、標題樣式、段落間距、
是否附新聞連結、是否附正文證據摘錄、是否輸出未取得正文新聞清單。
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import List, Dict

from app.models.topic import Topic, Stance
from app.models.news import NewsItem
from app.models.settings import WordExportSettings
from app.utils.logging_setup import get_logger

logger = get_logger("word_exporter")


def export_daily_report(
    output_path: str,
    topics: List[Topic],
    news_by_topic: Dict[str, List[NewsItem]],
    stances_by_topic: Dict[str, List[Stance]],
    missing_body_news: List[NewsItem],
    settings: WordExportSettings,
) -> str:
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # ---- 全域字型設定 ----
    style = doc.styles["Normal"]
    style.font.name = settings.font_name
    style.font.size = Pt(settings.font_size_pt)

    # ---- 頁首：Logo + 標題 + 日期 ----
    if settings.logo_path and Path(settings.logo_path).exists():
        try:
            doc.add_picture(settings.logo_path, width=Cm(3))
        except Exception as e:
            logger.warning(f"Logo 插入失敗: {e}")

    title_text = settings.header_text or "新聞輿情早報"
    date_str = datetime.now().strftime(settings.date_format)
    h = doc.add_heading(f"{title_text}（{date_str}）", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"本期共納入 {len(topics)} 個議題，涵蓋新聞 "
                       f"{sum(len(v) for v in news_by_topic.values())} 則。")

    # ---- 逐議題輸出 ----
    for idx, topic in enumerate(topics, start=1):
        items = news_by_topic.get(topic.topic_id, [])
        stances = stances_by_topic.get(topic.topic_id, [])

        doc.add_heading(f"{idx}. {topic.topic_name}", level=1)

        time_range = _compute_time_range(items)
        meta = doc.add_paragraph()
        meta.add_run(f"新聞數量：{len(items)} 則　時間範圍：{time_range}").italic = True

        _add_labeled_paragraph(doc, "150 字摘要", topic.summary_150, settings)
        _add_labeled_paragraph(doc, "300 字摘要", topic.summary_300 or topic.summary_full, settings)
        _add_labeled_paragraph(doc, "事件發展與關鍵進度", topic.development_progress, settings)
        _add_labeled_paragraph(doc, "核心爭點", topic.core_disputes, settings)

        # 主要論述與立場：僅在有明確立場時顯示（規格十二）
        if topic.has_identifiable_stance and stances:
            doc.add_heading("主要論述與立場", level=2)
            for label in ("支持", "反對／質疑", "官方回應"):
                group = [s for s in stances if s.stance_type == label]
                if not group:
                    continue
                doc.add_heading(label, level=3)
                for s in group:
                    p = doc.add_paragraph(style="List Bullet")
                    who = s.speaker + (f"（{s.organization}）" if s.organization else "")
                    p.add_run(f"{who}：").bold = True
                    p.add_run(s.claim)
                    if settings.include_body_excerpts and s.evidence_excerpt:
                        note = doc.add_paragraph()
                        note.add_run(f"　證據摘錄：{s.evidence_excerpt}").italic = True

        # 引用新聞清單
        doc.add_heading("引用新聞清單", level=2)
        for it in items:
            p = doc.add_paragraph(style="List Number")
            line = f"{it.title}（{it.source}，{it.published_at}）"
            p.add_run(line)
            if settings.include_news_links and it.url:
                p.add_run(f"\n{it.url}").italic = True

    # ---- 未取得正文新聞清單 ----
    if settings.include_missing_body_list and missing_body_news:
        doc.add_heading("附錄：未取得可用正文之新聞清單", level=1)
        for it in missing_body_news:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{it.title}（{it.source}，{it.published_at}） "
                       f"— 狀態：{it.body_fetch_status}；原因：{it.body_fetch_detail}")

    # ---- 頁尾 ----
    if settings.footer_text:
        section = doc.sections[0]
        footer = section.footer
        footer.paragraphs[0].text = settings.footer_text

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    logger.info(f"Word 早報已輸出: {out_path}")
    return str(out_path)


def _add_labeled_paragraph(doc, label: str, text: str, settings: WordExportSettings) -> None:
    if not text:
        return
    from docx.shared import Pt
    p = doc.add_paragraph()
    run_label = p.add_run(f"{label}：")
    run_label.bold = True
    p.add_run(text)
    p.paragraph_format.space_after = Pt(settings.paragraph_spacing_pt)


def _compute_time_range(items: List[NewsItem]) -> str:
    dates = [it.published_at for it in items if it.published_at]
    if not dates:
        return "無時間資訊"
    dates_sorted = sorted(dates)
    if dates_sorted[0] == dates_sorted[-1]:
        return dates_sorted[0]
    return f"{dates_sorted[0]} ～ {dates_sorted[-1]}"
