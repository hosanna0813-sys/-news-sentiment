"""測試：API Key 讀取優先序（V4.5.5）

使用者實際踩到的陷阱：電腦上殘留舊的 OPENAI_API_KEY 環境變數（基準測試時
設定的），設定頁存的新 key 被舊環境變數蓋住且毫無提示。改為 keyring
（設定頁儲存）優先、環境變數次之；雲端容器沒有 keyring backend，仍會
自然落到環境變數，網頁版部署行為不變。
"""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def fake_keyring(monkeypatch):
    """注入假 keyring 模組，模擬桌面版有可用的 Credential Manager"""
    store = {}
    fake = types.ModuleType("keyring")
    fake.set_password = lambda svc, acct, val: store.__setitem__((svc, acct), val)
    fake.get_password = lambda svc, acct: store.get((svc, acct))
    fake.delete_password = lambda svc, acct: store.pop((svc, acct), None)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return store


def test_settings_saved_key_beats_stale_env_var(fake_keyring, monkeypatch):
    from app.utils import secure_key_store as sks
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old-stale-env-key")
    fake_keyring[(sks.SERVICE_NAME, sks.ACCOUNT_NAME_OPENAI)] = "sk-new-from-settings"
    assert sks.load_openai_api_key() == "sk-new-from-settings"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-old-env")
    fake_keyring[(sks.SERVICE_NAME, sks.ACCOUNT_NAME)] = "sk-ant-new"
    assert sks.load_api_key() == "sk-ant-new"


def test_env_var_still_used_when_keyring_empty(fake_keyring, monkeypatch):
    """雲端部署情境：keyring 沒有存 key（容器沒 backend／沒存過）→ 落到環境變數"""
    from app.utils import secure_key_store as sks
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-render-env")
    fake_keyring.clear()
    assert sks.load_openai_api_key() == "sk-from-render-env"
