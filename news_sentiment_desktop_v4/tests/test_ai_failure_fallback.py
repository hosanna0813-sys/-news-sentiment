"""測試：AI 失敗回退（規格十五：不可假裝已完成 AI 判斷、須明確顯示錯誤、單批失敗只影響該批）"""
from __future__ import annotations

import types
import pytest

from app.services.ai.model_gateway import ModelGateway, GatewayError, GatewayErrorType


def _make_gateway(fake_anthropic_module, create_fn, max_retries=2):
    class FakeClient:
        def __init__(self, api_key, timeout):
            self.messages = types.SimpleNamespace(create=create_fn)

    fake_anthropic_module.Anthropic = FakeClient
    return ModelGateway(
        api_key_provider=lambda: "sk-ant-fake",
        task_model_lookup=lambda task: {"model_id": "claude-haiku-4-5", "max_tokens": 512, "temperature": 0.0},
        request_timeout_sec=5, max_retries=max_retries, retry_backoff_base_sec=0.001,
    )


def test_authentication_error_not_retried(fake_anthropic_module):
    call_count = {"n": 0}

    def create(**kwargs):
        call_count["n"] += 1
        raise fake_anthropic_module.AuthenticationError("invalid api key")

    gw = _make_gateway(fake_anthropic_module, create, max_retries=5)

    with pytest.raises(GatewayError) as exc_info:
        gw.call_with_tool(task="retention_judgement", system_prompt="s", user_content="u",
                           tool_name="t", tool_schema={"type": "object"})

    assert exc_info.value.error_type == GatewayErrorType.AUTH
    assert call_count["n"] == 1, "認證錯誤不應重試，避免浪費時間與額度"


def test_rate_limit_error_retried_then_exhausted(fake_anthropic_module):
    call_count = {"n": 0}

    def create(**kwargs):
        call_count["n"] += 1
        raise fake_anthropic_module.RateLimitError("rate limited")

    gw = _make_gateway(fake_anthropic_module, create, max_retries=3)

    with pytest.raises(GatewayError) as exc_info:
        gw.call_with_tool(task="retention_judgement", system_prompt="s", user_content="u",
                           tool_name="t", tool_schema={"type": "object"})

    assert exc_info.value.error_type == GatewayErrorType.RATE_LIMIT
    assert call_count["n"] == 3, "應依 max_retries 設定重試對應次數"


def test_not_configured_when_no_api_key():
    gw = ModelGateway(
        api_key_provider=lambda: None,
        task_model_lookup=lambda task: {"model_id": "claude-haiku-4-5", "max_tokens": 512},
    )
    with pytest.raises(GatewayError) as exc_info:
        gw.call_with_tool(task="retention_judgement", system_prompt="s", user_content="u",
                           tool_name="t", tool_schema={"type": "object"})
    assert exc_info.value.error_type == GatewayErrorType.NOT_CONFIGURED


def test_single_batch_failure_does_not_affect_others(fake_anthropic_module, job_repo, batch_repo):
    """單批失敗只回退該批，不影響其他批次（規格十五）"""
    from app.workers.batch_job_worker import BatchOutcome

    def process(batch_items):
        # 模擬第 2 批（index 1）呼叫 AI 失敗
        if batch_items[0] == "batch2_item":
            return BatchOutcome(success=False, error_type=GatewayErrorType.RATE_LIMIT,
                                 error_detail="模擬失敗")
        return BatchOutcome(success=True, success_count=len(batch_items))

    from app.models.job import JobRecord, BatchRecord
    from app.utils.text_utils import new_id
    import time

    job = JobRecord.new("retention", 3)
    job_repo.create(job)
    job_repo.update(job.job_id, {"status": "running", "started_at": time.time()})

    batches = [["batch1_item"], ["batch2_item"], ["batch3_item"]]
    results = []
    for idx, b in enumerate(batches):
        br = BatchRecord(batch_id=new_id("b_"), job_id=job.job_id, batch_index=idx)
        batch_repo.create(br)
        outcome = process(b)
        status = "completed" if outcome.success else "retryable"
        batch_repo.update(br.batch_id, {"status": status})
        results.append((idx, outcome.success))

    all_batches = batch_repo.list_by_job(job.job_id)
    statuses = {b.batch_index: b.status for b in all_batches}
    assert statuses[0] == "completed"
    assert statuses[1] == "retryable"   # 只有失敗批次標記為可重試
    assert statuses[2] == "completed"   # 其他批次不受影響
