"""留用初判 Worker — 組裝 BatchJobWorker 用於規格六的批次 AI 留用判斷（v3：兩段式）

每個外層批次內部依序做兩次 API 呼叫，算在同一個 BatchOutcome 裡（整批成功或整批
retryable，重試時粗篩會跟著重跑）：
    1. 粗篩（Haiku，便宜快速）：篩掉明顯不相關的新聞，直接標記「AI建議不留用」
    2. 細評（Sonnet，僅對通過粗篩的新聞執行）：完整 MOI 政策關注度評分

process_batch_fn 可能在 ThreadPoolExecutor 的工作執行緒中被平行呼叫（見
batch_job_worker.py 的 max_concurrency），因此內部一律自行建立 thread-local 的
NewsRepository/FeedbackRepository，不使用呼叫端在主執行緒建立的 repo 物件
（sqlite3 連線不可跨執行緒共用同一物件，比照 import_worker.py 的既有慣例）。
"""
from __future__ import annotations

from typing import List, Optional

from app.models.news import NewsItem
from app.repositories.news_repository import NewsRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.settings_repository import PromptRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.retention.retention_service import (
    judge_batch, prefilter_batch, decide_retain, _FALLBACK_JUDGEMENT as _DEFAULT_JUDGEMENT,
)
from app.services.feedback.feedback_service import log_feedback
from app.prompts.registry import get_active_prompt
from app.workers.batch_job_worker import BatchJobWorker, BatchOutcome
import json

MAX_FEWSHOT_EXAMPLES = 10

_ZERO_SCORE_FIELDS = {
    "score_business_relevance": 0.0, "score_response_requirement": 0.0,
    "score_political_sensitivity": 0.0, "score_media_attention": 0.0,
    "score_public_impact": 0.0, "score_executive_bonus": 0.0, "score_final": 0.0,
    "priority_stars": 0, "should_respond": 0, "is_moi_core_business": 0,
}


def _build_retention_human_examples(feedback_repo, news_repo: NewsRepository,
                                     max_examples: int = MAX_FEWSHOT_EXAMPLES) -> str:
    """方案D：讀取近期人工留用修正紀錄，組成少樣本範例注入細評 prompt（比照
    clustering_worker._build_human_examples 的作法）。只計入人工覆核紀錄
    （action 以 human_ 開頭），不含 AI 自己的判斷 log。"""
    if feedback_repo is None:
        return ""
    try:
        entries = feedback_repo.list_all(entity_type="retention")
    except Exception:
        return ""
    lines = []
    for e in entries:
        if not (e.action or "").startswith("human_"):
            continue
        if not (e.human_final_value or "").strip():
            continue
        it = news_repo.get(e.entity_id)
        if it is None:
            continue
        title = it.title[:40]
        old_stars = it.priority_stars if it.priority_stars else "無"
        old_label = "留用" if (e.ai_original_value or "") == "留用" else "不留用"
        new_label = "留用" if e.human_final_value == "留用" else "不留用"
        lines.append(f"- 新聞《{title}》：AI 原判 ★{old_stars} {old_label} → 人工改判{new_label}")
        if len(lines) >= max_examples:
            break
    return "\n".join(lines)


def build_retention_worker(items: List[NewsItem], batch_size: int, gateway: ModelGateway,
                            prompt_repo: PromptRepository, job_repo: JobRepository,
                            batch_repo: BatchRepository,
                            priority_threshold: int = 3,
                            max_concurrency: int = 1,
                            resume_job_id: Optional[str] = None,
                            db_path=None,
                            feedback_repo=None) -> BatchJobWorker:
    prefilter_cfg = get_active_prompt(prompt_repo, "retention_prefilter")
    prefilter_schema_obj = json.loads(prefilter_cfg.tool_schema_json)
    judge_cfg = get_active_prompt(prompt_repo, "retention_judgement")
    judge_schema_obj = json.loads(judge_cfg.tool_schema_json)

    human_examples = ""
    if feedback_repo is not None:
        human_examples = _build_retention_human_examples(feedback_repo, NewsRepository(db_path))

    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

    def process(batch_items: List[NewsItem]) -> BatchOutcome:
        try:
            relevance = prefilter_batch(
                gateway, batch_items, prefilter_cfg.system_prompt, prefilter_cfg.user_template,
                prefilter_schema_obj["name"], prefilter_schema_obj["schema"],
            )
        except GatewayError as e:
            return BatchOutcome(success=False, error_type=e.error_type, error_detail=e.message)

        relevant_items = [it for it in batch_items if relevance.get(it.row_id, True)]
        not_relevant_items = [it for it in batch_items if not relevance.get(it.row_id, True)]

        updates = []
        for it in not_relevant_items:
            updates.append({
                "row_id": it.row_id, "retention_status": "AI建議不留用", "retained": 0,
                "retention_reason": "", "action_reasoning": "", "retention_judged_by": "ai",
                **_ZERO_SCORE_FIELDS,
            })

        if relevant_items:
            try:
                results = judge_batch(
                    gateway, relevant_items, judge_cfg.system_prompt, judge_cfg.user_template,
                    judge_schema_obj["name"], judge_schema_obj["schema"],
                    human_examples=human_examples,
                )
            except GatewayError as e:
                return BatchOutcome(success=False, error_type=e.error_type, error_detail=e.message)

            for it in relevant_items:
                r = results.get(it.row_id) or dict(_DEFAULT_JUDGEMENT)
                retain = decide_retain(r, priority_threshold)
                new_status = "留用" if retain else "AI建議不留用"
                updates.append({
                    "row_id": it.row_id,
                    "retention_status": new_status,
                    "retained": 1 if retain else 0,
                    "retention_reason": "",
                    "action_reasoning": "",
                    "score_business_relevance": r["score_business_relevance"],
                    "score_response_requirement": r["score_response_requirement"],
                    "score_political_sensitivity": r["score_political_sensitivity"],
                    "score_media_attention": r["score_media_attention"],
                    "score_public_impact": r["score_public_impact"],
                    "score_executive_bonus": r["score_executive_bonus"],
                    "score_final": r["score_final"],
                    "priority_stars": r["priority_stars"],
                    "should_respond": 1 if r["should_respond"] else 0,
                    "is_moi_core_business": 1 if r["is_moi_core_business"] else 0,
                    "retention_judged_by": "ai",
                })

        thread_news_repo = NewsRepository(db_path)
        thread_feedback_repo = FeedbackRepository(db_path)
        thread_news_repo.update_fields_bulk(updates)
        for u in updates:
            log_feedback(thread_feedback_repo, batch_id="", entity_type="retention", entity_id=u["row_id"],
                         ai_original_value=json.dumps(u, ensure_ascii=False),
                         human_final_value="", action="ai_judge", operator="system")
        return BatchOutcome(success=True, success_count=len(batch_items))

    return BatchJobWorker(
        job_type="retention", item_batches=batches, process_batch_fn=process,
        job_repo=job_repo, batch_repo=batch_repo, resume_job_id=resume_job_id,
        job_label_fn=lambda it: it.row_id, max_concurrency=max_concurrency,
    )
