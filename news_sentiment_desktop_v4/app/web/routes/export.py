"""匯出頁 — 直接重用桌面版既有的 word_exporter.export_simple_topic_list()：
「議題標題 + 底下新聞標題＋連結」的簡易清單匯出，不含摘要/立場欄位，正好對應
本次網頁版「輸出新聞議題清單」的範圍，不需要修改 word_exporter.py。
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, render_template, send_file

from app.web.server import get_context
from app.exporters.word_exporter import export_simple_topic_list
from app.utils.paths import get_exports_dir

export_bp = Blueprint("export", __name__)


@export_bp.route("/export")
def index():
    ctx = get_context()
    topics = ctx.topic_repo.list_active()
    news_by_topic = {t.topic_id: ctx.news_repo.list_by_topic(t.topic_id) for t in topics}
    topic_count = sum(1 for t in topics if news_by_topic.get(t.topic_id))
    news_count = sum(len(v) for v in news_by_topic.values())
    return render_template("export.html", topic_count=topic_count, news_count=news_count)


@export_bp.route("/export/download")
def download():
    ctx = get_context()
    topics = ctx.topic_repo.list_active()
    news_by_topic = {t.topic_id: ctx.news_repo.list_by_topic(t.topic_id) for t in topics}

    filename = f"新聞議題清單_{datetime.now().strftime('%Y%m%d_%H%M')}.docx"
    output_path = get_exports_dir() / filename
    export_simple_topic_list(str(output_path), topics, news_by_topic, ctx.settings.word_export)

    return send_file(str(output_path), as_attachment=True, download_name=filename)
