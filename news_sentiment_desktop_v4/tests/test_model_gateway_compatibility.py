"""
測試：ModelGateway 對不同模型參數相容性處理（規格十七第 7 點）

驗證不同模型（haiku/sonnet/opus）的能力差異會正確反映在實際送出的參數上，
避免送出不支援的參數（例如 haiku 不支援 extended thinking）而導致 API 呼叫失敗。
"""
from __future__ import annotations

import types

from app.services.ai.model_capabilities import sanitize_params, get_capability
from app.services.ai.model_gateway import ModelGateway


def test_haiku_does_not_receive_thinking_param():
    params = sanitize_params("claude-haiku-4-5", max_tokens=4096, temperature=0.0,
                              use_extended_thinking=True)
    assert "thinking" not in params, "claude-haiku-4-5 不支援 extended thinking，不應送出 thinking 參數"
    assert params["temperature"] == 0.0


def test_opus_receives_thinking_param_when_requested():
    params = sanitize_params("claude-opus-4-8", max_tokens=8192, temperature=0.3,
                              use_extended_thinking=True)
    assert "thinking" in params
    assert params["thinking"]["type"] == "enabled"


def test_max_tokens_clamped_to_model_capability():
    cap = get_capability("claude-haiku-4-5")
    params = sanitize_params("claude-haiku-4-5", max_tokens=999999, temperature=0.0,
                              use_extended_thinking=False)
    assert params["max_tokens"] == cap.max_output_tokens


def test_unknown_model_falls_back_to_safe_defaults():
    params = sanitize_params("claude-future-model-9000", max_tokens=4096, temperature=0.5,
                              use_extended_thinking=True)
    # 未知模型使用保守 fallback：不預設支援 extended thinking
    assert "thinking" not in params
    assert params["max_tokens"] <= 4096


def test_gateway_sends_correct_model_id_per_task(fake_anthropic_module):
    captured = {}

    class FakeToolBlock:
        type = "tool_use"
        input = {"result": "ok"}

    class FakeUsage:
        input_tokens = 1
        output_tokens = 1

    class FakeResp:
        content = [FakeToolBlock()]
        stop_reason = "tool_use"
        usage = FakeUsage()

    def create(**kwargs):
        captured.update(kwargs)
        return FakeResp()

    class FakeClient:
        def __init__(self, api_key, timeout):
            self.messages = types.SimpleNamespace(create=create)

    fake_anthropic_module.Anthropic = FakeClient

    task_models = {
        "retention_judgement": {"model_id": "claude-haiku-4-5", "max_tokens": 1024, "temperature": 0.0},
        "topic_summarization": {"model_id": "claude-opus-4-8", "max_tokens": 8192, "temperature": 0.3,
                                 "use_extended_thinking": True},
    }
    gw = ModelGateway(api_key_provider=lambda: "sk-ant-x",
                       task_model_lookup=lambda task: task_models[task])

    gw.call_with_tool(task="retention_judgement", system_prompt="s", user_content="u",
                       tool_name="t", tool_schema={"type": "object"})
    assert captured["model"] == "claude-haiku-4-5"
    assert "thinking" not in captured

    gw.call_with_tool(task="topic_summarization", system_prompt="s", user_content="u",
                       tool_name="t", tool_schema={"type": "object"})
    assert captured["model"] == "claude-opus-4-8"
    assert "thinking" in captured
