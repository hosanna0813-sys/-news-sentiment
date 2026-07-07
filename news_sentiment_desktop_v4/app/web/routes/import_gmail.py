from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.web.server import get_context
from app.services.gmail.gmail_importer import import_from_gmail, GmailImportError

import_bp = Blueprint("import_gmail", __name__)

# 桌面版在使用者自己的電腦上跑，naive datetime 直接當作系統當地時間，剛好就是
# 台灣時間；網頁版的伺服器（Render）跟使用者不在同一個時區，同一個
# datetime-local 字串在伺服器上如果不指定時區直接轉成 timestamp，會被當成
# 伺服器自己的當地時區（通常是 UTC）解讀，跟使用者實際想要的台灣時間差 8 小時，
# 導致 Gmail 搜尋區間整個偏移、篩不到信件。內政部同仁一律在台灣操作，直接
# 明確標記為台灣時區，不依賴伺服器本身的系統時區設定是否剛好也設成台灣時間。
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def parse_taipei_datetime(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value).replace(tzinfo=TAIPEI_TZ)


@import_bp.route("/import", methods=["GET", "POST"])
def index():
    ctx = get_context()
    result_summary = None

    if request.method == "POST":
        try:
            start_dt = parse_taipei_datetime(request.form["start_dt"])
            end_dt = parse_taipei_datetime(request.form["end_dt"])
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
