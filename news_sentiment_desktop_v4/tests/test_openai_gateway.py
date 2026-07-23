"""測試：OpenAIGateway（V4.3.0 供應商切換）

以假的 openai 模組驗證：function calling 解析、claude 型號自動對應、
token 參數與 temperature 自癒、限流重試、json_mode 備援、錯誤分類。
不需要安裝 openai SDK 或呼叫真實 API。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from app.services.ai.openai_gateway import OpenAIGateway, _classify_openai_exception
from app.services.ai.model_gateway import GatewayError, GatewayErrorType


# ---------------------------------------------------------------------------
# 假 openai 模組
# ---------------------------------------------------------------------------
class _FakeFunction:
    def __init__(self, arguments):
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, arguments):
        self.function = _FakeFunction(arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 20


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]
        self.usage = _FakeUsage()


@pytest.fixture()
def fake_openai(monkeypatch):
    """注入假 openai 模組；測試以 handler(kwargs) 控制每次回應/例外"""
    state = {"handler": None, "calls": []}

    class _Completions:
        def create(self, **kwargs):
            state["calls"].append(kwargs)
            return state["handler"](kwargs)

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None, timeout=None):
            self.chat = _Chat()

    fake = types.ModuleType("openai")
    fake.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", fake)
    return state


def _make_gateway(model_id="gpt-5.5", max_retries=3):
    return OpenAIGateway(
        api_key_provider=lambda: "sk-test",
        task_model_lookup=lambda t: {"model_id": model_id, "max_tokens": 1000,
                                      "temperature": 0.2},
        default_model="gpt-5.5-default",
        max_retries=max_retries, retry_backoff_base_sec=0.0,
    )


def _tool_response(data: dict) -> _FakeResponse:
    return _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall(json.dumps(data, ensure_ascii=False))]))


# ---------------------------------------------------------------------------
# 測試
# ---------------------------------------------------------------------------
def test_call_with_tool_parses_function_arguments(fake_openai):
    fake_openai["handler"] = lambda kw: _tool_response({"judgements": [{"row_id": "a"}],
                                                          "note": "內容。</tag>"})
    gw = _make_gateway()
    result = gw.call_with_tool("retention_judgement", "sys", "user", "tool",
                                {"type": "object", "properties": {}})
    assert result.data["judgements"] == [{"row_id": "a"}]
    assert result.data["note"] == "內容。"          # 輸出清洗套用
    assert result.usage == {"input_tokens": 10, "output_tokens": 20}
    sent = fake_openai["calls"][0]
    assert sent["tool_choice"]["function"]["name"] == "tool"   # 強制指定 function
    assert sent["messages"][0]["role"] == "system"


def test_claude_model_id_mapped_to_default_openai_model(fake_openai):
    fake_openai["handler"] = lambda kw: _tool_response({"ok": True})
    gw = _make_gateway(model_id="claude-sonnet-5")
    gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert fake_openai["calls"][0]["model"] == "gpt-5.5-default"


def test_token_param_self_healing(fake_openai):
    """max_completion_tokens 不支援時換 max_tokens 重送並記入快取"""
    def handler(kw):
        if "max_completion_tokens" in kw:
            raise Exception("400 invalid_request: Unsupported parameter 'max_completion_tokens', "
                             "use 'max_tokens' instead")
        return _tool_response({"ok": True})
    fake_openai["handler"] = handler
    gw = _make_gateway()
    result = gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert result.data == {"ok": True}
    assert gw._token_param["gpt-5.5"] == "max_tokens"   # 已學到
    assert "max_tokens" in fake_openai["calls"][-1]


def test_temperature_self_healing(fake_openai):
    def handler(kw):
        if "temperature" in kw:
            raise Exception("400 invalid_request: 'temperature' is not supported with this model")
        return _tool_response({"ok": True})
    fake_openai["handler"] = handler
    gw = _make_gateway()
    result = gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert result.data == {"ok": True}
    assert "gpt-5.5" in gw._no_temperature
    assert "temperature" not in fake_openai["calls"][-1]


def test_truncated_output_retries_with_bigger_budget(fake_openai, monkeypatch):
    """finish_reason=length（輸出達 max_tokens 上限被截斷）→ 自動加大額度重試。
    截斷的 JSON 若流出去，部分解析會讓漏判項目默默套保守後備值
    （留用初判整批被誤標不留用的根因之一）。學到的額度會記住供後續批次起跳。"""
    from app.services.ai import openai_gateway as og
    monkeypatch.setattr(og, "_LEARNED_MIN_TOKENS", {})
    calls = fake_openai["calls"]

    def handler(kw):
        if len(calls) == 1:
            resp = _tool_response({"ok": True})
            resp.choices[0].finish_reason = "length"   # 第一次：被截斷
            return resp
        return _tool_response({"ok": True})
    fake_openai["handler"] = handler
    gw = _make_gateway()
    result = gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert result.data == {"ok": True}
    first_budget = calls[0].get("max_completion_tokens") or calls[0].get("max_tokens")
    second_budget = calls[1].get("max_completion_tokens") or calls[1].get("max_tokens")
    assert second_budget == first_budget * 3   # 加大三倍重試

    # 同任務的下一批直接以學到的額度起跳
    gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    third_budget = calls[2].get("max_completion_tokens") or calls[2].get("max_tokens")
    assert third_budget == second_budget


def test_unknown_model_falls_back_to_default(fake_openai, monkeypatch):
    """設定頁填錯模型名稱（例如不存在的 gpt-5.5-mini）→ 404 model_not_found。
    自癒：記入不可用清單、改用預設模型立即重試；後續呼叫直接用預設模型起跳。"""
    from app.services.ai import openai_gateway as og
    monkeypatch.setattr(og, "_UNAVAILABLE_MODELS", set())

    def handler(kw):
        if kw["model"] == "gpt-not-exist":
            raise Exception("Error code: 404 - {'error': {'message': 'The model `gpt-not-exist` "
                             "does not exist or you do not have access to it.', "
                             "'code': 'model_not_found'}}")
        return _tool_response({"ok": True})
    fake_openai["handler"] = handler
    gw = _make_gateway(model_id="gpt-not-exist")
    result = gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert result.data == {"ok": True}
    assert fake_openai["calls"][0]["model"] == "gpt-not-exist"
    assert fake_openai["calls"][1]["model"] == "gpt-5.5-default"   # 落回預設模型重試

    # 學到之後：同一個錯誤型號直接解析成預設模型，不再撞 404
    fake_openai["calls"].clear()
    gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert fake_openai["calls"][0]["model"] == "gpt-5.5-default"


def test_retry_on_rate_limit_then_success(fake_openai):
    attempts = {"n": 0}

    def handler(kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Exception("429 rate limit exceeded, please retry")
        return _tool_response({"ok": True})
    fake_openai["handler"] = handler
    gw = _make_gateway()
    assert gw.call_with_tool("t", "s", "u", "tool", {"type": "object"}).data == {"ok": True}
    assert attempts["n"] == 2


def test_auth_error_no_retry(fake_openai):
    attempts = {"n": 0}

    def handler(kw):
        attempts["n"] += 1
        raise Exception("401 Incorrect API key provided")
    fake_openai["handler"] = handler
    gw = _make_gateway()
    with pytest.raises(GatewayError) as ei:
        gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert ei.value.error_type == GatewayErrorType.AUTH
    assert attempts["n"] == 1   # 認證錯誤不重試


def test_json_mode_fallback_when_no_tool_call(fake_openai):
    """模型不回 function call 時，降級 json_mode 用文字 JSON 解析"""
    def handler(kw):
        if "tools" in kw:
            return _FakeResponse(_FakeMessage(content="我用文字回答"))  # 沒 tool_calls
        return _FakeResponse(_FakeMessage(content='{"topics": []}'))
    fake_openai["handler"] = handler
    gw = _make_gateway(max_retries=1)
    result = gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert result.stop_reason == "json_mode_fallback"
    assert result.data == {"topics": []}


def test_call_text_strips_artifacts(fake_openai):
    fake_openai["handler"] = lambda kw: _FakeResponse(
        _FakeMessage(content="中間摘要。</summary>"))
    gw = _make_gateway()
    assert gw.call_text("t", "s", "u") == "中間摘要。"


def test_not_configured_without_key(fake_openai):
    gw = OpenAIGateway(api_key_provider=lambda: None,
                        task_model_lookup=lambda t: {"model_id": "gpt-5.5"})
    with pytest.raises(GatewayError) as ei:
        gw.call_with_tool("t", "s", "u", "tool", {"type": "object"})
    assert ei.value.error_type == GatewayErrorType.NOT_CONFIGURED


def test_error_classification():
    assert _classify_openai_exception(Exception("429 rate limit")) == GatewayErrorType.RATE_LIMIT
    assert _classify_openai_exception(Exception("Request timed out")) == GatewayErrorType.TIMEOUT
    assert _classify_openai_exception(Exception("500 internal error")) == GatewayErrorType.OVERLOADED
    assert _classify_openai_exception(Exception("400 invalid_request")) == GatewayErrorType.INVALID_REQUEST


def test_app_settings_provider_defaults():
    from app.models.settings import ApiSettings
    s = ApiSettings()
    assert s.provider == "anthropic"          # 既有使用者升級後預設不變，需手動切換
    assert s.openai_default_model == "gpt-5.5"
