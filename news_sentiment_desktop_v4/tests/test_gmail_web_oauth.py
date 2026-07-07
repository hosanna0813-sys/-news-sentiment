"""complete_web_flow() 的單元測試——涵蓋這次修的 bug：儲存憑證失敗、或存了但
讀不回來時，必須明確拋出 GmailAuthError（讓呼叫端能 flash 出訊息），
不能被靜默吞掉、留下「畫面正常跳轉卻什麼提示都沒有、連接狀態仍顯示尚未連接」
的無聲失敗狀態。
"""
from __future__ import annotations

import pytest

from app.services.gmail import gmail_auth
from app.services.gmail.gmail_auth import complete_web_flow, GmailAuthError


class _FakeCredentials:
    def __init__(self):
        self.scopes = gmail_auth.SCOPES

    def to_json(self):
        return '{"fake": "creds"}'


class _FakeFlow:
    def __init__(self, fetch_token_error=None):
        self._fetch_token_error = fetch_token_error
        self.credentials = _FakeCredentials()
        self.fetch_token_called_with = None

    def fetch_token(self, authorization_response):
        self.fetch_token_called_with = authorization_response
        if self._fetch_token_error:
            raise self._fetch_token_error


def test_complete_web_flow_success(monkeypatch):
    monkeypatch.setattr(gmail_auth, "save_gmail_credentials", lambda creds_json: None)
    monkeypatch.setattr(gmail_auth, "get_valid_credentials", lambda: object())

    flow = _FakeFlow()
    complete_web_flow(flow, "https://example.onrender.com/gmail/oauth/callback?code=abc&state=xyz")
    assert flow.fetch_token_called_with is not None


def test_complete_web_flow_raises_when_fetch_token_fails(monkeypatch):
    monkeypatch.setattr(gmail_auth, "save_gmail_credentials", lambda creds_json: None)

    flow = _FakeFlow(fetch_token_error=RuntimeError("invalid_grant"))
    with pytest.raises(GmailAuthError):
        complete_web_flow(flow, "https://example.onrender.com/gmail/oauth/callback?code=abc")


def test_complete_web_flow_raises_when_save_fails(monkeypatch):
    def _boom(creds_json):
        raise OSError("disk full")

    monkeypatch.setattr(gmail_auth, "save_gmail_credentials", _boom)

    flow = _FakeFlow()
    with pytest.raises(GmailAuthError):
        complete_web_flow(flow, "https://example.onrender.com/gmail/oauth/callback?code=abc")


def test_complete_web_flow_raises_when_readback_fails(monkeypatch):
    # 這是這次修的核心 bug 案例：儲存本身沒有拋例外（write 看似成功），但立即
    # 讀回卻拿不到有效憑證（例如序列化格式問題）——修之前這種情況會整個函式
    # 正常回傳，呼叫端誤以為連接成功，實際上 get_valid_credentials() 仍是 None。
    monkeypatch.setattr(gmail_auth, "save_gmail_credentials", lambda creds_json: None)
    monkeypatch.setattr(gmail_auth, "get_valid_credentials", lambda: None)

    flow = _FakeFlow()
    with pytest.raises(GmailAuthError, match="讀回驗證失敗"):
        complete_web_flow(flow, "https://example.onrender.com/gmail/oauth/callback?code=abc")
