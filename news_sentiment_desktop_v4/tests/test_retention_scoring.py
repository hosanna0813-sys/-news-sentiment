"""測試：留用初判 v3（兩段式：Haiku 粗篩 + Sonnet MOI 評分，皆不含自由文字欄位）"""
from __future__ import annotations

import types

from app.models.news import NewsItem
from app.services.ai.model_gateway import ModelGateway
from app.services.retention.retention_service import (
    judge_batch, prefilter_batch, apply_human_retention_override,
)
from app.workers.retention_worker import build_retention_worker, _build_retention_human_examples
from app.prompts.retention_prompt import (
    TOOL_NAME, TOOL_SCHEMA, SYSTEM_PROMPT, USER_TEMPLATE,
    PREFILTER_TOOL_NAME, PREFILTER_TOOL_SCHEMA, PREFILTER_SYSTEM_PROMPT, PREFILTER_USER_TEMPLATE,
)
from app.repositories.settings_repository import PromptRepository
from app.services.feedback.feedback_service import log_feedback


def _make_item(row_id: str, title: str = "測試新聞") -> NewsItem:
    return NewsItem(row_id=row_id, title=title, source="測試媒體",
                     published_at="2026-07-03", channel="政治")


def _make_gateway(fake_anthropic_module, responses_by_tool: dict, captured_calls: list = None):
    """responses_by_tool: {tool_name: judgements_list}；create() 依請求的 tool 名稱回不同內容，
    模擬 process_batch_fn 內部依序呼叫粗篩(prefilter)與細評(judgement)兩個不同 tool 的情境。
    captured_calls：若提供，每次 create() 呼叫的 kwargs 會被 append 進去，供測試檢查實際送出的
    user content（例如方案D少樣本範例是否真的送進 API 呼叫）。"""
    class FakeContentBlock:
        def __init__(self, type_, input_=None):
            self.type = type_
            self.input = input_

    class FakeResponse:
        def __init__(self, judgements):
            self.content = [FakeContentBlock("tool_use", input_={"judgements": judgements})]
            self.stop_reason = "tool_use"
            self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=10)

    def create(**kwargs):
        if captured_calls is not None:
            captured_calls.append(kwargs)
        tool_name = kwargs["tools"][0]["name"]
        judgements = responses_by_tool.get(tool_name, [])
        return FakeResponse(judgements)

    class FakeClient:
        def __init__(self, api_key, timeout):
            self.messages = types.SimpleNamespace(create=create)

    fake_anthropic_module.Anthropic = FakeClient
    return ModelGateway(
        api_key_provider=lambda: "sk-ant-fake",
        task_model_lookup=lambda task: {"model_id": "claude-sonnet-5", "max_tokens": 4096, "temperature": 0.0},
        request_timeout_sec=5, max_retries=1, retry_backoff_base_sec=0.001,
    )


def _full_judgement(row_id, priority_stars=5, should_respond=True, is_moi_core_business=False):
    return {
        "row_id": row_id,
        "business_relevance": 35, "response_requirement": 18, "political_sensitivity": 12,
        "media_attention": 10, "public_impact": 8, "executive_bonus": 10,
        "final_score": 93, "priority_stars": priority_stars,
        "should_respond": should_respond, "is_moi_core_business": is_moi_core_business,
    }


# ---------- judge_batch（階段二）欄位解析 ----------

def test_judge_batch_maps_all_score_fields(fake_anthropic_module):
    items = [_make_item("r1")]
    gw = _make_gateway(fake_anthropic_module, {TOOL_NAME: [_full_judgement("r1")]})
    out = judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA)
    r = out["r1"]
    assert r["score_final"] == 93
    assert r["priority_stars"] == 5
    assert r["should_respond"] is True
    assert r["is_moi_core_business"] is False
    assert "reason" not in r and "action_reasoning" not in r and "recommended_action" not in r


def test_judge_batch_maps_is_moi_core_business_true(fake_anthropic_module):
    items = [_make_item("r1")]
    gw = _make_gateway(fake_anthropic_module, {
        TOOL_NAME: [_full_judgement("r1", priority_stars=2, should_respond=False, is_moi_core_business=True)],
    })
    out = judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA)
    assert out["r1"]["is_moi_core_business"] is True


def test_judge_batch_skips_malformed_items_and_falls_back(fake_anthropic_module):
    items = [_make_item("r1")]
    gw = _make_gateway(fake_anthropic_module, {TOOL_NAME: ["not a dict", {"row_id": None}]})
    out = judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA)
    assert out["r1"]["priority_stars"] == 1
    assert out["r1"]["should_respond"] is False
    assert out["r1"]["is_moi_core_business"] is False


def test_judge_batch_missing_row_id_falls_back_to_low_priority(fake_anthropic_module):
    items = [_make_item("r1"), _make_item("r2")]
    gw = _make_gateway(fake_anthropic_module, {TOOL_NAME: [_full_judgement("r1")]})  # r2 漏判
    out = judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA)
    assert out["r2"]["priority_stars"] == 1
    assert out["r2"]["should_respond"] is False
    assert out["r2"]["is_moi_core_business"] is False


# ---------- prefilter_batch（階段一）欄位解析 ----------

def test_prefilter_batch_maps_relevance(fake_anthropic_module):
    items = [_make_item("r1"), _make_item("r2")]
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True},
                               {"row_id": "r2", "is_relevant": False}],
    })
    out = prefilter_batch(gw, items, PREFILTER_SYSTEM_PROMPT, PREFILTER_USER_TEMPLATE,
                           PREFILTER_TOOL_NAME, PREFILTER_TOOL_SCHEMA)
    assert out == {"r1": True, "r2": False}


def test_prefilter_batch_missing_row_id_falls_back_to_relevant(fake_anthropic_module):
    """漏判時寧可放行進入階段二，也不要在粗篩就武斷排除"""
    items = [_make_item("r1")]
    gw = _make_gateway(fake_anthropic_module, {PREFILTER_TOOL_NAME: []})
    out = prefilter_batch(gw, items, PREFILTER_SYSTEM_PROMPT, PREFILTER_USER_TEMPLATE,
                           PREFILTER_TOOL_NAME, PREFILTER_TOOL_SCHEMA)
    assert out["r1"] is True


# ---------- worker 兩段式流程（直接呼叫 process_batch_fn，不啟動 QThread） ----------

def test_worker_excludes_at_prefilter_stage_without_calling_stage_two(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    """粗篩判定不相關時，不應該再呼叫細評（用只註冊粗篩回應、細評 tool 沒有對應回應來驗證：
    若細評真的被呼叫，回應會是空清單，導致 fallback 低優先級而非「完全沒有分數」的乾淨排除狀態）"""
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": False}],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    outcome = worker.process_batch_fn(items)
    assert outcome.success
    saved = news_repo.get("r1")
    assert bool(saved.retained) is False
    assert saved.retention_status == "AI建議不留用"
    assert saved.priority_stars == 0  # 粗篩排除，完全沒進入評分階段，分數維持 0（非低優先級的 1）
    assert saved.score_final == 0
    assert saved.is_moi_core_business == 0


def test_worker_relevant_items_proceed_to_stage_two_and_retain_when_above_threshold(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1", priority_stars=4, should_respond=False)],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    outcome = worker.process_batch_fn(items)
    assert outcome.success
    saved = news_repo.get("r1")
    assert bool(saved.retained) is True
    assert saved.retention_status == "留用"
    assert saved.priority_stars == 4
    assert bool(saved.is_moi_core_business) is False


def test_worker_relevant_but_below_threshold_excluded(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1", priority_stars=2, should_respond=False,
                                     is_moi_core_business=False)],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    worker.process_batch_fn(items)
    saved = news_repo.get("r1")
    assert bool(saved.retained) is False
    assert saved.retention_status == "AI建議不留用"


def test_worker_retains_low_priority_if_is_moi_core_business_true(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    """即使優先級未達門檻且不需回應，只要符合 MOI 核心業務旗標，仍應留用（方案A）"""
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1", priority_stars=2, should_respond=False,
                                     is_moi_core_business=True)],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    worker.process_batch_fn(items)
    saved = news_repo.get("r1")
    assert bool(saved.retained) is True
    assert saved.retention_status == "留用"
    assert bool(saved.is_moi_core_business) is True


def test_worker_retains_low_priority_if_should_respond_true(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    """即使優先級未達門檻，只要 AI 判斷內政部應該回應，仍應留用"""
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1", priority_stars=2, should_respond=True)],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    worker.process_batch_fn(items)
    saved = news_repo.get("r1")
    assert bool(saved.retained) is True
    assert saved.retention_status == "留用"


def test_worker_mixed_batch_only_relevant_subset_hits_stage_two(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    items = [_make_item("r1"), _make_item("r2")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True},
                               {"row_id": "r2", "is_relevant": False}],
        TOOL_NAME: [_full_judgement("r1", priority_stars=5, should_respond=True)],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    outcome = worker.process_batch_fn(items)
    assert outcome.success
    r1 = news_repo.get("r1")
    r2 = news_repo.get("r2")
    assert r1.retention_status == "留用"
    assert r1.priority_stars == 5
    assert r2.retention_status == "AI建議不留用"


# ---------- 方案D：少樣本人工修正範例 _build_retention_human_examples() ----------

def test_build_human_examples_empty_when_no_feedback(news_repo, feedback_repo):
    assert _build_retention_human_examples(feedback_repo, news_repo) == ""


def test_build_human_examples_returns_empty_for_none_repo(news_repo):
    assert _build_retention_human_examples(None, news_repo) == ""


def test_build_human_examples_filters_non_human_actions(news_repo, feedback_repo):
    """AI 自己的判斷紀錄（ai_judge）不算修正，不應被當成少樣本範例"""
    items = [_make_item("r1", title="測試新聞A")]
    news_repo.upsert_many(items)
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="", human_final_value="", action="ai_judge", operator="system")
    assert _build_retention_human_examples(feedback_repo, news_repo) == ""


def test_build_human_examples_formats_override_with_title_and_stars(news_repo, feedback_repo):
    items = [_make_item("r1", title="陽明山國家公園步道整修工程")]
    news_repo.upsert_many(items)
    news_repo.update_fields("r1", {"priority_stars": 2})
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="user")
    result = _build_retention_human_examples(feedback_repo, news_repo)
    assert "陽明山國家公園步道整修工程" in result
    assert "★2" in result
    assert "AI 原判 ★2 不留用 → 人工改判留用" in result


def test_build_human_examples_handles_table_override_empty_ai_original_value(news_repo, feedback_repo):
    """表格勾選觸發的 human_override_table，ai_original_value 是空字串，仍應正確顯示成「不留用」"""
    items = [_make_item("r1", title="測試新聞B")]
    news_repo.upsert_many(items)
    news_repo.update_fields("r1", {"priority_stars": 1})
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="", human_final_value="留用",
                 action="human_override_table", operator="user")
    result = _build_retention_human_examples(feedback_repo, news_repo)
    assert "AI 原判 ★1 不留用 → 人工改判留用" in result


def test_build_human_examples_respects_max_examples_cap(news_repo, feedback_repo):
    for i in range(15):
        row_id = f"r{i}"
        news_repo.upsert_many([_make_item(row_id, title=f"新聞{i}")])
        log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id=row_id,
                     ai_original_value="AI建議不留用", human_final_value="留用",
                     action="human_override", operator="user")
    result = _build_retention_human_examples(feedback_repo, news_repo, max_examples=5)
    assert len(result.split("\n")) == 5


def test_build_human_examples_skips_entity_id_not_found_in_news_repo(news_repo, feedback_repo):
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="不存在的row_id",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="user")
    assert _build_retention_human_examples(feedback_repo, news_repo) == ""


# ---------- 方案D：少樣本範例注入 judge_batch() / USER_TEMPLATE ----------

def test_judge_batch_injects_human_examples_into_user_content(fake_anthropic_module):
    items = [_make_item("r1")]
    captured = []
    gw = _make_gateway(fake_anthropic_module, {TOOL_NAME: [_full_judgement("r1")]}, captured_calls=captured)
    example_text = "- 新聞《測試》：AI 原判 ★2 不留用 → 人工改判留用"
    judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA,
                human_examples=example_text)
    user_content = captured[0]["messages"][0]["content"]
    assert example_text in user_content


def test_judge_batch_with_empty_human_examples_produces_clean_template(fake_anthropic_module):
    items = [_make_item("r1")]
    captured = []
    gw = _make_gateway(fake_anthropic_module, {TOOL_NAME: [_full_judgement("r1")]}, captured_calls=captured)
    judge_batch(gw, items, SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA)
    user_content = captured[0]["messages"][0]["content"]
    assert "{human_examples_section}" not in user_content


def test_prefilter_batch_unaffected_by_human_examples_param(fake_anthropic_module):
    """粗篩階段不接受也不需要 human_examples 參數，方案D只作用在細評階段"""
    import inspect
    sig = inspect.signature(prefilter_batch)
    assert "human_examples" not in sig.parameters


# ---------- build_retention_worker() 串接 feedback_repo ----------

def test_build_retention_worker_without_feedback_repo_still_works(
        fake_anthropic_module, news_repo, job_repo, batch_repo, tmp_db_path):
    """feedback_repo 預設 None，向下相容既有呼叫端不受影響"""
    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1")],
    })
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path)
    outcome = worker.process_batch_fn(items)
    assert outcome.success


def test_build_retention_worker_threads_human_examples_into_api_call(
        fake_anthropic_module, news_repo, job_repo, batch_repo, feedback_repo, tmp_db_path):
    """既有人工修正紀錄應被組成少樣本範例，並實際送進細評的 API 呼叫"""
    prior_item = _make_item("r_prior", title="陽明山國家公園步道整修工程")
    news_repo.upsert_many([prior_item])
    news_repo.update_fields("r_prior", {"priority_stars": 2})
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r_prior",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="user")

    items = [_make_item("r1")]
    news_repo.upsert_many(items)
    captured = []
    gw = _make_gateway(fake_anthropic_module, {
        PREFILTER_TOOL_NAME: [{"row_id": "r1", "is_relevant": True}],
        TOOL_NAME: [_full_judgement("r1")],
    }, captured_calls=captured)
    worker = build_retention_worker(
        items, 10, gw, PromptRepository(tmp_db_path), job_repo, batch_repo,
        priority_threshold=3, db_path=tmp_db_path, feedback_repo=feedback_repo)
    outcome = worker.process_batch_fn(items)
    assert outcome.success
    judge_call = next(c for c in captured if c["tools"][0]["name"] == TOOL_NAME)
    user_content = judge_call["messages"][0]["content"]
    assert "陽明山國家公園步道整修工程" in user_content
    assert "★2" in user_content


# ---------- apply_human_retention_override() ----------
# 這條規則原本寫死在 app/ui/pages/retention_page.py 的兩個 Qt slot 裡，沒有
# QApplication 就無法單獨測試；抽成 retention_service 的純函式後，這裡直接
# 驗證狀態轉換與 feedback log，不需要啟動任何 Qt 元件。

def test_apply_human_retention_override_marks_retained(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1"))
    new_status = apply_human_retention_override(
        news_repo, feedback_repo, "r1", True, old_status="AI建議不留用", action="human_override")

    assert new_status == "留用"
    item = news_repo.get("r1")
    assert item.retained is True or item.retained == 1
    assert item.retention_status == "留用"
    assert item.retention_judged_by == "human"

    entries = feedback_repo.list_all(entity_type="retention")
    assert len(entries) == 1
    assert entries[0].action == "human_override"
    assert entries[0].ai_original_value == "AI建議不留用"
    assert entries[0].human_final_value == "留用"


def test_apply_human_retention_override_marks_not_retained(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1"))
    new_status = apply_human_retention_override(
        news_repo, feedback_repo, "r1", False, old_status="留用", action="human_override_table")

    assert new_status == "人工不留用"
    item = news_repo.get("r1")
    assert not item.retained
    assert item.retention_status == "人工不留用"
    assert item.retention_judged_by == "human"

    entries = feedback_repo.list_all(entity_type="retention")
    assert entries[0].action == "human_override_table"
    assert entries[0].human_final_value == "人工不留用"


def test_apply_human_retention_override_threads_reason_snapshot(news_repo, feedback_repo):
    """網頁版靠 reason 存標題快照，讓「清除資料」把 news 清空後 few-shot 範例仍找得到標題"""
    news_repo.upsert_one(_make_item("r1", title="測試標題快照"))
    apply_human_retention_override(
        news_repo, feedback_repo, "r1", True, old_status="待確認",
        action="human_override", operator="web", reason="測試標題快照")

    entry = feedback_repo.list_all(entity_type="retention")[0]
    assert entry.operator == "web"
    assert entry.reason == "測試標題快照"


# ---------- 桌面版／網頁版共用同一份 build_human_examples() ----------
# 兩邊原本各自重寫一份幾乎相同的邏輯，已經出現分岔（網頁版有標題快照 fallback、
# 桌面版有星等顯示）。收斂成 retention_service.build_human_examples() 之後，
# 兩邊薄轉接函式對同一份資料應該產生完全相同的輸出，且都同時具備兩個特性。

def test_desktop_and_web_wrappers_produce_identical_output(news_repo, feedback_repo):
    from app.workers.retention_worker import _build_retention_human_examples
    from app.web.routes.retention import _build_human_examples as web_build_human_examples

    news_repo.upsert_one(_make_item("r1", title="測試新聞一致性"))
    news_repo.update_fields("r1", {"priority_stars": 3})
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="user")

    desktop_result = _build_retention_human_examples(feedback_repo, news_repo)
    web_result = web_build_human_examples(feedback_repo, news_repo)
    assert desktop_result == web_result
    assert "★3" in desktop_result  # 星等（原本只有桌面版才有）


def test_web_wrapper_gains_star_rating_after_consolidation(news_repo, feedback_repo):
    from app.web.routes.retention import _build_human_examples as web_build_human_examples

    news_repo.upsert_one(_make_item("r1", title="網頁版也該顯示星等"))
    news_repo.update_fields("r1", {"priority_stars": 4})
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="web", reason="網頁版也該顯示星等")

    result = web_build_human_examples(feedback_repo, news_repo)
    assert "★4" in result


def test_desktop_wrapper_gains_title_snapshot_fallback_after_consolidation(news_repo, feedback_repo):
    """news 列已被刪除（比照「清除資料」情境），桌面版現在也能靠 reason 快照
    組出範例，不再因為 news_repo.get() 找不到就整筆跳過。"""
    from app.workers.retention_worker import _build_retention_human_examples

    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="deleted_row",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="web", reason="已被清除資料的新聞標題")

    result = _build_retention_human_examples(feedback_repo, news_repo)
    assert "已被清除資料的新聞標題" in result
