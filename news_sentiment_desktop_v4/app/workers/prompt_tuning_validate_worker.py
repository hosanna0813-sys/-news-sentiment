"""PromptTuningValidateWorker — 對應 Prompt 調校建議第二步：自動驗證提案

沿用 BatchJobWorker，但每個外層批次對同一批新聞呼叫「兩次」judge_batch()——一次用目前
使用中的 retention_judgement prompt，一次用提案的 proposed_system_prompt/proposed_user_template
（tool schema 共用不變，因為提案不可更動結構化輸出欄位）。兩次呼叫使用同一份凍結的少樣本
範例文字，確保公平比較。

與正式留用判斷 worker（retention_worker.py）最大的不同：這是唯讀評估，process() 只把結果
累積進記憶體字典，絕不寫回 NewsRepository（不可影響正式的 news 資料表分數/留用狀態）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.models.news import NewsItem
from app.models.prompt_tuning import PromptTuningDraft
from app.repositories.news_repository import NewsRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.repositories.settings_repository import PromptRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.retention.retention_service import judge_batch
from app.prompts.registry import get_active_prompt
from app.workers.retention_worker import _build_retention_human_examples
from app.workers.batch_job_worker import BatchJobWorker, BatchOutcome
from app.services.prompt_tuning.validate_service import MAX_CORRECTION_SAMPLE, MAX_CONTROL_SAMPLE


def select_validation_samples(draft: PromptTuningDraft, prompt_repo: PromptRepository,
                               news_repo: NewsRepository):
    """回傳 (correction_items, control_items)，供成本估算對話框與實際驗證共用同一份抽樣，
    避免兩次各自查詢時資料剛好變動而抽到不同樣本。"""
    versions = prompt_repo.list_versions("retention_judgement")
    based_on = next((v for v in versions if v.version == draft.based_on_version), None)
    since_ts = based_on.last_modified_at if based_on else 0.0
    correction_items = news_repo.list_human_corrected_since(since_ts, MAX_CORRECTION_SAMPLE)
    control_items = news_repo.list_boundary_control_sample(MAX_CONTROL_SAMPLE)
    return correction_items, control_items


def build_prompt_tuning_validate_worker(
        draft: PromptTuningDraft, correction_items: List[NewsItem], control_items: List[NewsItem],
        gateway: ModelGateway, prompt_repo: PromptRepository, feedback_repo: FeedbackRepository,
        news_repo: NewsRepository, job_repo: JobRepository, batch_repo: BatchRepository,
        batch_size: int = 10, resume_job_id=None) -> BatchJobWorker:
    current_cfg = get_active_prompt(prompt_repo, "retention_judgement")
    judge_schema_obj = json.loads(current_cfg.tool_schema_json)   # tool schema 兩趟共用，提案不可更動

    all_items = list(correction_items) + list(control_items)
    human_examples = _build_retention_human_examples(feedback_repo, news_repo)  # 凍結文字，兩趟共用

    current_results: Dict[str, Dict[str, Any]] = {}
    proposed_results: Dict[str, Dict[str, Any]] = {}

    batches = [all_items[i:i + batch_size] for i in range(0, len(all_items), batch_size)]

    def process(batch_items: List[NewsItem]) -> BatchOutcome:
        try:
            cur_r = judge_batch(
                gateway, batch_items, current_cfg.system_prompt, current_cfg.user_template,
                judge_schema_obj["name"], judge_schema_obj["schema"], human_examples=human_examples,
            )
            prop_r = judge_batch(
                gateway, batch_items, draft.proposed_system_prompt, draft.proposed_user_template,
                judge_schema_obj["name"], judge_schema_obj["schema"], human_examples=human_examples,
            )
        except GatewayError as e:
            return BatchOutcome(success=False, error_type=e.error_type, error_detail=e.message)
        current_results.update(cur_r)
        proposed_results.update(prop_r)
        return BatchOutcome(success=True, success_count=len(batch_items))

    worker = BatchJobWorker(
        job_type="prompt_validation", item_batches=batches, process_batch_fn=process,
        job_repo=job_repo, batch_repo=batch_repo, resume_job_id=resume_job_id,
        job_label_fn=lambda it: it.row_id, max_concurrency=1,
    )
    # 供 finished_job 處理端計算最終指標用（唯讀評估結果只留在記憶體，不進 DB news 表）
    worker.prompt_tuning_context = {
        "draft_id": draft.draft_id,
        "correction_items": correction_items,
        "control_items": control_items,
        "current_results": current_results,
        "proposed_results": proposed_results,
    }
    return worker
