"""留用初判頁 — 對應桌面版 app/workers/retention_worker.py 的批次流程，
改寫成背景 Thread（見 app/web/job_runner.py）版本；核心 AI 呼叫完全重用
app/services/retention/retention_service.py，不重寫判斷邏輯。
"""
from __future__ import annotations

import json

from flask import Blueprint, redirect, render_template, request, url_for

from app.web.server import get_context
from app.web.job_runner import start_batch_job, BatchOutcome
from app.repositories.news_repository import NewsRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.services.retention.retention_service import prefilter_batch, judge_batch, decide_retain, \
    _FALLBACK_JUDGEMENT
from app.services.feedback.feedback_service import log_feedback
from app.prompts.registry import get_active_prompt

retention_bp = Blueprint("retention", __name__)

BATCH_SIZE = 10
MAX_FEWSHOT_EXAMPLES = 10

_ZERO_SCORE_FIELDS = {
    "score_business_relevance": 0.0, "score_response_requirement": 0.0,
    "score_political_sensitivity": 0.0, "score_media_attention": 0.0,
    "score_public_impact": 0.0, "score_executive_bonus": 0.0, "score_final": 0.0,
    "priority_stars": 0, "should_respond": 0, "is_moi_core_business": 0,
}


def _build_human_examples(feedback_repo, news_repo) -> str:
    entries = feedback_repo.list_all(entity_type="retention")
    lines = []
    for e in entries:
        if not (e.action or "").startswith("human_") or not (e.human_final_value or "").strip():
            continue
        it = news_repo.get(e.entity_id)
        if it is None:
            continue
        old_label = "留用" if (e.ai_original_value or "") == "留用" else "不留用"
        new_label = "留用" if e.human_final_value == "留用" else "不留用"
        lines.append(f"- 新聞《{it.title[:40]}》：AI 原判 {old_label} → 人工改判{new_label}")
        if len(lines) >= MAX_FEWSHOT_EXAMPLES:
            break
    return "\n".join(lines)


@retention_bp.route("/retention")
def index():
    ctx = get_context()
    items = ctx.news_repo.list_all()
    return render_template("retention.html", items=items, job_id=request.args.get("job_id"))


@retention_bp.route("/retention/run", methods=["POST"])
def run():
    ctx = get_context()
    items = [it for it in ctx.news_repo.list_all() if it.retention_judged_by != "human"]
    if not items:
        return redirect(url_for("retention.index"))

    prefilter_cfg = get_active_prompt(ctx.prompt_repo, "retention_prefilter")
    prefilter_schema = json.loads(prefilter_cfg.tool_schema_json)
    judge_cfg = get_active_prompt(ctx.prompt_repo, "retention_judgement")
    judge_schema = json.loads(judge_cfg.tool_schema_json)
    priority_threshold = ctx.settings.api.retention_priority_threshold
    human_examples = _build_human_examples(FeedbackRepository(), NewsRepository())

    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]

    def process(batch_items):
        relevance = prefilter_batch(
            ctx.gateway, batch_items, prefilter_cfg.system_prompt, prefilter_cfg.user_template,
            prefilter_schema["name"], prefilter_schema["schema"],
        )
        relevant_items = [it for it in batch_items if relevance.get(it.row_id, True)]
        not_relevant_items = [it for it in batch_items if not relevance.get(it.row_id, True)]

        updates = []
        for it in not_relevant_items:
            updates.append({"row_id": it.row_id, "retention_status": "AI建議不留用", "retained": 0,
                             "retention_judged_by": "ai", **_ZERO_SCORE_FIELDS})

        if relevant_items:
            results = judge_batch(
                ctx.gateway, relevant_items, judge_cfg.system_prompt, judge_cfg.user_template,
                judge_schema["name"], judge_schema["schema"], human_examples=human_examples,
            )
            for it in relevant_items:
                r = results.get(it.row_id) or dict(_FALLBACK_JUDGEMENT)
                retain = decide_retain(r, priority_threshold)
                updates.append({
                    "row_id": it.row_id,
                    "retention_status": "留用" if retain else "AI建議不留用",
                    "retained": 1 if retain else 0,
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

        thread_news_repo = NewsRepository()
        thread_feedback_repo = FeedbackRepository()
        thread_news_repo.update_fields_bulk(updates)
        for u in updates:
            log_feedback(thread_feedback_repo, batch_id="", entity_type="retention", entity_id=u["row_id"],
                          ai_original_value=json.dumps(u, ensure_ascii=False),
                          human_final_value="", action="ai_judge", operator="system")
        return BatchOutcome(success=True, success_count=len(batch_items))

    job_id = start_batch_job("retention", batches, process, JobRepository(), BatchRepository())
    return redirect(url_for("retention.index", job_id=job_id))


@retention_bp.route("/retention/override", methods=["POST"])
def override():
    ctx = get_context()
    row_id = request.form["row_id"]
    retained = request.form.get("retained") == "on"
    item = ctx.news_repo.get(row_id)
    if item is not None:
        old_status = item.retention_status
        ctx.news_repo.update_fields(row_id, {
            "retained": 1 if retained else 0,
            "retention_status": "留用" if retained else "人工不留用",
            "retention_judged_by": "human",
        })
        log_feedback(FeedbackRepository(), batch_id="", entity_type="retention", entity_id=row_id,
                      ai_original_value=old_status, human_final_value="留用" if retained else "不留用",
                      action="human_override", operator="web")
    return redirect(url_for("retention.index"))
