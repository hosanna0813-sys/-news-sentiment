"""
ModelGateway — 唯一的 Anthropic API 呼叫入口

規格要求（四）：
    「所有 AI 請求須經由單一 AIClient／ModelGateway 管理...不可在各頁面
    散落直接呼叫 API。」

職責：
    - 依任務類型選擇對應模型（由呼叫端傳入 task，內部查 AppSettings.task_models）
    - 組裝 system prompt / user prompt
    - 處理 Tool Use 結構化輸出與 JSON 解析（含容錯重試）
    - 逾時、重試、指數退避
    - 呼叫錯誤分類（authentication_error / rate_limit_error / overloaded_error /
      invalid_request_error / timeout / content_policy / other）並回報給上層工作佇列
    - 視情況切換 Messages API（即時）或 Message Batches API（批次）

本模組刻意不在頂層 import `anthropic`，而是在建構時才 import，
如此一來即使尚未安裝 SDK，其餘不需要呼叫 AI 的功能（匯入、留用清單瀏覽、
Word 樣式設定等）仍可正常執行與測試。
"""
from __future__ import annotations

import time
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Callable

from app.utils.logging_setup import get_logger
from app.utils.text_utils import safe_json_loads, strip_artifacts_deep, strip_model_artifacts
from app.services.ai.model_capabilities import (
    sanitize_params, get_capability, strip_learned_unsupported,
    record_unsupported_param, detect_unsupported_param,
)

logger = get_logger("model_gateway")


class GatewayErrorType:
    AUTH = "authentication_error"
    RATE_LIMIT = "rate_limit_error"
    OVERLOADED = "overloaded_error"
    INVALID_REQUEST = "invalid_request_error"
    TIMEOUT = "timeout"
    CONTENT_POLICY = "content_policy"
    NOT_CONFIGURED = "not_configured"
    PARSE_ERROR = "parse_error"
    OTHER = "other"


class GatewayError(Exception):
    def __init__(self, error_type: str, message: str, raw: Optional[Exception] = None):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.raw = raw


@dataclass
class ToolUseResult:
    """單次 Tool Use 呼叫的結構化結果"""
    data: Any                     # 解析後的 JSON（dict 或 list）
    raw_text: str                 # 模型若有附帶文字說明
    model_used: str
    stop_reason: str
    usage: Dict[str, int]


def _classify_exception(e: Exception) -> str:
    """將 anthropic SDK 例外分類為規格書要求的錯誤型態"""
    try:
        import anthropic  # type: ignore
    except Exception:
        anthropic = None

    if anthropic is not None:
        if isinstance(e, anthropic.AuthenticationError):
            return GatewayErrorType.AUTH
        if isinstance(e, anthropic.RateLimitError):
            return GatewayErrorType.RATE_LIMIT
        if isinstance(e, anthropic.APIStatusError):
            status = getattr(e, "status_code", None)
            if status == 529:
                return GatewayErrorType.OVERLOADED
            if status == 400:
                return GatewayErrorType.INVALID_REQUEST
            if status == 401:
                return GatewayErrorType.AUTH
        if isinstance(e, anthropic.APITimeoutError):
            return GatewayErrorType.TIMEOUT
        if isinstance(e, anthropic.APIConnectionError):
            return GatewayErrorType.OTHER

    msg = str(e).lower()
    if "timeout" in msg:
        return GatewayErrorType.TIMEOUT
    if "rate limit" in msg or "429" in msg:
        return GatewayErrorType.RATE_LIMIT
    if "overloaded" in msg or "529" in msg:
        return GatewayErrorType.OVERLOADED
    if "auth" in msg or "401" in msg:
        return GatewayErrorType.AUTH
    if "content" in msg and "polic" in msg:
        return GatewayErrorType.CONTENT_POLICY
    return GatewayErrorType.OTHER


class ModelGateway:
    def __init__(self, api_key_provider: Callable[[], Optional[str]],
                 task_model_lookup: Callable[[str], Dict[str, Any]],
                 request_timeout_sec: int = 60, max_retries: int = 5,
                 retry_backoff_base_sec: float = 2.0):
        """
        api_key_provider: 呼叫時才取得 API Key 的函式（避免長期持有明碼於記憶體中過久）
        task_model_lookup: task -> {"model_id", "max_tokens", "temperature",
                                     "use_extended_thinking", "use_message_batches"}
        """
        self._api_key_provider = api_key_provider
        self._task_model_lookup = task_model_lookup
        self.request_timeout_sec = request_timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_base_sec = retry_backoff_base_sec
        self._client = None

    # ---------- client 管理 ----------
    def _get_client(self):
        api_key = self._api_key_provider()
        if not api_key:
            raise GatewayError(GatewayErrorType.NOT_CONFIGURED, "尚未設定 Anthropic API Key")
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise GatewayError(GatewayErrorType.NOT_CONFIGURED,
                                "尚未安裝 anthropic 套件，請執行 pip install anthropic") from e
        # 每次呼叫重新建立 client（成本很低），避免 API Key 變更後仍使用舊 key
        return anthropic.Anthropic(api_key=api_key, timeout=self.request_timeout_sec)

    def get_model_for_task(self, task: str) -> Dict[str, Any]:
        """公開方法：查詢某任務目前設定使用的模型與參數"""
        return self._task_model_lookup(task)

    def test_connection(self) -> Dict[str, Any]:
        """設定頁「測試」按鈕：以最小成本呼叫驗證 API Key 是否可用"""
        try:
            client = self._get_client()
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            return {"ok": True, "message": "連線成功"}
        except GatewayError as e:
            return {"ok": False, "message": e.message, "error_type": e.error_type}
        except Exception as e:
            err_type = _classify_exception(e)
            return {"ok": False, "message": str(e), "error_type": err_type}

    # ---------- 內部：帶參數自癒的 messages.create ----------
    def _create_message(self, task: str, model_id: str, params: dict, **create_kwargs):
        """
        呼叫 client.messages.create，並處理參數棄用自癒（V4.1.3）：
        1. 先剝除已學到「此模型不支援」的參數。
        2. 若 API 回 400 且訊息指出某已送參數 deprecated/not supported，
           記錄至執行期能力快取、剝除該參數後立即重送（每個參數只剝一次，
           最多剝 len(params) 次，避免無限循環）。
        3. 其他 400（如 schema 錯誤）不屬於參數問題，直接拋出。
        """
        params = strip_learned_unsupported(model_id, dict(params))
        client = self._get_client()
        for _ in range(len(params) + 1):
            try:
                return self._send(client, model_id, params, create_kwargs)
            except Exception as e:
                err_type = _classify_exception(e)
                if err_type != GatewayErrorType.INVALID_REQUEST:
                    raise
                bad_param = detect_unsupported_param(str(e), params)
                if bad_param is None:
                    raise  # 非參數棄用類的 400，交由上層處理（不重試）
                logger.warning(f"[{task}] 模型 {model_id} 不支援參數 `{bad_param}`，"
                                f"已記錄並剝除後重送")
                record_unsupported_param(model_id, bad_param)
                params.pop(bad_param, None)
        raise GatewayError(GatewayErrorType.INVALID_REQUEST,
                            f"參數自癒後仍無法送出請求（model={model_id}）")

    @staticmethod
    def _send(client, model_id: str, params: dict, create_kwargs: dict):
        """
        實際送出請求（V4.1.6）：優先使用串流模式。
        長議題綜整（Opus 生成大量文字）在非串流模式下容易超過連線逾時而失敗
        （Anthropic 官方建議 long requests 使用 streaming）；串流模式逐段接收，
        讀取逾時以每段計算，長生成不會被整段切斷。SDK 過舊不支援 stream 時
        回退為一般 create。
        """
        stream_fn = getattr(getattr(client, "messages", None), "stream", None)
        if stream_fn is not None:
            try:
                with stream_fn(model=model_id, **create_kwargs, **params) as s:
                    return s.get_final_message()
            except TypeError:
                pass  # 少數舊版 SDK 參數簽名不符，回退非串流
        return client.messages.create(model=model_id, **create_kwargs, **params)

    # ---------- 核心呼叫：Tool Use 結構化輸出 ----------
    def call_with_tool(self, task: str, system_prompt: str, user_content: str,
                        tool_name: str, tool_schema: Dict[str, Any],
                        extra_messages: Optional[List[Dict[str, Any]]] = None) -> ToolUseResult:
        """
        以 Tool Use 強制模型回傳結構化 JSON（規格四建議的主要方式）。
        含逾時重試 + 指數退避；重試會分類錯誤並在耗盡重試次數後拋出 GatewayError，
        由呼叫端（worker）標記該批為 failed/retryable，不影響其他已完成批次。
        """
        cfg = self._task_model_lookup(task)
        model_id = cfg.get("model_id", "claude-sonnet-5")
        params = sanitize_params(
            model_id=model_id,
            max_tokens=cfg.get("max_tokens", 4096),
            temperature=cfg.get("temperature", 0.3),
            use_extended_thinking=cfg.get("use_extended_thinking", False),
        )

        messages = [{"role": "user", "content": user_content}]
        if extra_messages:
            messages = extra_messages + messages

        tools = [{
            "name": tool_name,
            "description": f"回傳 {tool_name} 任務所需的結構化欄位",
            "input_schema": tool_schema,
        }]

        last_error: Optional[GatewayError] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._create_message(
                    task, model_id, params,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    tool_choice={"type": "tool", "name": tool_name},
                )
                tool_block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
                text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
                if tool_block is None:
                    raise GatewayError(GatewayErrorType.PARSE_ERROR, "模型未回傳 tool_use 區塊")
                return ToolUseResult(
                    data=strip_artifacts_deep(tool_block.input),
                    raw_text=strip_model_artifacts(
                        getattr(text_block, "text", "") if text_block else ""),
                    model_used=model_id,
                    stop_reason=resp.stop_reason,
                    usage={"input_tokens": resp.usage.input_tokens,
                           "output_tokens": resp.usage.output_tokens},
                )
            except GatewayError as ge:
                last_error = ge
                if ge.error_type == GatewayErrorType.NOT_CONFIGURED:
                    raise  # 沒設定 key，重試無意義
                logger.warning(f"[{task}] 第 {attempt} 次呼叫失敗 ({ge.error_type}): {ge.message}")
            except Exception as e:
                err_type = _classify_exception(e)
                last_error = GatewayError(err_type, str(e), raw=e)
                logger.warning(f"[{task}] 第 {attempt} 次呼叫失敗 ({err_type}): {e}")

            if last_error and last_error.error_type in (
                    GatewayErrorType.AUTH, GatewayErrorType.INVALID_REQUEST):
                break  # 認證錯誤與請求本身不合法：重試結果不會改變，直接停止
            if attempt < self.max_retries:
                backoff = self.retry_backoff_base_sec * (2 ** (attempt - 1))
                time.sleep(min(backoff, 30))

        assert last_error is not None
        # 備援機制（規格四第 2 點）：若失敗原因是「模型未依 Tool Use 回傳結構化資料」
        # （而非認證/限流等 API 層錯誤），改用 json_mode（system prompt 強制只回傳 JSON
        # + 應用層嚴格解析）再嘗試一次，仍失敗才向上拋出原始錯誤。
        if last_error.error_type == GatewayErrorType.PARSE_ERROR:
            logger.warning(f"[{task}] Tool Use 解析失敗，降級改用 json_mode 備援")
            try:
                data = self.call_json_mode(task=task, system_prompt=system_prompt,
                                            user_content=user_content)
                cfg2 = self._task_model_lookup(task)
                return ToolUseResult(data=data, raw_text="", model_used=cfg2.get("model_id", ""),
                                      stop_reason="json_mode_fallback", usage={})
            except GatewayError:
                pass  # 備援也失敗，拋出原始錯誤
        raise last_error

    # ---------- 純文字輸出（system prompt 已要求「只回傳合法 JSON」的備援方式） ----------
    def call_json_mode(self, task: str, system_prompt: str, user_content: str) -> Any:
        """
        次選方案：不使用 Tool Use，而是在 system prompt 明確要求「只回傳合法 JSON」，
        並在應用層以嚴格 JSON 解析 + 重試處理例外（規格四第 2 點）。
        """
        cfg = self._task_model_lookup(task)
        model_id = cfg.get("model_id", "claude-sonnet-5")
        params = sanitize_params(model_id, cfg.get("max_tokens", 4096), cfg.get("temperature", 0.3),
                                  cfg.get("use_extended_thinking", False))

        strict_system = system_prompt + "\n\n重要：只回傳合法 JSON，不要包含任何說明文字或 markdown 標記。"
        last_error: Optional[GatewayError] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._create_message(
                    task, model_id, params, system=strict_system,
                    messages=[{"role": "user", "content": user_content}],
                )
                text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
                text = getattr(text_block, "text", "") if text_block else ""
                parsed = safe_json_loads(text)
                if parsed is None:
                    raise GatewayError(GatewayErrorType.PARSE_ERROR, f"JSON 解析失敗，原始回應：{text[:200]}")
                return strip_artifacts_deep(parsed)
            except GatewayError as ge:
                last_error = ge
                if ge.error_type == GatewayErrorType.NOT_CONFIGURED:
                    raise
                logger.warning(f"[{task}] json_mode 第 {attempt} 次失敗: {ge.message}")
            except Exception as e:
                err_type = _classify_exception(e)
                last_error = GatewayError(err_type, str(e), raw=e)
                logger.warning(f"[{task}] json_mode 第 {attempt} 次失敗 ({err_type}): {e}")
            if last_error and last_error.error_type in (
                    GatewayErrorType.AUTH, GatewayErrorType.INVALID_REQUEST):
                break
            if attempt < self.max_retries:
                time.sleep(min(self.retry_backoff_base_sec * (2 ** (attempt - 1)), 30))
        assert last_error is not None
        raise last_error

    # ---------- 純文字輸出（map-reduce 中間摘要等內部用途） ----------
    def call_text(self, task: str, system_prompt: str, user_content: str,
                   max_tokens: int = 2048) -> str:
        """回傳純文字（同樣經過參數自癒與能力過濾）"""
        cfg = self._task_model_lookup(task)
        model_id = cfg.get("model_id", "claude-sonnet-5")
        params = sanitize_params(model_id, max_tokens, cfg.get("temperature", 0.3), False)
        resp = self._create_message(
            task, model_id, params, system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        return strip_model_artifacts(getattr(text_block, "text", "") if text_block else "")
