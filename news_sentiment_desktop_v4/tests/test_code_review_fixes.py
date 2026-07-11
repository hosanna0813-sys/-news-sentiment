"""測試：2026-07 程式碼健檢批次修復

涵蓋：
    1. OpenAI 例外分類——錨定比對，不再把訊息裡恰好出現的數字當 HTTP 狀態碼
    2. OpenAI 參數自癒快取為模組層級（gateway 重建不失憶）
    3. ModelGateway 反向模型對應（gpt-* 設定殘留時落回預設 Claude 模型）
    4. resolve_model_id 公開介面（兩個 gateway 都有）
    5. restore_default 取「最新」預設版本、還原後仍標記 is_default
    6. seed_defaults 的 schema-only 升級（prompt 文字沒變、只有 tool schema 變）
    7. 分群 tool schema 含 topic_id（增量分群沿用既有議題的必要欄位）
    8. taxonomy 共用模組（build/prepend）
    9. 桌面版 worker 已接上 keyword_taxonomy 參數
"""
from __future__ import annotations

import inspect
import json

import pytest

from app.services.ai.model_gateway import ModelGateway, GatewayErrorType
from app.services.ai.openai_gateway import OpenAIGateway, _classify_openai_exception
from app.services.taxonomy import build_keyword_context, prepend_keyword_context


# ---------------------------------------------------------------------------
# 1. OpenAI 例外分類
# ---------------------------------------------------------------------------
def test_classifier_does_not_match_digits_inside_larger_numbers():
    # 舊寫法 `"400" in msg` 會把 14000 誤判成 400 → invalid_request（不重試）
    assert _classify_openai_exception(
        Exception("max_tokens is too large: 14000")) == GatewayErrorType.OTHER
    # 5290 不是 529
    assert _classify_openai_exception(
        Exception("request id 5290 failed")) == GatewayErrorType.OTHER


def test_classifier_prefers_exception_type_name():
    class RateLimitError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    assert _classify_openai_exception(RateLimitError("boom")) == GatewayErrorType.RATE_LIMIT
    assert _classify_openai_exception(AuthenticationError("boom")) == GatewayErrorType.AUTH
    assert _classify_openai_exception(APITimeoutError("boom")) == GatewayErrorType.TIMEOUT


def test_classifier_uses_status_code_attribute():
    class APIStatusError(Exception):
        def __init__(self, msg, status_code):
            super().__init__(msg)
            self.status_code = status_code

    assert _classify_openai_exception(APIStatusError("x", 429)) == GatewayErrorType.RATE_LIMIT
    assert _classify_openai_exception(APIStatusError("x", 401)) == GatewayErrorType.AUTH
    assert _classify_openai_exception(APIStatusError("x", 503)) == GatewayErrorType.OVERLOADED
    assert _classify_openai_exception(APIStatusError("x", 422)) == GatewayErrorType.INVALID_REQUEST


def test_classifier_anchored_http_codes_in_message():
    assert _classify_openai_exception(Exception("Error code: 429")) == GatewayErrorType.RATE_LIMIT
    assert _classify_openai_exception(Exception("got 502 from upstream")) == GatewayErrorType.OVERLOADED
    assert _classify_openai_exception(Exception("404 model not found")) == GatewayErrorType.INVALID_REQUEST


# ---------------------------------------------------------------------------
# 2. 參數自癒快取為模組層級
# ---------------------------------------------------------------------------
def test_capability_cache_survives_gateway_rebuild():
    from app.services.ai import openai_gateway as og
    og._TOKEN_PARAM["gpt-test-model"] = "max_tokens"
    try:
        gw = OpenAIGateway(api_key_provider=lambda: "sk-x",
                            task_model_lookup=lambda t: {"model_id": "gpt-test-model"})
        # 新建的 gateway 直接看得到先前學到的能力快取
        assert gw._token_param["gpt-test-model"] == "max_tokens"
    finally:
        og._TOKEN_PARAM.pop("gpt-test-model", None)


# ---------------------------------------------------------------------------
# 3/4. 模型對應
# ---------------------------------------------------------------------------
def test_model_gateway_falls_back_when_task_model_is_not_claude():
    gw = ModelGateway(api_key_provider=lambda: "k",
                       task_model_lookup=lambda t: {"model_id": "gpt-5.5"},
                       default_model="claude-sonnet-5")
    assert gw.resolve_model_id("retention_judgement") == "claude-sonnet-5"


def test_model_gateway_keeps_claude_model_id():
    gw = ModelGateway(api_key_provider=lambda: "k",
                       task_model_lookup=lambda t: {"model_id": "claude-haiku-4-5"},
                       default_model="claude-sonnet-5")
    assert gw.resolve_model_id("retention_prefilter") == "claude-haiku-4-5"


def test_openai_gateway_resolve_model_id_maps_claude_to_default():
    gw = OpenAIGateway(api_key_provider=lambda: "sk-x",
                        task_model_lookup=lambda t: {"model_id": "claude-sonnet-5"},
                        default_model="gpt-5.5")
    assert gw.resolve_model_id("topic_summarization") == "gpt-5.5"
    gw2 = OpenAIGateway(api_key_provider=lambda: "sk-x",
                         task_model_lookup=lambda t: {"model_id": "gpt-5.5-mini"},
                         default_model="gpt-5.5")
    assert gw2.resolve_model_id("topic_summarization") == "gpt-5.5-mini"


# ---------------------------------------------------------------------------
# 5. restore_default 取最新預設版本
# ---------------------------------------------------------------------------
def test_restore_default_uses_latest_default_version(tmp_db_path):
    from app.repositories.settings_repository import PromptRepository
    from app.models.prompt_config import PromptConfig
    repo = PromptRepository(tmp_db_path)
    repo.ensure_seeded("t1", PromptConfig(task="t1", system_prompt="預設v1",
                                            user_template="u", tool_schema_json="{}"))
    # 使用者自行修改（非預設）
    repo.save_new_version(PromptConfig(task="t1", system_prompt="使用者改過",
                                         user_template="u", tool_schema_json="{}"))
    # 程式內建預設升級（seed_defaults 會寫入新的 is_default 版本）
    repo.save_new_version(PromptConfig(task="t1", system_prompt="預設v3（升級後）",
                                         user_template="u", tool_schema_json="{}", is_default=True))
    # 使用者又再改一版
    repo.save_new_version(PromptConfig(task="t1", system_prompt="使用者又改",
                                         user_template="u", tool_schema_json="{}"))

    restored = repo.restore_default("t1")
    assert restored is not None
    assert restored.system_prompt == "預設v3（升級後）"   # 不是最舊的 v1
    assert restored.is_default                             # 還原後保留預設標記
    active = repo.get_active("t1")
    assert active.system_prompt == "預設v3（升級後）"


# ---------------------------------------------------------------------------
# 6. schema-only 升級
# ---------------------------------------------------------------------------
def test_seed_defaults_upgrades_schema_only_change(tmp_db_path):
    from app.repositories.settings_repository import PromptRepository
    from app.prompts.registry import seed_defaults, _DEFAULTS
    repo = PromptRepository(tmp_db_path)
    seed_defaults(repo)

    # 模擬舊資料庫：prompt 文字與現行預設相同，但 tool schema 是舊版（少欄位）
    old_schema = json.dumps({"name": "submit_topic_clusters",
                              "schema": {"type": "object", "properties": {}}}, ensure_ascii=False)
    active = repo.get_active("topic_clustering")
    repo.conn.execute("UPDATE prompts SET tool_schema_json=? WHERE task=? AND version=?",
                       (old_schema, "topic_clustering", active.version))

    seed_defaults(repo)   # 再跑一次啟動流程

    upgraded = repo.get_active("topic_clustering")
    expected = json.dumps(_DEFAULTS["topic_clustering"]["tool_schema"], ensure_ascii=False)
    assert upgraded.tool_schema_json == expected
    assert upgraded.is_default


# ---------------------------------------------------------------------------
# 7. 分群 schema 含 topic_id
# ---------------------------------------------------------------------------
def test_clustering_tool_schema_includes_topic_id():
    from app.prompts.clustering_prompt import CLUSTERING_TOOL_SCHEMA
    topic_props = CLUSTERING_TOOL_SCHEMA["properties"]["topics"]["items"]["properties"]
    assert "topic_id" in topic_props   # 增量分群要求模型「沿用既有 topic_id」的必要欄位


# ---------------------------------------------------------------------------
# 8. taxonomy 共用模組
# ---------------------------------------------------------------------------
def test_build_keyword_context_empty_and_nonempty():
    assert build_keyword_context("") == ""
    assert build_keyword_context("   ") == ""
    out = build_keyword_context("內政議題　內政部|戶役政")
    assert "業務關注議題與關鍵字對照表" in out
    assert "內政部|戶役政" in out


def test_prepend_keyword_context_combinations():
    assert prepend_keyword_context("", "範例A") == "範例A"
    only_ctx = prepend_keyword_context("關鍵字表", "")
    assert only_ctx.startswith("【業務關注議題與關鍵字對照表】")
    both = prepend_keyword_context("關鍵字表", "範例A")
    assert both.index("關鍵字表") < both.index("範例A")


# ---------------------------------------------------------------------------
# 9. 桌面版 worker 已接上 keyword_taxonomy
# ---------------------------------------------------------------------------
def test_retention_worker_accepts_keyword_taxonomy():
    from app.workers.retention_worker import build_retention_worker
    assert "keyword_taxonomy" in inspect.signature(build_retention_worker).parameters


def test_clustering_worker_accepts_keyword_taxonomy():
    pytest.importorskip("PySide6")
    from app.workers.clustering_worker import ClusteringWorker
    assert "keyword_taxonomy" in inspect.signature(ClusteringWorker.__init__).parameters
