"""留用初判頁 — 對應桌面版 app/workers/retention_worker.py 的批次流程，
改寫成背景 Thread（見 app/web/job_runner.py）版本；核心 AI 呼叫完全重用
app/services/retention/retention_service.py，不重寫判斷邏輯。

build_retention_job_inputs() 是這裡與「一鍵完成」流程
（app/web/routes/pipeline.py）共用的組裝邏輯——prompt/human_examples/批次切法
只寫一次，兩邊都呼叫同一份，避免各自維護一份容易日後兜不起來。
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
        # 標題優先讀 reason 裡存的快照（記錄當下就存好，見 override() 的
        # log_feedback 呼叫）——這樣「清除資料」把 news 資料表清空之後，這筆
        # 回饋依然能用來組 few-shot 範例，不會因為 news_repo.get() 找不到
        # 對應新聞就整筆被跳過，讓「留給以後訓練 AI」的紀錄實際上失去作用。
        # 只有清除資料前就存在的舊紀錄（沒有標題快照）才需要退回即時查表。
        title = e.reason or ""
        if not title:
            it = news_repo.get(e.entity_id)
            if it is None:
                continue
            title = it.title
        old_label = "留用" if (e.ai_original_value or "") == "留用" else "不留用"
        new_label = "留用" if e.human_final_value == "留用" else "不留用"
        lines.append(f"- 新聞《{title[:40]}》：AI 原判 {old_label} → 人工改判{new_label}")
        if len(lines) >= MAX_FEWSHOT_EXAMPLES:
            break
    return "\n".join(lines)


def build_keyword_context(ctx) -> str:
    """把使用者在設定頁貼的議題／關鍵字彙整表，轉成一段可直接注入 prompt 的
    參考文字。刻意不在程式端解析布林語法（來源常有不平衡括號、不一致分隔符號
    等人工謄寫雜訊，硬解析容易悄悄出錯）——原文交給 AI 理解語意即可。"""
    taxonomy = (ctx.settings.keyword_taxonomy or "").strip()
    if not taxonomy:
        return ""
    return (
        "【業務關注議題與關鍵字對照表】以下是本單位各業務關注的議題分類與相關關鍵字"
        "（可能包含 | 代表或、& 代表且的檢索語法），請作為判斷新聞是否相關、"
        "以及應歸入哪個議題類別的重要參考：\n" + taxonomy
    )


def build_retention_job_inputs(ctx):
    """回傳 (batches, process_fn)；沒有待判斷的新聞時回傳 ([], None)。"""
    items = [it for it in ctx.news_repo.list_all() if it.retention_judged_by != "human"]
    if not items:
        return [], None

    prefilter_cfg = get_active_prompt(ctx.prompt_repo, "retention_prefilter")
    prefilter_schema = json.loads(prefilter_cfg.tool_schema_json)
    judge_cfg = get_active_prompt(ctx.prompt_repo, "retention_judgement")
    judge_schema = json.loads(judge_cfg.tool_schema_json)
    priority_threshold = ctx.settings.api.retention_priority_threshold

    keyword_context = build_keyword_context(ctx)
    human_examples = _build_human_examples(FeedbackRepository(), NewsRepository())
    if keyword_context:
        human_examples = f"{keyword_context}\n\n{human_examples}" if human_examples else keyword_context

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

    return batches, process


@retention_bp.route("/retention")
def index():
    ctx = get_context()
    items = ctx.news_repo.list_all()
    job_id = request.args.get("job_id")
    if not job_id:
        # 沒有 job_id 查詢參數時（例如使用者重新整理、或直接輸入網址回到這頁），
        # 仍主動查一次有沒有尚未跑完的留用初判工作並顯示進度條——避免使用者以為
        # 「進度條消失=卡住」，其實只是網址上的 job_id 不見了。
        running = JobRepository().list_resumable("retention")
        if running:
            job_id = running[0].job_id
    return render_template("retention.html", items=items, job_id=job_id)


@retention_bp.route("/retention/run", methods=["POST"])
def run():
    ctx = get_context()
    batches, process = build_retention_job_inputs(ctx)
    if not batches:
        return redirect(url_for("retention.index"))
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
                      action="human_override", operator="web", reason=item.title[:60])
    # 勾選留用是用背景 fetch 送出（見 retention.html 的 toggleRetained()），
    # 不需要整頁重新導向、也不用浪費一次完整頁面渲染；沒有這個標頭的請求
    # （例如停用 JS 時的一般表單提交）才走原本的重新導向。
    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    return redirect(url_for("retention.index"))
