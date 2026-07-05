"""
Flask 應用程式工廠 — 網頁版

與桌面版共用同一個 AppContext（composition root），只是外層 UI 從 PySide6
換成 Flask。所有 AI 呼叫一樣經由 AppContext.gateway（ModelGateway），
資料一樣存在同一顆 SQLite DB（app/utils/paths.py，雲端部署時由
NEWS_SENTIMENT_DATA_DIR 環境變數指向持久磁碟）。
"""
from __future__ import annotations

import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.controllers.app_context import AppContext
from app.web.auth import login_bp, register_auth_gate


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-secret-key")
    # Render（與大多數 PaaS）在 TLS 終止代理後面以 http 轉發給本服務；沒有
    # ProxyFix，Flask 會誤判 request.scheme 為 http，導致 url_for(_external=True)
    # 產生的 Gmail OAuth redirect_uri 跟 Google Cloud Console 登記的 https
    # 網址對不起來。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config["APP_CONTEXT"] = AppContext(debug=os.environ.get("NSD_DEBUG") == "1")

    from app.web.routes.dashboard import dashboard_bp
    from app.web.routes.settings import settings_bp
    from app.web.routes.import_gmail import import_bp
    from app.web.routes.scraping import scraping_bp
    from app.web.routes.retention import retention_bp
    from app.web.routes.clustering import clustering_bp
    from app.web.routes.export import export_bp
    from app.web.routes.jobs import jobs_bp

    app.register_blueprint(login_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(scraping_bp)
    app.register_blueprint(retention_bp)
    app.register_blueprint(clustering_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(jobs_bp)

    register_auth_gate(app)

    return app


def get_context() -> AppContext:
    from flask import current_app
    return current_app.config["APP_CONTEXT"]
