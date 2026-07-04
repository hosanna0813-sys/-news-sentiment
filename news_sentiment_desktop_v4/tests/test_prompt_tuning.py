"""測試：Prompt 調校建議流程（提案 + 驗證），比照 test_retention_scoring.py 的 fake gateway 慣例"""
from __future__ import annotations

import json
import types

import pytest

from app.models.news import NewsItem
from app.models.prompt_tuning import PromptTuningDraft
from app.services.ai.model_gateway import ModelGateway
from app.services.retention.retention_service import decide_retain
from app.services.prompt_tuning.propose_service import (
    generate_prompt_tuning_proposal, count_new_corrections_since, build_correction_payload,
    TooFewCorrectionsError, ProposalRejectedError, MAX_PROPOSE_CORRECTIONS,
)
from app.services.prompt_tuning.validate_service import (
    compute_validation_metrics, estimate_validation_cost,
)
from app.services.feedback.feedback_service import log_feedback
from app.repositories.settings_repository import PromptRepository
from app.workers.prompt_tuning_validate_worker import build_prompt_tuning_validate_worker
from app.prompts.retention_prompt import TOOL_NAME as JUDGE_TOOL_NAME
from app.prompts.prompt_tuning_prompt import PROPOSE_TOOL_NAME


def _make_item(row_id: str, title: str = "測試新聞", priority_stars: int = 0,
               retained: bool = True) -> NewsItem:
    return NewsItem(row_id=row_id, title=title, source="測試媒體", published_at="2026-07-03",
                     channel="政治", priority_stars=priority_stars, retained=retained)


def _make_multi_gateway(fake_anthropic_module, response_by_tool_name: dict, captured_calls: list = None):
    """response_by_tool_name: {tool_name: <tool_block.input 內容>}——直接回傳模型工具輸入的原始 dict，
    不像 test_retention_scoring.py 的 _make_gateway 固定包成 {"judgements": [...]}，因為提案工具
    (submit_prompt_tuning_proposal) 回傳的是單一物件，不是清單。"""
    class FakeContentBlock:
        def __init__(self, type_, input_=None):
            self.type = type_
            self.input = input_

    class FakeResponse:
        def __init__(self, content_input):
            self.content = [FakeContentBlock("tool_use", input_=content_input)]
            self.stop_reason = "tool_use"
            self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=10)

    def create(**kwargs):
        if captured_calls is not None:
            captured_calls.append(kwargs)
        tool_name = kwargs["tools"][0]["name"]
        return FakeResponse(response_by_tool_name.get(tool_name, {}))

    class FakeClient:
        def __init__(self, api_key, timeout):
            self.messages = types.SimpleNamespace(create=create)

    fake_anthropic_module.Anthropic = FakeClient
    return ModelGateway(
        api_key_provider=lambda: "sk-ant-fake",
        task_model_lookup=lambda task: {"model_id": "claude-sonnet-5", "max_tokens": 4096, "temperature": 0.0},
        request_timeout_sec=5, max_retries=1, retry_backoff_base_sec=0.001,
    )


def _valid_proposal(system_prompt="改良後的 SYSTEM_PROMPT，內含 row_id, business_relevance, "
                                   "response_requirement, political_sensitivity, media_attention, "
                                   "public_impact, executive_bonus, final_score, priority_stars, "
                                   "should_respond, is_moi_core_business",
                     user_template="請判斷。{human_examples_section}\n{news_batch_json}"):
    return {
        "proposed_system_prompt": system_prompt,
        "proposed_user_template": user_template,
        "rationale": "觀察到黃牛查緝案被低估，強化機關查緝成果條件的措辭。",
    }


# ---------- PromptTuningRepository ----------

def test_prompt_tuning_repository_upsert_and_get_roundtrip(tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    repo = PromptTuningRepository(tmp_db_path)
    draft = PromptTuningDraft(draft_id="pt_1", based_on_version=1,
                               proposed_system_prompt="A", proposed_user_template="B",
                               rationale="C", correction_count_used=5)
    repo.upsert(draft)
    got = repo.get("pt_1")
    assert got.proposed_system_prompt == "A"
    assert got.status == "待驗證"
    assert got.correction_count_used == 5


def test_prompt_tuning_repository_update_status(tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    repo = PromptTuningRepository(tmp_db_path)
    repo.upsert(PromptTuningDraft(draft_id="pt_1", based_on_version=1))
    repo.update_status("pt_1", "已拒絕")
    assert repo.get("pt_1").status == "已拒絕"


def test_prompt_tuning_repository_update_validation_result(tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    repo = PromptTuningRepository(tmp_db_path)
    repo.upsert(PromptTuningDraft(draft_id="pt_1", based_on_version=1))
    repo.update_validation_result("pt_1", "已驗證", json.dumps({"recovery_rate": 0.8}))
    got = repo.get("pt_1")
    assert got.status == "已驗證"
    assert json.loads(got.validation_metrics_json)["recovery_rate"] == 0.8


def test_prompt_tuning_repository_list_all_filters_by_status(tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    repo = PromptTuningRepository(tmp_db_path)
    repo.upsert(PromptTuningDraft(draft_id="pt_1", based_on_version=1, status="待驗證"))
    repo.upsert(PromptTuningDraft(draft_id="pt_2", based_on_version=1, status="已套用"))
    assert [d.draft_id for d in repo.list_all(status="已套用")] == ["pt_2"]
    assert len(repo.list_all()) == 2


def test_prompt_tuning_repository_latest_created_at_for_task_used_by_guard(tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    repo = PromptTuningRepository(tmp_db_path)
    assert repo.latest_created_at_for_task("retention_judgement") == 0.0
    repo.upsert(PromptTuningDraft(draft_id="pt_1", task="retention_judgement", based_on_version=1))
    assert repo.latest_created_at_for_task("retention_judgement") > 0.0


# ---------- propose_service ----------

def test_count_new_corrections_since_filters_correctly(feedback_repo, news_repo):
    news_repo.upsert_many([_make_item("r1")])
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="AI建議不留用", human_final_value="留用",
                 action="human_override", operator="user")
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id="r1",
                 ai_original_value="", human_final_value="", action="ai_judge", operator="system")
    assert count_new_corrections_since(feedback_repo, 0.0) == 1


def test_build_correction_payload_caps_at_max_propose_corrections(feedback_repo, news_repo):
    for i in range(40):
        row_id = f"r{i}"
        news_repo.upsert_many([_make_item(row_id, title=f"新聞{i}")])
        log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id=row_id,
                     ai_original_value="AI建議不留用", human_final_value="留用",
                     action="human_override", operator="user")
    payload = build_correction_payload(feedback_repo, news_repo)
    assert len(payload) == MAX_PROPOSE_CORRECTIONS


def _seed_min_corrections(feedback_repo, news_repo, count=5):
    for i in range(count):
        row_id = f"r{i}"
        news_repo.upsert_many([_make_item(row_id, title=f"新聞{i}")])
        log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id=row_id,
                     ai_original_value="AI建議不留用", human_final_value="留用",
                     action="human_override", operator="user")


def test_generate_proposal_raises_too_few_corrections_without_calling_api(
        fake_anthropic_module, feedback_repo, news_repo, tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    tuning_repo = PromptTuningRepository(tmp_db_path)
    captured = []
    gw = _make_multi_gateway(fake_anthropic_module, {}, captured_calls=captured)
    prompt_repo = PromptRepository(tmp_db_path)
    with pytest.raises(TooFewCorrectionsError):
        generate_prompt_tuning_proposal(gw, prompt_repo, feedback_repo, news_repo, tuning_repo)
    assert captured == []


def test_generate_proposal_succeeds_and_persists_draft(
        fake_anthropic_module, feedback_repo, news_repo, tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    tuning_repo = PromptTuningRepository(tmp_db_path)
    prompt_repo = PromptRepository(tmp_db_path)
    _seed_min_corrections(feedback_repo, news_repo)
    gw = _make_multi_gateway(fake_anthropic_module, {PROPOSE_TOOL_NAME: _valid_proposal()})
    draft = generate_prompt_tuning_proposal(gw, prompt_repo, feedback_repo, news_repo, tuning_repo)
    assert draft.status == "待驗證"
    assert draft.based_on_version == 1  # 未 seed 過，退回內建預設版本號 1
    assert draft.correction_count_used == 5
    assert tuning_repo.get(draft.draft_id) is not None


def test_generate_proposal_rejects_response_missing_placeholder(
        fake_anthropic_module, feedback_repo, news_repo, tmp_db_path):
    from app.repositories.prompt_tuning_repository import PromptTuningRepository
    tuning_repo = PromptTuningRepository(tmp_db_path)
    prompt_repo = PromptRepository(tmp_db_path)
    _seed_min_corrections(feedback_repo, news_repo)
    bad_proposal = _valid_proposal(user_template="請判斷。{news_batch_json}")  # 漏了 human_examples_section
    gw = _make_multi_gateway(fake_anthropic_module, {PROPOSE_TOOL_NAME: bad_proposal})
    with pytest.raises(ProposalRejectedError):
        generate_prompt_tuning_proposal(gw, prompt_repo, feedback_repo, news_repo, tuning_repo)
    assert tuning_repo.list_all() == []


# ---------- news_repository 新查詢方法 ----------

def test_list_human_corrected_since_filters_by_timestamp_and_judged_by(news_repo):
    # update_fields() 一律把 updated_at 蓋成當下時間，所以要測「時間戳過濾」必須在 upsert_many
    # 時就把 updated_at 設定在 dataclass 實例上（upsert_many 直接寫入 to_dict() 的值，不強制覆蓋）。
    old_item = _make_item("r_old")
    old_item.retention_judged_by = "human"
    old_item.updated_at = 100.0
    news_repo.upsert_many([old_item])

    new_item = _make_item("r_new")
    new_item.retention_judged_by = "human"
    new_item.updated_at = 999999999.0
    news_repo.upsert_many([new_item])

    ai_item = _make_item("r_ai")
    ai_item.retention_judged_by = "ai"
    ai_item.updated_at = 999999999.0
    news_repo.upsert_many([ai_item])

    result = news_repo.list_human_corrected_since(500.0, limit=10)
    ids = {it.row_id for it in result}
    assert ids == {"r_new"}


def test_list_human_corrected_since_respects_limit(news_repo):
    for i in range(5):
        it = _make_item(f"r{i}")
        it.retention_judged_by = "human"
        news_repo.upsert_many([it])
    result = news_repo.list_human_corrected_since(0.0, limit=2)
    assert len(result) == 2


def test_list_boundary_control_sample_matches_criteria_and_excludes_human(news_repo):
    boundary = _make_item("r_boundary", priority_stars=2)
    boundary.should_respond = False
    boundary.retention_judged_by = "ai"
    news_repo.upsert_many([boundary])

    human_boundary = _make_item("r_human", priority_stars=2)
    human_boundary.should_respond = False
    human_boundary.retention_judged_by = "human"
    news_repo.upsert_many([human_boundary])

    high_priority = _make_item("r_high", priority_stars=4)
    high_priority.retention_judged_by = "ai"
    news_repo.upsert_many([high_priority])

    result = news_repo.list_boundary_control_sample(limit=10)
    ids = {it.row_id for it in result}
    assert ids == {"r_boundary"}


def test_list_boundary_control_sample_respects_limit(news_repo):
    for i in range(5):
        it = _make_item(f"r{i}", priority_stars=2)
        it.should_respond = False
        it.retention_judged_by = "ai"
        news_repo.upsert_many([it])
    result = news_repo.list_boundary_control_sample(limit=3)
    assert len(result) == 3


# ---------- validate_service ----------

def _judgement(priority_stars, should_respond, is_moi_core_business):
    return {"priority_stars": priority_stars, "should_respond": should_respond,
            "is_moi_core_business": is_moi_core_business}


def test_compute_metrics_recovery_and_false_positive():
    correction_items = [_make_item("r1", retained=True)]     # human 最終決定：留用
    control_items = [_make_item("r2", retained=False)]        # 目前正確排除

    current_results = {
        "r1": _judgement(2, False, False),   # 目前 prompt 判不留用（跟 human 不符，判錯）
        "r2": _judgement(2, False, False),   # 目前 prompt 正確排除
    }
    proposed_results = {
        "r1": _judgement(2, False, True),    # 建議 prompt 判留用（跟 human 相符，復原）
        "r2": _judgement(2, False, True),    # 建議 prompt 誤判留用（新產生的誤判）
    }
    metrics = compute_validation_metrics(
        correction_items, control_items, current_results, proposed_results,
        retain_fn=decide_retain, priority_threshold=3, estimated_cost_usd=1.23,
    )
    assert metrics.recovery_count == 1
    assert metrics.recovery_rate == 1.0
    assert metrics.false_positive_count == 1
    assert metrics.false_positive_rate == 1.0
    assert metrics.estimated_cost_usd == 1.23


def test_compute_metrics_zero_division_safe_with_empty_samples():
    metrics = compute_validation_metrics([], [], {}, {}, retain_fn=decide_retain, priority_threshold=3)
    assert metrics.recovery_rate == 0.0
    assert metrics.false_positive_rate == 0.0


def test_compute_metrics_no_change_yields_zero_recovery_and_false_positive():
    correction_items = [_make_item("r1", retained=False)]
    control_items = [_make_item("r2", retained=False)]
    same_results = {"r1": _judgement(1, False, False), "r2": _judgement(1, False, False)}
    metrics = compute_validation_metrics(
        correction_items, control_items, same_results, same_results,
        retain_fn=decide_retain, priority_threshold=3,
    )
    assert metrics.recovery_count == 0
    assert metrics.false_positive_count == 0


def test_estimate_validation_cost_scales_with_two_passes_and_sample_size():
    cost_136 = estimate_validation_cost(60, 76)
    assert cost_136 == pytest.approx(0.31 * 2, abs=0.01)
    assert estimate_validation_cost(0, 0) == 0.0


# ---------- decide_retain 共用判斷公式 ----------

def test_decide_retain_true_branches():
    assert decide_retain(_judgement(3, False, False), priority_threshold=3) is True
    assert decide_retain(_judgement(1, True, False), priority_threshold=3) is True
    assert decide_retain(_judgement(1, False, True), priority_threshold=3) is True


def test_decide_retain_false_when_all_conditions_fail():
    assert decide_retain(_judgement(2, False, False), priority_threshold=3) is False


# ---------- prompt_tuning_validate_worker：兩次呼叫、唯讀評估 ----------

def test_validate_worker_calls_judge_batch_twice_per_batch_with_different_prompts(
        fake_anthropic_module, news_repo, feedback_repo, job_repo, batch_repo, tmp_db_path):
    from app.models.prompt_tuning import PromptTuningDraft
    prompt_repo = PromptRepository(tmp_db_path)
    item = _make_item("r1", priority_stars=2)
    item.retention_judged_by = "human"
    news_repo.upsert_many([item])

    captured = []
    gw = _make_multi_gateway(fake_anthropic_module, {
        JUDGE_TOOL_NAME: {"judgements": [
            {"row_id": "r1", "business_relevance": 10, "response_requirement": 5,
             "political_sensitivity": 5, "media_attention": 5, "public_impact": 5,
             "executive_bonus": 0, "final_score": 30, "priority_stars": 2,
             "should_respond": False, "is_moi_core_business": False},
        ]},
    }, captured_calls=captured)

    draft = PromptTuningDraft(draft_id="pt_1", based_on_version=1,
                               proposed_system_prompt="提案版 SYSTEM_PROMPT",
                               proposed_user_template="提案版 {human_examples_section}\n{news_batch_json}")

    worker = build_prompt_tuning_validate_worker(
        draft, [item], [], gw, prompt_repo, feedback_repo, news_repo, job_repo, batch_repo)
    outcome = worker.process_batch_fn([item])
    assert outcome.success
    assert len(captured) == 2
    system_prompts_used = {c["system"] for c in captured}
    assert "提案版 SYSTEM_PROMPT" in system_prompts_used
    assert len(system_prompts_used) == 2  # 兩次呼叫用的 system prompt 確實不同


def test_validate_worker_does_not_write_to_news_repository(
        fake_anthropic_module, news_repo, feedback_repo, job_repo, batch_repo, tmp_db_path):
    from app.models.prompt_tuning import PromptTuningDraft
    prompt_repo = PromptRepository(tmp_db_path)
    item = _make_item("r1", priority_stars=2)
    news_repo.upsert_many([item])

    gw = _make_multi_gateway(fake_anthropic_module, {
        JUDGE_TOOL_NAME: {"judgements": [
            {"row_id": "r1", "business_relevance": 40, "response_requirement": 20,
             "political_sensitivity": 15, "media_attention": 15, "public_impact": 10,
             "executive_bonus": 20, "final_score": 120, "priority_stars": 5,
             "should_respond": True, "is_moi_core_business": True},
        ]},
    })
    draft = PromptTuningDraft(draft_id="pt_1", based_on_version=1,
                               proposed_system_prompt="提案版",
                               proposed_user_template="{human_examples_section}{news_batch_json}")
    worker = build_prompt_tuning_validate_worker(
        draft, [item], [], gw, prompt_repo, feedback_repo, news_repo, job_repo, batch_repo)
    worker.process_batch_fn([item])

    saved = news_repo.get("r1")
    assert saved.priority_stars == 2   # 驗證流程唯讀，不寫回正式分數
    assert saved.retained == item.retained


def test_validate_worker_batch_outcome_failure_on_gateway_error(
        fake_anthropic_module, news_repo, feedback_repo, job_repo, batch_repo, tmp_db_path):
    from app.models.prompt_tuning import PromptTuningDraft
    prompt_repo = PromptRepository(tmp_db_path)
    item = _make_item("r1")
    news_repo.upsert_many([item])
    gw = _make_multi_gateway(fake_anthropic_module, {})  # 空回應 -> judge_batch 會 fallback，不算失敗
    draft = PromptTuningDraft(draft_id="pt_1", based_on_version=1,
                               proposed_system_prompt="提案版",
                               proposed_user_template="{human_examples_section}{news_batch_json}")

    def _raise_gateway_error(*a, **kw):
        from app.services.ai.model_gateway import GatewayError
        raise GatewayError("other", "模擬 API 失敗")
    gw.call_with_tool = _raise_gateway_error

    worker = build_prompt_tuning_validate_worker(
        draft, [item], [], gw, prompt_repo, feedback_repo, news_repo, job_repo, batch_repo)
    outcome = worker.process_batch_fn([item])
    assert outcome.success is False
