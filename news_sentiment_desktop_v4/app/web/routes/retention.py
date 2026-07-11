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
from app.web.job_runner import start_batch_job, find_running_job_id, BatchOutcome
from app.repositories.news_repository import NewsRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.services.retention.retention_service import prefilter_batch, judge_batch, decide_retain, \
    apply_human_retention_override, build_human_examples, _FALLBACK_JUDGEMENT
from app.services.feedback.feedback_service import log_feedback
from app.services.taxonomy import build_keyword_context as _build_keyword_context, \
    prepend_keyword_context
from app.prompts.registry import get_active_prompt
from app.utils.text_utils import extract_keywords_from_taxonomy, highlight_keywords, clean_body_for_preview

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
    """已收斂至 retention_service.build_human_examples()（原本這裡與桌面版
    app/workers/retention_worker.py 的 _build_retention_human_examples 各自
    重複實作一份、且已經出現分岔——這份原本多了 reason 標題快照、桌面版原本
    多了 ★星等），保留原函式名稱供既有測試沿用，兩邊現在拿到完整功能。"""
    return build_human_examples(feedback_repo, news_repo, MAX_FEWSHOT_EXAMPLES)


def build_keyword_context(ctx) -> str:
    """已收斂至 app/services/taxonomy.py（桌面版留用初判／分群 worker 現在也要
    注入同一段對照表，避免兩份文案分岔），保留原函式簽名供既有呼叫端與測試沿用。"""
    return _build_keyword_context(ctx.settings.keyword_taxonomy)


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

    human_examples = prepend_keyword_context(
        ctx.settings.keyword_taxonomy,
        _build_human_examples(FeedbackRepository(), NewsRepository()))

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
        # 批次開跑到寫回之間，使用者可能已在頁面上人工覆寫留用狀態——
        # 寫回前重讀一次目前值，AI 結果不可回頭蓋掉人工判斷
        skipped_human = set()
        for u in updates:
            cur = thread_news_repo.get(u["row_id"])
            if cur is not None and cur.retention_judged_by == "human":
                skipped_human.add(u["row_id"])
        updates = [u for u in updates if u["row_id"] not in skipped_human]
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
        # list_resumable 也會回傳 failed/retryable 的舊工作；這裡只關心「正在跑」
        # 的進度條，掛上舊的失敗 job 會讓頁面一直輪詢一個不會動的進度。
        job_id = find_running_job_id("retention")

    # 正文預覽維持完整原文、不截斷，只是把來源網頁常見的零星換行攤平成連續文字
    # （clean_body_for_preview，避免看起來被切成一截一截），並把設定頁「議題／
    # 關鍵字彙整表」裡出現過的詞加粗提示——純視覺輔助，不影響 AI 判斷邏輯
    # （那邊仍是整段原文交給模型）。
    keywords = extract_keywords_from_taxonomy(ctx.settings.keyword_taxonomy)
    body_html_by_row_id = {
        it.row_id: highlight_keywords(clean_body_for_preview(it.body_text), keywords)
        for it in items if it.body_text
    }
    return render_template("retention.html", items=items, job_id=job_id,
                            body_html_by_row_id=body_html_by_row_id)


@retention_bp.route("/retention/run", methods=["POST"])
def run():
    ctx = get_context()
    # 已有留用初判（或一鍵完成）在跑：直接導回既有進度條，不重複開工作——
    # 兩個並行工作會對同一批新聞互相覆寫判斷結果
    existing = find_running_job_id("retention") or find_running_job_id("pipeline")
    if existing:
        return redirect(url_for("retention.index", job_id=existing))
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
        apply_human_retention_override(
            ctx.news_repo, FeedbackRepository(), row_id, retained,
            old_status=item.retention_status, action="human_override",
            operator="web", reason=item.title[:60])
    # 勾選留用是用背景 fetch 送出（見 retention.html 的 toggleRetained()），
    # 不需要整頁重新導向、也不用浪費一次完整頁面渲染；沒有這個標頭的請求
    # （例如停用 JS 時的一般表單提交）才走原本的重新導向。
    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    return redirect(url_for("retention.index"))
