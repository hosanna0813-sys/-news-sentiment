"""共用密碼閘門 — 對應「不同人直接到網站就開始操作，但用共用密碼擋掉不相關人士」需求。

沒有個別帳號系統：全站只有一組密碼（WEB_SHARED_PASSWORD 環境變數），登入後
在 Flask session（signed cookie，非明碼）內設定 authenticated 旗標。
"""
from __future__ import annotations

import os
from flask import Blueprint, redirect, render_template, request, session, url_for

login_bp = Blueprint("login", __name__)

_SESSION_KEY = "authenticated"
_EXEMPT_ENDPOINTS = {"login.login", "static"}


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
        shared_password = os.environ.get("WEB_SHARED_PASSWORD", "")
        submitted = request.form.get("password", "")
        if shared_password and submitted == shared_password:
            session[_SESSION_KEY] = True
            next_path = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_path)
        error = "密碼錯誤，請再試一次"
        if not shared_password:
            error = "伺服器尚未設定 WEB_SHARED_PASSWORD 環境變數，無法登入"
    return render_template("login.html", error=error)


@login_bp.route("/logout")
def logout():
    session.pop(_SESSION_KEY, None)
    return redirect(url_for("login.login"))
