from __future__ import annotations

from flask import Blueprint, render_template

from app.web.server import get_context

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
