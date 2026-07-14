"""測試：分群 worker 的失敗可見性（V4.5.4）

使用者回報「AI 議題分群分不出議題」——舊行為在三種情況都只顯示
「分群完成，共產生 0 個議題」：(1) 沒有可分群的新聞、(2) 正文全數不足、
(3) 全部分桶的 AI 呼叫失敗（只寫 log）。現在都改成明確訊息。

worker 以同步方式呼叫 run()（不 start() QThread），搭配假 gateway 驗證。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.models.news import NewsItem
from app.services.ai.model_gateway import GatewayError, GatewayErrorType


def _make_worker(qapp, tmp_db_path, gateway, **kwargs):
    from app.workers.clustering_worker import ClusteringWorker
    from app.repositories.news_repository import NewsRepository
    from app.repositories.topic_repository import TopicRepository
    from app.repositories.settings_repository import PromptRepository
    from app.prompts.registry import seed_defaults
    prompt_repo = PromptRepository(tmp_db_path)
    seed_defaults(prompt_repo)
    worker = ClusteringWorker(
        gateway, NewsRepository(tmp_db_path), TopicRepository(tmp_db_path),
        prompt_repo, db_path=tmp_db_path, **kwargs)
    errors, oks = [], []
    worker.finished_error.connect(errors.append)
    worker.finished_ok.connect(oks.append)
    return worker, errors, oks


class _FailingGateway:
    def call_with_tool(self, *a, **kw):
        raise GatewayError(GatewayErrorType.OVERLOADED, "模擬 API 過載")


def test_no_clusterable_news_reports_reason(qapp, tmp_db_path):
    worker, errors, oks = _make_worker(qapp, tmp_db_path, _FailingGateway())
    worker.run()
    assert not oks
    assert errors and "已留用且有正文" in errors[0]


def test_all_insufficient_body_reports_reason(qapp, tmp_db_path, news_repo):
    news_repo.upsert_one(NewsItem(row_id="r1", title="正文太短的新聞", source="來源",
                                    published_at="2026-07-14", retained=True,
                                    body_text="太短", body_word_count=2,
                                    body_fetch_status="成功"))
    worker, errors, oks = _make_worker(qapp, tmp_db_path, _FailingGateway())
    worker.run()
    assert not oks
    assert errors and "正文不足" in errors[0]


def test_all_buckets_failing_reports_last_error(qapp, tmp_db_path, news_repo):
    for i in range(3):
        news_repo.upsert_one(NewsItem(
            row_id=f"r{i}", title=f"新聞{i}", source="來源", published_at="2026-07-14",
            retained=True, body_text="足夠長的正文內容。" * 20, body_word_count=200,
            body_fetch_status="成功"))
    worker, errors, oks = _make_worker(qapp, tmp_db_path, _FailingGateway())
    worker.run()
    assert not oks
    assert errors
    assert "分桶的 AI 呼叫都失敗" in errors[0]
    assert "模擬 API 過載" in errors[0]
    assert worker.failed_buckets == 1   # 3 則新聞在預設桶大小下為 1 桶
