"""
API Key 安全儲存

規格要求（四、模型與 API 設定）：
    - 不寫入程式碼
    - 不寫入 feedback log、案例庫或 Word
    - 不以明碼存於一般 JSON
    - 在 Windows 使用 DPAPI 或 Windows Credential Manager 加密保存
    - 介面僅顯示遮罩與末四碼
    - 可一鍵清除

實作方式：
    使用 `keyring` 套件。在 Windows 上，keyring 預設 backend 即為
    Windows Credential Manager（透過 win32ctypes 呼叫 Credential API，
    底層由作業系統以 DPAPI 加密），符合規格「使用 DPAPI 或 Windows
    Credential Manager」的要求。

    在非 Windows 開發／測試環境（本沙盒即為 Linux），keyring 會嘗試使用
    可用的 backend（如 SecretService / KWallet）；若完全沒有可用 backend，
    會拋出例外，此時我們退回一個「僅供本機開發測試」的檔案型 fallback，
    並在 UI 上明確警告這不具備 Windows 正式部署的加密強度。正式上線
    （Windows）一律使用 keyring 的 Credential Manager backend。
"""
from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Optional

from .paths import get_app_data_dir
from .logging_setup import get_logger

logger = get_logger("secure_key_store")

SERVICE_NAME = "NewsSentimentDesktopV4"
ACCOUNT_NAME = "ANTHROPIC_API_KEY"
ACCOUNT_NAME_GMAIL = "GMAIL_OAUTH_CREDENTIALS"


class SecureKeyStoreError(Exception):
    pass


def _dev_fallback_path() -> Path:
    return get_app_data_dir() / ".dev_fallback_credential.json"


def _try_keyring():
    try:
        import keyring  # type: ignore
        return keyring
    except Exception as e:  # pragma: no cover
        logger.warning(f"keyring 套件不可用: {e}")
        return None


def save_api_key(api_key: str) -> None:
    if not api_key:
        raise SecureKeyStoreError("API Key 不可為空")
    kr = _try_keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE_NAME, ACCOUNT_NAME, api_key)
            logger.info("API Key 已透過 keyring (Windows Credential Manager / DPAPI) 儲存")
            return
        except Exception as e:
            logger.warning(f"keyring 儲存失敗，改用開發用 fallback: {e}")
    # 非 Windows 開發環境 fallback（明確標示非正式加密強度，僅供本機測試）
    if sys.platform == "win32":
        raise SecureKeyStoreError(f"無法透過 Windows Credential Manager 儲存 API Key")
    _dev_fallback_path().write_text(json.dumps({"api_key": api_key}), encoding="utf-8")
    logger.warning("已使用開發用明碼 fallback 儲存 API Key（僅限本沙盒測試，正式版必須在 Windows 上執行）")


def load_api_key() -> Optional[str]:
    kr = _try_keyring()
    if kr is not None:
        try:
            val = kr.get_password(SERVICE_NAME, ACCOUNT_NAME)
            if val:
                return val
        except Exception as e:
            logger.warning(f"keyring 讀取失敗: {e}")
    if sys.platform != "win32":
        p = _dev_fallback_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("api_key")
            except Exception:
                return None
    return None


def clear_api_key() -> None:
    kr = _try_keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception as e:
            logger.warning(f"keyring 清除失敗（可能本來就不存在）: {e}")
    p = _dev_fallback_path()
    if p.exists():
        p.unlink()
    logger.info("API Key 已清除")


def mask_api_key(api_key: Optional[str]) -> str:
    """介面僅顯示遮罩與末四碼"""
    if not api_key:
        return "（未設定）"
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return "*" * (len(api_key) - 4) + api_key[-4:]


def _dev_fallback_path_gmail() -> Path:
    return get_app_data_dir() / ".dev_fallback_gmail_credential.json"


def save_gmail_credentials(creds_json: str) -> None:
    """存 google.oauth2.credentials.Credentials.to_json() 的完整字串（含 refresh_token）"""
    if not creds_json:
        raise SecureKeyStoreError("Gmail 憑證內容不可為空")
    kr = _try_keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE_NAME, ACCOUNT_NAME_GMAIL, creds_json)
            logger.info("Gmail 憑證已透過 keyring (Windows Credential Manager / DPAPI) 儲存")
            return
        except Exception as e:
            logger.warning(f"keyring 儲存失敗，改用開發用 fallback: {e}")
    if sys.platform == "win32":
        raise SecureKeyStoreError("無法透過 Windows Credential Manager 儲存 Gmail 憑證")
    _dev_fallback_path_gmail().write_text(creds_json, encoding="utf-8")
    logger.warning("已使用開發用明碼 fallback 儲存 Gmail 憑證（僅限本沙盒測試，正式版必須在 Windows 上執行）")


def load_gmail_credentials() -> Optional[str]:
    kr = _try_keyring()
    if kr is not None:
        try:
            val = kr.get_password(SERVICE_NAME, ACCOUNT_NAME_GMAIL)
            if val:
                return val
        except Exception as e:
            logger.warning(f"keyring 讀取失敗: {e}")
    if sys.platform != "win32":
        p = _dev_fallback_path_gmail()
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def clear_gmail_credentials() -> None:
    kr = _try_keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE_NAME, ACCOUNT_NAME_GMAIL)
        except Exception as e:
            logger.warning(f"keyring 清除失敗（可能本來就不存在）: {e}")
    p = _dev_fallback_path_gmail()
    if p.exists():
        p.unlink()
    logger.info("Gmail 憑證已清除")
