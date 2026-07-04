"""測試：參數棄用自癒機制（V4.1.3）— 偵測、剝除重送、學習快取、invalid_request 不重試"""
from __future__ import annotations

import types
import pytest

from app.services.ai.model_capabilities import (
    detect_unsupported_param, record_unsupported_param, strip_learned_unsupported,
    get_learned_unsupported, _LEARNED_UNSUPPORTED,
)
from app.services.ai.model_gateway import ModelGateway, GatewayError, GatewayErrorType


@pytest.fixture(autouse=True)
def _clear_learned():
    _LEARNED_UNSUPPORTED.clear()
    yield
    _LEARNED_UNSUPPORTED.clear()


def test_detect_unsupported_param_from_error_message():
    params = {"max_tokens": 4096, "temperature": 0.2}
    msg = "Error code: 400 - {'message': '`temperature` is deprecated for this model.'}"
    assert detect_unsupported_param(msg, params) == "temperature"


def test_detect_returns_none_for_non_param_400():
    params = {"max_tokens": 4096, "temperature": 0.2}
    assert detect_unsupported_param("invalid input_schema: xxx", params) is None


def test_strip_learned_unsupported():
    record_unsupported_param("model-x", "temperature")
    out = strip_learned_unsupported("model-x", {"max_tokens": 100, "temperature": 0.5})
    assert "temperature" not in out and out["max_tokens"] == 100
    # 其他模型不受影響
    out2 = strip_learned_unsupported("model-y", {"temperature": 0.5})
    assert "temperature" in out2


def _build_gateway(fake_anthropic_module, create_fn):
    class FakeClient:
        def __init__(self, api_key, timeout):
            self.messages = types.SimpleNamespace(create=create_fn)
    fake_anthropic_module.Anthropic = FakeClient
    return ModelGateway(
        api_key_provider=lambda: "sk-ant-x",
        task_model_lookup=lambda t: {"model_id": "claude-sonnet-5", "max_tokens": 4096,
                                       "temperature": 0.2},
        max_retries=5, retry_backoff_base_sec=0.001)


def test_gateway_strips_deprecated_param_and_learns(fake_anthropic_module):
    class ToolBlock:
        type = "tool_use"
        input = {"topics": []}

    class Usage:
        input_tokens = 1
        output_tokens = 1

    class Resp:
        content = [ToolBlock()]
        stop_reason = "tool_use"
        usage = Usage()

    calls = []

    def create(**kwargs):
        calls.append(dict(kwargs))
        if "temperature" in kwargs:
            raise fake_anthropic_module.APIStatusError(
                "Error code: 400 - `temperature` is deprecated for this model.",
                status_code=400)
        return Resp()

    gw = _build_gateway(fake_anthropic_module, create)
    gw.call_with_tool(task="topic_clustering", system_prompt="s", user_content="u",
                       tool_name="t", tool_schema={"type": "object"})
    assert len(calls) == 2  # 第一次帶 temperature 失敗，剝除後第二次成功
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    assert "temperature" in get_learned_unsupported("claude-sonnet-5")

    # 學過之後：後續呼叫直接不送，一次成功
    calls.clear()
    gw.call_with_tool(task="topic_clustering", system_prompt="s", user_content="u",
                       tool_name="t", tool_schema={"type": "object"})
    assert len(calls) == 1 and "temperature" not in calls[0]


def test_non_param_invalid_request_not_retried(fake_anthropic_module):
    calls = []

    def create(**kwargs):
        calls.append(1)
        raise fake_anthropic_module.APIStatusError(
            "Error code: 400 - invalid input_schema: xxx", status_code=400)

    gw = _build_gateway(fake_anthropic_module, create)
    with pytest.raises(GatewayError) as exc:
        gw.call_with_tool(task="rule_draft", system_prompt="s", user_content="u",
                           tool_name="t", tool_schema={"type": "object"})
    assert exc.value.error_type == GatewayErrorType.INVALID_REQUEST
    assert len(calls) == 1  # invalid_request 不重試
