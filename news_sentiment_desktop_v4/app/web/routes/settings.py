"""設定頁：Gmail 寄件者/主旨關鍵字 + Gmail OAuth 連接狀態。

Anthropic API Key 與 Gmail OAuth Client ID/Secret 在雲端部署時一律由環境變數
注入（見 README「網頁版部署到 Render」一節），不提供網頁表單輸入明碼欄位。
"""
from __future__ import annotations

import os

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.web.server import get_context
from app.services.gmail.gmail_auth import (
    build_web_flow, complete_web_flow, get_valid_credentials, GmailAuthError,
)
from app.utils.secure_key_store import mask_api_key
from app.utils.logging_setup import get_logger

logger = get_logger("web_settings")

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
def index():
    ctx = get_context()
    if request.method == "POST":
        ctx.settings.gmail.sender_email_filter = request.form.get("sender_email_filter", "").strip()
        ctx.settings.gmail.subject_keyword = request.form.get("subject_keyword", "").strip()
        ctx.save_settings()
        flash("Gmail 設定已儲存", "success")
        return redirect(url_for("settings.index"))

    api_key_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    oauth_client_configured = bool(os.environ.get("GMAIL_OAUTH_CLIENT_ID") and
                                    os.environ.get("GMAIL_OAUTH_CLIENT_SECRET"))
    gmail_connected = get_valid_credentials() is not None
    # 顯示程式實際會送給 Google 的 redirect_uri，讓使用者逐字複製到 Google
    # Cloud Console 的 Authorized redirect URIs——手動猜測網址是
    # redirect_uri_mismatch（400）最常見的原因。
    oauth_redirect_uri = url_for("settings.gmail_oauth_callback", _external=True)

    return render_template(
        "settings.html",
        gmail=ctx.settings.gmail,
        api_key_configured=api_key_configured,
        api_key_masked=mask_api_key(os.environ.get("ANTHROPIC_API_KEY")),
        oauth_client_configured=oauth_client_configured,
        gmail_connected=gmail_connected,
        oauth_redirect_uri=oauth_redirect_uri,
    )


@settings_bp.route("/gmail/oauth/start")
def gmail_oauth_start():
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    redirect_uri = url_for("settings.gmail_oauth_callback", _external=True)
    try:
        flow = build_web_flow(client_id, client_secret, redirect_uri)
    except GmailAuthError as e:
        flash(str(e), "error")
        return redirect(url_for("settings.index"))
    auth_url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    return redirect(auth_url)


@settings_bp.route("/gmail/oauth/callback")
def gmail_oauth_callback():
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    redirect_uri = url_for("settings.gmail_oauth_callback", _external=True)
    try:
        flow = build_web_flow(client_id, client_secret, redirect_uri)
        complete_web_flow(flow, request.url)
        flash("Gmail 已成功連接", "success")
    except GmailAuthError as e:
        flash(f"Gmail 連接失敗：{e}", "error")
    except Exception as e:
        # 任何非預期的例外（不只是 GmailAuthError）都要轉成看得到的錯誤訊息並
        # 完整記錄，不能讓使用者停在「畫面正常跳轉、但什麼提示都沒有、連接狀態
        # 卻仍是尚未連接」的無聲失敗狀態。
        logger.exception("Gmail OAuth callback 發生未預期例外")
        flash(f"Gmail 連接發生未預期錯誤：{e}", "error")
    return redirect(url_for("settings.index"))
