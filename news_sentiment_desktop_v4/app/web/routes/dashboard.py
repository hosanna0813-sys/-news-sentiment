from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for

from app.web.server import get_context
from app.web.job_runner import has_any_running_job

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    ctx = get_context()
    news = ctx.news_repo.list_all()
    total = len(news)
    retained = sum(1 for n in news if n.retained)
    with_body = sum(1 for n in news if n.has_body)
    clustered = sum(1 for n in news if n.final_topic_id)
    topics = ctx.topic_repo.list_active()

    return render_template(
        "dashboard.html",
        total=total, retained=retained, with_body=with_body,
        clustered=clustered, topic_count=len(topics),
    )


@dashboard_bp.route("/clear_data", methods=["POST"])
def clear_data():
    """清除本次匯入的新聞與議題，開始新的一輪彙整。

    刻意不清 feedback_log（回饋 log，供留用初判／議題分群的 few-shot 學習
    用）——這是使用者明確要求保留、之後要拿來訓練 AI 判斷的紀錄；
    NewsRepository.delete_all() 本身的既有設計就已經是「不影響回饋 log／
    案例庫／規則庫」，這裡直接沿用該行為，不重新發明一套。
    """
    ctx = get_context()
    if has_any_running_job():
        # 背景執行緒正在讀寫這些資料表，邊跑邊刪會讓它的後續寫入變成孤兒資料
        flash("目前有工作正在執行中，請等它跑完（或失敗）後再清除資料", "error")
        return redirect(url_for("dashboard.index"))
    news_count = ctx.news_repo.delete_all()  # 一併清 import_batches；不動 feedback_log
    ctx.topic_repo.delete_all()
    ctx.scrape_stats_repo.delete_all()
    ctx.job_repo.delete_all()
    ctx.batch_repo.delete_all()
    flash(f"已清除 {news_count} 則新聞與所有議題，可以開始新的一輪彙整"
          "（留用／分群的人工修正回饋紀錄不受影響，仍會用於之後的 AI 判斷參考）", "success")
    return redirect(url_for("dashboard.index"))
