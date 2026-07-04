"""共用 pytest fixtures：隔離的臨時 SQLite 資料庫、假 anthropic 模組"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def tmp_db_path(tmp_path):
    db_path = tmp_path / "test_nsd.db"
    from app.repositories.db import init_db
    init_db(db_path)
    return db_path


@pytest.fixture()
def news_repo(tmp_db_path):
    from app.repositories.news_repository import NewsRepository
    return NewsRepository(tmp_db_path)


@pytest.fixture()
def topic_repo(tmp_db_path):
    from app.repositories.topic_repository import TopicRepository
    return TopicRepository(tmp_db_path)


@pytest.fixture()
def job_repo(tmp_db_path):
    from app.repositories.job_repository import JobRepository
    return JobRepository(tmp_db_path)


@pytest.fixture()
def batch_repo(tmp_db_path):
    from app.repositories.job_repository import BatchRepository
    return BatchRepository(tmp_db_path)


@pytest.fixture()
def feedback_repo(tmp_db_path):
    from app.repositories.feedback_repository import FeedbackRepository
    return FeedbackRepository(tmp_db_path)


@pytest.fixture(scope="session")
def qapp():
    """提供 QApplication 實例；若未安裝 PySide6 則使用此 fixture 的測試會被自動略過"""
    pyside6 = pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def fake_anthropic_module(monkeypatch):
    """
    注入一個假的 anthropic 套件到 sys.modules，讓 ModelGateway 相關測試
    不需要真的安裝 anthropic SDK 或呼叫真實 API 即可驗證重試/錯誤分類/
    Tool Use 解析邏輯（規格十七要求的 'mock Anthropic API 回應'）。
    """
    fake = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg)
            self.status_code = status_code

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    fake.AuthenticationError = AuthenticationError
    fake.RateLimitError = RateLimitError
    fake.APIStatusError = APIStatusError
    fake.APITimeoutError = APITimeoutError
    fake.APIConnectionError = APIConnectionError

    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake
