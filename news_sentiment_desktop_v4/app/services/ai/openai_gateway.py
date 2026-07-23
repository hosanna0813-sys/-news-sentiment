"""
OpenAIGateway — OpenAI (ChatGPT) API 閘道，與 ModelGateway 介面完全相同

供應商切換（V4.3.0）：AppContext 依 settings.api.provider 決定建立
ModelGateway（Anthropic）或本類別（OpenAI）。兩者公開介面一致
（call_with_tool / call_json_mode / call_text / test_connection /
get_model_for_task），所有分析服務（留用、分群、綜整、立場、規則、
Prompt 調校）完全不需要知道底層是哪一家。

與 ModelGateway 相同的設計：
    - 逾時、重試、指數退避、錯誤分類（沿用 GatewayError / GatewayErrorType）
    - Tool Use（OpenAI 稱 function calling）強制結構化輸出，
      解析失敗時降級 json_mode 備援
    - 參數自癒：token 參數名稱（max_completion_tokens vs max_tokens）與
      temperature 支援度依模型在執行期學習快取
    - 模型輸出清洗（strip_model_artifacts）套用於所有輸出路徑

模型對應：任務設定裡若還是 claude-* 的模型 ID（切換供應商後未逐一改），
自動以 default_model（設定頁的「OpenAI 預設模型」）取代並記 log，
不會讓請求打到不存在的模型。
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional, Dict, Any, List, Callable

from app.utils.logging_setup import get_logger
from app.utils.text_utils import safe_json_loads, strip_artifacts_deep, strip_model_artifacts
from app.services.ai.model_gateway import GatewayError, GatewayErrorType, ToolUseResult

logger = get_logger("openai_gateway")

# 參數自癒的執行期能力快取（與 ModelGateway 的 model_capabilities 同精神）。
# 放模組層級而非 gateway 實例屬性：切換供應商／改設定會重建 gateway，
# 已學到的「這個模型要用哪個 token 參數／不支援 temperature」不該跟著蒸發，
# 否則每次重建都要再吃一次 400 才重新學會。
_TOKEN_PARAM: Dict[str, str] = {}      # model_id -> "max_completion_tokens"/"max_tokens"
_NO_TEMPERATURE: set = set()            # 不支援 temperature 的模型
# 截斷自癒學到的「任務最低輸出額度」（task -> max_tokens），同 model_gateway：
# 某任務被截斷加大過，同一次執行內後續批次直接以大額度起跳
_LEARNED_MIN_TOKENS: Dict[str, int] = {}
# 已確認「此帳戶不可用」的模型 ID（404 model_not_found 學到的）——
# 後續呼叫直接改用預設模型，不再每批撞一次 404
_UNAVAILABLE_MODELS: set = set()

_HTTP_STATUS_CLASS = {
    "401": GatewayErrorType.AUTH, "403": GatewayErrorType.AUTH,
    "429": GatewayErrorType.RATE_LIMIT,
    "408": GatewayErrorType.TIMEOUT,
    "500": GatewayErrorType.OVERLOADED, "502": GatewayErrorType.OVERLOADED,
    "503": GatewayErrorType.OVERLOADED, "529": GatewayErrorType.OVERLOADED,
}


def _classify_openai_exception(e: Exception) -> str:
    """將 openai SDK 例外分類為與 ModelGateway 相同的錯誤型態。

    判斷順序（可靠 → 不可靠）：
    1. SDK 例外類名（AuthenticationError/RateLimitError/...）
    2. status_code 屬性（openai 的 APIStatusError 系列都有）
    3. 訊息關鍵字；HTTP 狀態碼只用字邊界比對（\\b429\\b），避免像
       「max_tokens: 14000」這種內容裡恰好含數字造成誤分類
    不依賴 SDK 匯入成功，測試可用假模組／一般 Exception。"""
    name = type(e).__name__.lower()
    if "authentication" in name or "permissiondenied" in name:
        return GatewayErrorType.AUTH
    if "ratelimit" in name:
        return GatewayErrorType.RATE_LIMIT
    if "timeout" in name:
        return GatewayErrorType.TIMEOUT
    if "internalserver" in name:
        return GatewayErrorType.OVERLOADED
    if "badrequest" in name or "unprocessableentity" in name or "notfound" in name:
        return GatewayErrorType.INVALID_REQUEST

    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        mapped = _HTTP_STATUS_CLASS.get(str(status))
        if mapped:
            return mapped
        if 400 <= status < 500:
            return GatewayErrorType.INVALID_REQUEST
        if status >= 500:
            return GatewayErrorType.OVERLOADED

    msg = str(e).lower()
    if "invalid api key" in msg or "incorrect api key" in msg:
        return GatewayErrorType.AUTH
    if "rate limit" in msg or "rate_limit" in msg:
        return GatewayErrorType.RATE_LIMIT
    if "timed out" in msg or "timeout" in msg:
        return GatewayErrorType.TIMEOUT
    if "content" in msg and ("polic" in msg or "filter" in msg):
        return GatewayErrorType.CONTENT_POLICY
    if "overloaded" in msg:
        return GatewayErrorType.OVERLOADED
    if "invalid_request" in msg or "invalid request" in msg:
        return GatewayErrorType.INVALID_REQUEST
    m = re.search(r"\b(4\d\d|5\d\d)\b", msg)
    if m:
        code = m.group(1)
        return _HTTP_STATUS_CLASS.get(
            code,
            GatewayErrorType.INVALID_REQUEST if code.startswith("4") else GatewayErrorType.OVERLOADED)
    return GatewayErrorType.OTHER


class OpenAIGateway:
    def __init__(self, api_key_provider: Callable[[], Optional[str]],
                 task_model_lookup: Callable[[str], Dict[str, Any]],
                 default_model: str = "gpt-5.5",
                 request_timeout_sec: int = 120, max_retries: int = 5,
                 retry_backoff_base_sec: float = 2.0):
        self._api_key_provider = api_key_provider
        self._task_model_lookup = task_model_lookup
        self.default_model = default_model
        self.request_timeout_sec = request_timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_base_sec = retry_backoff_base_sec
        # 執行期能力快取——實例屬性只是模組層級快取的別名（見檔頭說明），
        # 重建 gateway（改設定／切供應商）不會弄丟已學到的參數相容性
        self._token_param = _TOKEN_PARAM
        self._no_temperature = _NO_TEMPERATURE
        self._mapped_models: set = set()             # 已記過 log 的 claude->openai 對應

    # ---------- client 管理 ----------
    def _get_client(self):
        api_key = self._api_key_provider()
        if not api_key:
            raise GatewayError(GatewayErrorType.NOT_CONFIGURED, "尚未設定 OpenAI API Key")
        try:
            import openai  # type: ignore
        except ImportError as e:
            raise GatewayError(GatewayErrorType.NOT_CONFIGURED,
                                "尚未安裝 openai 套件，請執行 pip install openai") from e
        return openai.OpenAI(api_key=api_key, timeout=self.request_timeout_sec)

    def get_model_for_task(self, task: str) -> Dict[str, Any]:
        return self._task_model_lookup(task)

    def resolve_model_id(self, task: str) -> str:
        """實際會送出請求的模型 ID（含 claude-* → OpenAI 預設模型的對應），
        供落庫記錄（如 summarized_by_model）使用——直接拿任務設定的 model_id
        會在供應商切換後記到錯的名字"""
        return self._resolve_model(self._task_model_lookup(task))

    def _resolve_model(self, cfg: Dict[str, Any]) -> str:
        """任務設定的模型 ID 若是 claude-*、空值、或已確認此帳戶不可用（404 學到的），
        自動改用 OpenAI 預設模型"""
        model_id = cfg.get("model_id", "") or ""
        if model_id in _UNAVAILABLE_MODELS:
            return self.default_model
        if not model_id or model_id.startswith("claude"):
            if model_id and model_id not in self._mapped_models:
                self._mapped_models.add(model_id)
                logger.info(f"任務模型 {model_id} 為 Claude 型號，OpenAI 供應商下改用預設模型 {self.default_model}"
                             "（可在設定頁逐任務改成 OpenAI 型號）")
            return self.default_model
        return model_id

    def test_connection(self) -> Dict[str, Any]:
        try:
            client = self._get_client()
            self._create(client, self.default_model,
                          messages=[{"role": "user", "content": "ping"}], max_tokens=8)
            return {"ok": True, "message": "連線成功"}
        except GatewayError as e:
            return {"ok": False, "message": e.message, "error_type": e.error_type}
        except Exception as e:
            return {"ok": False, "message": str(e), "error_type": _classify_openai_exception(e)}

    # ---------- 內部：帶參數自癒的 chat.completions.create ----------
    def _create(self, client, model_id: str, messages: List[dict], max_tokens: int,
                 temperature: Optional[float] = None, tools: Optional[list] = None,
                 tool_choice: Optional[dict] = None):
        """
        參數自癒（執行期學習，最多重試 3 次參數組合）：
        1. token 參數：新款模型用 max_completion_tokens，舊款用 max_tokens；
           400 指出參數名不對時換另一個並記到快取。
        2. temperature：部分模型不支援，400 指出時剝除並記到快取。
        """
        token_param = self._token_param.get(model_id, "max_completion_tokens")
        use_temperature = temperature is not None and model_id not in self._no_temperature
        for _ in range(3):
            kwargs: Dict[str, Any] = {"model": model_id, "messages": messages,
                                       token_param: max_tokens}
            if use_temperature:
                kwargs["temperature"] = temperature
            if tools is not None:
                kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            try:
                resp = client.chat.completions.create(**kwargs)
                self._token_param[model_id] = token_param
                return resp
            except Exception as e:
                if _classify_openai_exception(e) != GatewayErrorType.INVALID_REQUEST:
                    raise
                msg = str(e)
                if token_param in msg and ("unsupported" in msg.lower() or "not supported" in msg.lower()
                                            or "unrecognized" in msg.lower() or "use" in msg.lower()):
                    other = ("max_tokens" if token_param == "max_completion_tokens"
                              else "max_completion_tokens")
                    logger.warning(f"模型 {model_id} 不支援參數 `{token_param}`，改用 `{other}` 重送")
                    token_param = other
                    continue
                if use_temperature and "temperature" in msg:
                    logger.warning(f"模型 {model_id} 不支援參數 `temperature`，已記錄並剝除後重送")
                    self._no_temperature.add(model_id)
                    use_temperature = False
                    continue
                raise
        raise GatewayError(GatewayErrorType.INVALID_REQUEST,
                            f"參數自癒後仍無法送出請求（model={model_id}）")

    @staticmethod
    def _build_messages(system_prompt: str, user_content: str,
                         extra_messages: Optional[List[Dict[str, Any]]] = None) -> List[dict]:
        messages: List[dict] = [{"role": "system", "content": system_prompt}]
        if extra_messages:
            messages += extra_messages
        messages.append({"role": "user", "content": user_content})
        return messages

    # ---------- 核心呼叫：function calling 結構化輸出 ----------
    def call_with_tool(self, task: str, system_prompt: str, user_content: str,
                        tool_name: str, tool_schema: Dict[str, Any],
                        extra_messages: Optional[List[Dict[str, Any]]] = None) -> ToolUseResult:
        cfg = self._task_model_lookup(task)
        model_id = self._resolve_model(cfg)
        max_tokens = max(cfg.get("max_tokens", 4096), _LEARNED_MIN_TOKENS.get(task, 0))
        temperature = cfg.get("temperature", 0.3)
        tools = [{"type": "function", "function": {
            "name": tool_name,
            "description": f"回傳 {tool_name} 任務所需的結構化欄位",
            "parameters": tool_schema,
        }}]
        tool_choice = {"type": "function", "function": {"name": tool_name}}
        messages = self._build_messages(system_prompt, user_content, extra_messages)

        last_error: Optional[GatewayError] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_client()
                resp = self._create(client, model_id, messages, max_tokens, temperature,
                                     tools=tools, tool_choice=tool_choice)
                choice = resp.choices[0]
                # 截斷自癒：推理型模型（gpt-5.x）會先消耗大量輸出 token 思考，
                # 設定的 max_tokens 很容易不夠，輸出 JSON 被砍半——部分解析器
                # 仍可能救回「前幾筆」，剩下的項目就默默套用保守後備值（例如
                # 留用初判整批被誤標不留用）。偵測 finish_reason=length 時
                # 把額度加大三倍重試，不讓截斷結果流出去。
                if (getattr(choice, "finish_reason", "") or "") == "length":
                    old_budget = max_tokens
                    max_tokens = min(max_tokens * 3, 32000)
                    _LEARNED_MIN_TOKENS[task] = max(_LEARNED_MIN_TOKENS.get(task, 0), max_tokens)
                    raise GatewayError(
                        GatewayErrorType.PARSE_ERROR,
                        f"模型輸出達 max_tokens 上限被截斷（{old_budget} → 已自動調高為 {max_tokens} 重試）")
                tool_calls = getattr(choice.message, "tool_calls", None)
                if not tool_calls:
                    raise GatewayError(GatewayErrorType.PARSE_ERROR, "模型未回傳 function call")
                args_text = tool_calls[0].function.arguments
                data = safe_json_loads(args_text)
                if data is None:
                    raise GatewayError(GatewayErrorType.PARSE_ERROR,
                                        f"function 參數 JSON 解析失敗：{str(args_text)[:200]}")
                usage = getattr(resp, "usage", None)
                return ToolUseResult(
                    data=strip_artifacts_deep(data),
                    raw_text=strip_model_artifacts(getattr(choice.message, "content", "") or ""),
                    model_used=model_id,
                    stop_reason=getattr(choice, "finish_reason", "") or "",
                    usage={"input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                           "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0},
                )
            except GatewayError as ge:
                last_error = ge
                if ge.error_type == GatewayErrorType.NOT_CONFIGURED:
                    raise
                logger.warning(f"[{task}] 第 {attempt} 次呼叫失敗 ({ge.error_type}): {ge.message}")
            except Exception as e:
                err_type = _classify_openai_exception(e)
                msg = str(e)
                # 模型不存在自癒：設定頁填錯型號（或新型號帳戶尚無權限）時，
                # 記入不可用清單並改用預設模型立即重試，而不是整批 404 失敗
                if "model_not_found" in msg or "does not exist or you do not have access" in msg:
                    _UNAVAILABLE_MODELS.add(model_id)
                    if model_id != self.default_model:
                        logger.warning(f"[{task}] 模型 {model_id} 不存在或無權限，"
                                        f"改用預設模型 {self.default_model} 重試"
                                        "（請到設定頁修正該任務的模型名稱）")
                        model_id = self.default_model
                        continue
                    msg += "\n→ 此模型不存在或帳戶無權限：請到「系統設定 → 任務模型設定」" \
                           "確認模型名稱（可用「測試連線」驗證預設模型）"
                elif "insufficient_quota" in msg or "exceeded your current quota" in msg:
                    msg += "\n→ OpenAI 帳戶額度不足：請至 platform.openai.com 儲值，" \
                           "或到「系統設定 → AI 供應商」切換為 Anthropic 後重跑"
                elif "invalid_api_key" in msg or "Incorrect API key" in msg:
                    msg += "\n→ OpenAI API Key 無效：請至 platform.openai.com/api-keys 重新產生，" \
                           "在「系統設定 → AI 供應商 / API」更新後按「測試連線」驗證"
                last_error = GatewayError(err_type, msg, raw=e)
                logger.warning(f"[{task}] 第 {attempt} 次呼叫失敗 ({err_type}): {e}")

            if last_error and last_error.error_type in (
                    GatewayErrorType.AUTH, GatewayErrorType.INVALID_REQUEST):
                break
            if attempt < self.max_retries:
                time.sleep(min(self.retry_backoff_base_sec * (2 ** (attempt - 1)), 30))

        assert last_error is not None
        if last_error.error_type == GatewayErrorType.PARSE_ERROR:
            logger.warning(f"[{task}] function calling 解析失敗，降級改用 json_mode 備援")
            try:
                data = self.call_json_mode(task=task, system_prompt=system_prompt,
                                            user_content=user_content)
                return ToolUseResult(data=data, raw_text="", model_used=model_id,
                                      stop_reason="json_mode_fallback", usage={})
            except GatewayError:
                pass
        raise last_error

    # ---------- json_mode 備援 ----------
    def call_json_mode(self, task: str, system_prompt: str, user_content: str) -> Any:
        cfg = self._task_model_lookup(task)
        model_id = self._resolve_model(cfg)
        strict_system = system_prompt + "\n\n重要：只回傳合法 JSON，不要包含任何說明文字或 markdown 標記。"
        messages = self._build_messages(strict_system, user_content)

        max_tokens = max(cfg.get("max_tokens", 4096), _LEARNED_MIN_TOKENS.get(task, 0))
        last_error: Optional[GatewayError] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._get_client()
                resp = self._create(client, model_id, messages,
                                     max_tokens, cfg.get("temperature", 0.3))
                choice = resp.choices[0]
                # 截斷自癒（同 call_with_tool）：截斷的 JSON 不可流出去
                if (getattr(choice, "finish_reason", "") or "") == "length":
                    old_budget = max_tokens
                    max_tokens = min(max_tokens * 3, 32000)
                    _LEARNED_MIN_TOKENS[task] = max(_LEARNED_MIN_TOKENS.get(task, 0), max_tokens)
                    raise GatewayError(
                        GatewayErrorType.PARSE_ERROR,
                        f"模型輸出達 max_tokens 上限被截斷（{old_budget} → 已自動調高為 {max_tokens} 重試）")
                text = getattr(choice.message, "content", "") or ""
                parsed = safe_json_loads(text)
                if parsed is None:
                    raise GatewayError(GatewayErrorType.PARSE_ERROR,
                                        f"JSON 解析失敗，原始回應：{text[:200]}")
                return strip_artifacts_deep(parsed)
            except GatewayError as ge:
                last_error = ge
                if ge.error_type == GatewayErrorType.NOT_CONFIGURED:
                    raise
                logger.warning(f"[{task}] json_mode 第 {attempt} 次失敗: {ge.message}")
            except Exception as e:
                err_type = _classify_openai_exception(e)
                msg = str(e)
                # 模型不存在自癒（同 call_with_tool）
                if ("model_not_found" in msg or "does not exist or you do not have access" in msg):
                    _UNAVAILABLE_MODELS.add(model_id)
                    if model_id != self.default_model:
                        logger.warning(f"[{task}] json_mode：模型 {model_id} 不存在或無權限，"
                                        f"改用預設模型 {self.default_model} 重試")
                        model_id = self.default_model
                        continue
                last_error = GatewayError(err_type, msg, raw=e)
                logger.warning(f"[{task}] json_mode 第 {attempt} 次失敗 ({err_type}): {e}")
            if last_error and last_error.error_type in (
                    GatewayErrorType.AUTH, GatewayErrorType.INVALID_REQUEST):
                break
            if attempt < self.max_retries:
                time.sleep(min(self.retry_backoff_base_sec * (2 ** (attempt - 1)), 30))
        assert last_error is not None
        raise last_error

    # ---------- 純文字輸出 ----------
    def call_text(self, task: str, system_prompt: str, user_content: str,
                   max_tokens: int = 2048) -> str:
        cfg = self._task_model_lookup(task)
        model_id = self._resolve_model(cfg)
        client = self._get_client()
        resp = self._create(client, model_id,
                             self._build_messages(system_prompt, user_content),
                             max_tokens, cfg.get("temperature", 0.3))
        return strip_model_artifacts(getattr(resp.choices[0].message, "content", "") or "")
