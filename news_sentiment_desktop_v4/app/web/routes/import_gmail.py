from __future__ import annotations

import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.web.server import get_context
from app.services.gmail.gmail_importer import import_from_gmail, GmailImportError

import_bp = Blueprint("import_gmail", __name__)


@import_bp.route("/import", methods=["GET", "POST"])
def index():
    ctx = get_context()
    result_summary = None

    if request.method == "POST":
        try:
            start_dt = datetime.datetime.fromisoformat(request.form["start_dt"])
            end_dt = datetime.datetime.fromisoformat(request.form["end_dt"])
        except (KeyError, ValueError):
            flash("請填寫正確的起訖時間", "error")
            return redirect(url_for("import_gmail.index"))

        try:
            result = import_from_gmail(ctx.settings.gmail, start_dt, end_dt)
        except GmailImportError as e:
            flash(str(e), "error")
            return redirect(url_for("import_gmail.index"))

        ctx.news_repo.upsert_many(result.items)
        result_summary = {
            "file_name": result.file_name,
            "total_rows": result.total_rows,
            "duplicate_rows": result.duplicate_rows,
        }
        flash(f"匯入完成，共 {result.total_rows} 筆新聞", "success")

    news_count = len(ctx.news_repo.list_all())
    return render_template("import.html", result_summary=result_summary, news_count=news_count,
                            gmail_configured=bool(ctx.settings.gmail.sender_email_filter))
