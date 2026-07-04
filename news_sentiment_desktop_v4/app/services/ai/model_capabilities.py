"""
模型能力設定（Model Capabilities）

規格要求（四）：
    「不可假設所有模型都支援相同參數；例如部分模型的 temperature、
    max_tokens（輸出上限）、擴充思考（extended thinking）／effort
    參數的可用範圍不同，呼叫前應以 Models API 或設定檔確認能力，
    避免送出不支援的參數而失敗。」

實作策略：
    1. 提供一份「已知模型能力」設定檔（本檔案），做為離線預設值。
    2. ModelGateway 啟動時可選擇呼叫 Anthropic Models API
       （GET /v1/models）核對模型是否存在／取得最新清單；
       若 API 呼叫失敗（例如尚未設定 API Key），則退回本檔案的
       離線設定，不影響其餘功能操作。
    3. 呼叫前一律經過 sanitize_params()，過濾掉該模型不支援的參數，
       避免 invalid_request_error。

注意：Anthropic 的模型參數能力會持續更新，本檔案中的數值僅為
2026 年初的保守估計，實際能力請以 https://docs.claude.com 為準；
系統設計上刻意將這些值集中於單一檔案，方便未來調整而不需改動
呼叫邏輯本身。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ModelCapability:
    model_id: str
    max_output_tokens: int
    supports_temperature: bool = True
    supports_extended_thinking: bool = False
    supports_tool_use: bool = True
    tier: str = "sonnet"   # haiku / sonnet / opus，用於 UI 分類顯示


# 離線預設能力表（找不到 Models API 回應時使用）
KNOWN_MODEL_CAPABILITIES: Dict[str, ModelCapability] = {
    "claude-haiku-4-5": ModelCapability(
        model_id="claude-haiku-4-5", max_output_tokens=8192,
        supports_temperature=True, supports_extended_thinking=False,
        supports_tool_use=True, tier="haiku",
    ),
    "claude-sonnet-5": ModelCapability(
        model_id="claude-sonnet-5", max_output_tokens=16384,
        supports_temperature=True, supports_extended_thinking=True,
        supports_tool_use=True, tier="sonnet",
    ),
    "claude-opus-4-8": ModelCapability(
        model_id="claude-opus-4-8", max_output_tokens=16384,
        supports_temperature=True, supports_extended_thinking=True,
        supports_tool_use=True, tier="opus",
    ),
}

_FALLBACK_CAPABILITY = ModelCapability(
    model_id="unknown", max_output_tokens=4096, supports_temperature=True,
    supports_extended_thinking=False, supports_tool_use=True, tier="sonnet",
)

# ---------------------------------------------------------------------------
# 執行期學習能力快取（V4.1.3）
#
# 離線能力表無法跟上模型參數的棄用節奏（例如新一代模型棄用 temperature）。
# 因此改為「失敗自癒」策略：API 回覆 400 且訊息指出某參數 deprecated /
# not supported 時，記錄該模型不支援該參數，剝除後立即重送；
# 之後同一模型的所有呼叫直接不送該參數，不需再失敗一次。
# ---------------------------------------------------------------------------
import re
from typing import Set

_LEARNED_UNSUPPORTED: Dict[str, Set[str]] = {}

# 400 訊息中的參數棄用/不支援特徵
_UNSUPPORTED_PATTERNS = ("deprecated", "not supported", "unsupported", "no longer")


def record_unsupported_param(model_id: str, param: str) -> None:
    _LEARNED_UNSUPPORTED.setdefault(model_id, set()).add(param)


def get_learned_unsupported(model_id: str) -> Set[str]:
    return set(_LEARNED_UNSUPPORTED.get(model_id, set()))


def strip_learned_unsupported(model_id: str, params: dict) -> dict:
    """移除已知該模型不支援的參數"""
    learned = _LEARNED_UNSUPPORTED.get(model_id)
    if not learned:
        return params
    return {k: v for k, v in params.items() if k not in learned}


def detect_unsupported_param(error_message: str, sent_params: dict) -> Optional[str]:
    """
    從 400 錯誤訊息判斷是哪個已送出的參數被棄用/不支援。
    例：'`temperature` is deprecated for this model.' -> 'temperature'
    僅在訊息含棄用特徵字眼時判定，避免誤把其他 400（如 schema 錯誤）當參數問題。
    """
    if not error_message:
        return None
    msg_lower = error_message.lower()
    if not any(p in msg_lower for p in _UNSUPPORTED_PATTERNS):
        return None
    # 反引號包住的參數名優先
    for m in re.findall(r"`(\w+)`", error_message):
        if m in sent_params:
            return m
    # 其次：訊息中直接出現的已送出參數名
    for p in sent_params:
        if p in msg_lower:
            return p
    return None


def get_capability(model_id: str) -> ModelCapability:
    return KNOWN_MODEL_CAPABILITIES.get(model_id, _FALLBACK_CAPABILITY)


def sanitize_params(model_id: str, max_tokens: int, temperature: Optional[float],
                     use_extended_thinking: bool) -> dict:
    """依模型能力過濾/裁剪參數，避免送出不支援的參數而失敗"""
    cap = get_capability(model_id)
    params: dict = {
        "max_tokens": min(max_tokens, cap.max_output_tokens) if cap.max_output_tokens else max_tokens,
    }
    if cap.supports_temperature and temperature is not None:
        params["temperature"] = temperature
    if use_extended_thinking and cap.supports_extended_thinking:
        # Anthropic extended thinking 需要額外的 thinking 參數與較大的 max_tokens 預算，
        # 這裡採保守預設值，實務上應可在設定頁調整
        params["thinking"] = {"type": "enabled", "budget_tokens": min(4096, cap.max_output_tokens // 2)}
    return params
