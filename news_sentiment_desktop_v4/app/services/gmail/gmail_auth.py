"""
Gmail OAuth 授權管理

只申請唯讀 scope（gmail.readonly），本模組刻意不在頂層 import google 相關套件，
理由與 model_gateway.py 對 anthropic 的作法相同：未安裝套件不影響其他功能。

憑證（含 refresh_token）以 google.oauth2.credentials.Credentials.to_json() 的完整
字串存於 keyring（見 secure_key_store.py），不落地寫入一般檔案。
"""
from __future__ import annotations

import json
from typing import Optional

from app.utils.secure_key_store import (
    save_gmail_credentials, load_gmail_credentials, clear_gmail_credentials,
)
from app.utils.logging_setup import get_logger

logger = get_logger("gmail_auth")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAuthError(Exception):
    pass


def run_oauth_flow(client_id: str, client_secret: str):
    """開啟系統瀏覽器進行 OAuth 同意流程（會阻塞直到使用者完成授權或逾時），
    呼叫端（GmailAuthWorker）須丟到背景執行緒執行，避免卡住 UI。"""
    if not client_id or not client_secret:
        raise GmailAuthError("請先輸入 Client ID 與 Client Secret")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise GmailAuthError("尚未安裝 google-auth-oauthlib，請執行 pip install -r requirements.txt") from e

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    try:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        raise GmailAuthError(f"OAuth 授權失敗：{e}") from e

    save_gmail_credentials(creds.to_json())
    logger.info("Gmail OAuth 授權完成並已儲存憑證")
    return creds


def build_web_flow(client_id: str, client_secret: str, redirect_uri: str):
    """建立網頁版（雲端部署）用的 OAuth Flow——桌面版用 InstalledAppFlow +
    run_local_server() 在無頭雲端容器上無法執行（容器裡沒有瀏覽器，且
    localhost callback 對外部使用者的瀏覽器沒有意義），改用標準的
    「Web application」授權碼流程：導向 Google 同意畫面 → 導回本服務的
    固定 redirect_uri → 用 authorization code 換 token。
    對應的 OAuth Client 需在 Google Cloud Console 建立為 Web application
    類型（非桌面版用的 Desktop app），Authorized redirect URI 需與
    redirect_uri 完全一致。"""
    if not client_id or not client_secret:
        raise GmailAuthError("尚未設定 Gmail OAuth Client ID / Client Secret")
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as e:
        raise GmailAuthError("尚未安裝 google-auth-oauthlib，請執行 pip install -r requirements.txt") from e

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, SCOPES, redirect_uri=redirect_uri)
    return flow


def complete_web_flow(flow, authorization_response: str) -> None:
    """在 OAuth callback 路由呼叫：以完整的 callback URL（含 code/state 參數）
    換取 token 並儲存憑證。

    儲存這步（save_gmail_credentials）以前漏包在 try/except 內，一旦儲存失敗
    （例如 keyring 無可用 backend 又寫檔失敗）會以未預期例外的形式往上拋，
    造成呼叫端的 GmailAuthError 攔截完全沒接到、畫面上什麼提示都不顯示，
    只留一個「尚未連接」的假象。現在整段（換 token + 存憑證 + 立即讀回驗證）
    都包在同一個 try/except 內，任何失敗都會轉成有意義的 GmailAuthError
    訊息，並完整記錄到 log 供排查。"""
    try:
        flow.fetch_token(authorization_response=authorization_response)
        logger.info(f"Gmail OAuth 換取 token 成功（scopes={flow.credentials.scopes}）")
        save_gmail_credentials(flow.credentials.to_json())
    except Exception as e:
        logger.exception("Gmail OAuth web flow 失敗（fetch_token 或儲存憑證階段）")
        raise GmailAuthError(f"OAuth 授權失敗：{e}") from e

    # 立即讀回驗證：儲存「看似」成功，但如果讀回失敗（例如序列化格式問題），
    # 使用者應該馬上知道，而不是等下次匯入才發現「尚未連接」。
    if get_valid_credentials() is None:
        logger.error("Gmail 憑證已寫入，但立即讀回驗證失敗（get_valid_credentials 回傳 None）")
        raise GmailAuthError("憑證已寫入但讀回驗證失敗，請查看伺服器 log 並重試一次")

    logger.info("Gmail OAuth 授權完成並已儲存憑證（web flow）")


def get_valid_credentials():
    """從 keyring 讀回憑證；過期則自動 refresh 並回寫。讀不到或 refresh 失敗回傳 None。"""
    creds_json = load_gmail_credentials()
    if not creds_json:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        logger.warning("尚未安裝 google-auth，無法還原 Gmail 憑證")
        return None

    try:
        info = json.loads(creds_json)
        creds = Credentials.from_authorized_user_info(info, SCOPES)
    except Exception as e:
        logger.warning(f"Gmail 憑證內容無法解析: {e}")
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_gmail_credentials(creds.to_json())
        except Exception as e:
            logger.warning(f"Gmail 憑證 refresh 失敗，需重新授權: {e}")
            clear_gmail_credentials()
            return None

    return creds if creds and creds.valid else None
