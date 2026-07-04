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
