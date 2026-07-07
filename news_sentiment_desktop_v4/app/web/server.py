"""
Flask 應用程式工廠 — 網頁版

與桌面版共用同一個 AppContext（composition root），只是外層 UI 從 PySide6
換成 Flask。所有 AI 呼叫一樣經由 AppContext.gateway（ModelGateway），
資料一樣存在同一顆 SQLite DB（app/utils/paths.py，雲端部署時由
NEWS_SENTIMENT_DATA_DIR 環境變數指向持久磁碟）。
"""
from __future__ import annotations

import os
import secrets

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.controllers.app_context import AppContext
from app.web.auth import login_bp, register_auth_gate
from app.utils.logging_setup import get_logger

logger = get_logger("web_server")


def create_app() -> Flask:
    app = Flask(__name__)
    secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        # 沒設這個環境變數就不能啟動一個「大家都知道」的固定密鑰——那等於讓任何人
        # 都能偽造已登入的 session cookie、直接繞過共用密碼。改成每次啟動隨機產生
        # 一把（僅在這次程序生命週期內有效，重啟後既有 session 會全部失效，本機
        # 測試/開發沿用這個行為即可），並大聲記警告，避免正式部署忘記設定卻不自知。
        secret_key = secrets.token_hex(32)
        logger.warning(
            "未設定 FLASK_SECRET_KEY 環境變數，已產生僅本次執行有效的隨機密鑰。"
            "正式部署請務必設定固定的 FLASK_SECRET_KEY，否則每次重啟都會讓所有人被登出。"
        )
    app.secret_key = secret_key
    # Render（與大多數 PaaS）在 TLS 終止代理後面以 http 轉發給本服務；沒有
    # ProxyFix，Flask 會誤判 request.scheme 為 http，導致 url_for(_external=True)
    # 產生的 Gmail OAuth redirect_uri 跟 Google Cloud Console 登記的 https
    # 網址對不起來。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config["APP_CONTEXT"] = AppContext(debug=os.environ.get("NSD_DEBUG") == "1")

    # 網頁版每次工作都在背景 threading.Thread 裡跑、不支援續跑（見 job_runner.py
    # 開頭說明）；雲端部署重新啟動一定會把還在跑的背景執行緒直接砍掉，資料庫裡
    # 對應的 job 卻永遠停在 running，UI 會一直顯示一個其實已經死掉的進度條。
    # 啟動時掃描一次，把這些殘留紀錄標記為 failed。桌面版不呼叫這個方法——
    # 桌面版的 running 工作本來就設計成可以續跑，不能在啟動時被誤判成失敗。
    from app.repositories.job_repository import JobRepository
    stale_count = JobRepository().mark_stale_running_jobs_as_failed()
    if stale_count:
        logger.warning(f"啟動時清理 {stale_count} 筆卡在 running 狀態的殘留工作紀錄（已標記為 failed）")

    from app.web.routes.dashboard import dashboard_bp
    from app.web.routes.settings import settings_bp
    from app.web.routes.import_gmail import import_bp
    from app.web.routes.scraping import scraping_bp
    from app.web.routes.retention import retention_bp
    from app.web.routes.clustering import clustering_bp
    from app.web.routes.export import export_bp
    from app.web.routes.jobs import jobs_bp
    from app.web.routes.pipeline import pipeline_bp

    app.register_blueprint(login_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(scraping_bp)
    app.register_blueprint(retention_bp)
    app.register_blueprint(clustering_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(pipeline_bp)

    register_auth_gate(app)

    return app


def get_context() -> AppContext:
    from flask import current_app
    return current_app.config["APP_CONTEXT"]
