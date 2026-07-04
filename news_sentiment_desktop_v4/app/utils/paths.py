"""
應用程式路徑管理

所有資料（SQLite DB、Prompt 設定、案例庫、規則庫、log）都存放在
使用者本機的 %APPDATA%/NewsSentimentDesktopV4 (Windows)，
非 Windows 開發環境則退回 ~/.news_sentiment_desktop_v4，方便在本沙盒測試。

嚴禁：API Key 不得存在這裡的任何明碼檔案中（見 secure_key_store.py）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "NewsSentimentDesktopV4"


def get_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        p = Path(base) / APP_DIR_NAME
    else:
        # 開發／測試用途的退回路徑（非 Windows）
        p = Path.home() / f".{APP_DIR_NAME.lower()}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db_path() -> Path:
    return get_app_data_dir() / "news_sentiment.db"


def get_logs_dir() -> Path:
    d = get_app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_exports_dir() -> Path:
    d = get_app_data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_prompts_backup_dir() -> Path:
    d = get_app_data_dir() / "prompt_versions"
    d.mkdir(parents=True, exist_ok=True)
    return d
