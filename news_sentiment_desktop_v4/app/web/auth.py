"""共用密碼閘門 — 對應「不同人直接到網站就開始操作，但用共用密碼擋掉不相關人士」需求。

沒有個別帳號系統：全站只有一組密碼（WEB_SHARED_PASSWORD 環境變數），登入後
在 Flask session（signed cookie，非明碼）內設定 authenticated 旗標。
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.utils.logging_setup import get_logger

logger = get_logger("web_auth")

login_bp = Blueprint("login", __name__)

_SESSION_KEY = "authenticated"
_EXEMPT_ENDPOINTS = {"login.login", "static"}

# 簡易登入節流：共用密碼沒有帳號鎖定機制天生就怕暴力嘗試，記錄每個來源 IP
# 短時間內的失敗次數，超過門檻就先擋一段時間。只需要在單一 gunicorn worker
# 程序內生效（render.yaml 本來就是 -w 1），程序重啟會重置，足以擋掉自動化暴力
# 嘗試，不追求跨程序/跨重啟的持久化。
_FAILED_ATTEMPT_LIMIT = 8
_LOCKOUT_SECONDS = 60
_lock = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}


def _client_key() -> str:
    return request.remote_addr or "unknown"


def _is_locked_out(key: str) -> bool:
    with _lock:
        attempts = _failed_attempts.get(key, [])
        cutoff = time.time() - _LOCKOUT_SECONDS
        attempts = [t for t in attempts if t > cutoff]
        _failed_attempts[key] = attempts
        return len(attempts) >= _FAILED_ATTEMPT_LIMIT


def _record_failed_attempt(key: str) -> None:
    with _lock:
        _failed_attempts.setdefault(key, []).append(time.time())


def _record_success(key: str) -> None:
    with _lock:
        _failed_attempts.pop(key, None)


def _safe_next_path(value: str) -> str:
    """只接受同站相對路徑，避免 next 參數被用來做開放重導向（open redirect）。"""
    if not value or not value.startswith("/") or value.startswith("//"):
        return url_for("dashboard.index")
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return url_for("dashboard.index")
    return value


def is_authenticated() -> bool:
    return bool(session.get(_SESSION_KEY))


def register_auth_gate(app) -> None:
    @app.before_request
    def _require_login():
        if request.endpoint in _EXEMPT_ENDPOINTS or request.endpoint is None:
            return None
        if not is_authenticated():
            return redirect(url_for("login.login", next=request.path))
        return None


@login_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        client_key = _client_key()
        if _is_locked_out(client_key):
            error = "嘗試次數過多，請稍後再試"
            logger.warning(f"登入節流：{client_key} 短時間內失敗次數過多，暫時拒絕")
            return render_template("login.html", error=error)

        shared_password = os.environ.get("WEB_SHARED_PASSWORD", "")
        submitted = request.form.get("password", "")
        # 用常數時間比較，避免密碼長度／內容差異透過回應時間被推測
        if shared_password and hmac.compare_digest(submitted, shared_password):
            _record_success(client_key)
            session[_SESSION_KEY] = True
            next_path = _safe_next_path(request.args.get("next", ""))
            return redirect(next_path)
        _record_failed_attempt(client_key)
        error = "密碼錯誤，請再試一次"
        if not shared_password:
            error = "伺服器尚未設定 WEB_SHARED_PASSWORD 環境變數，無法登入"
    return render_template("login.html", error=error)


@login_bp.route("/logout")
def logout():
    session.pop(_SESSION_KEY, None)
    return redirect(url_for("login.login"))
