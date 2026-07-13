"""
留用初判服務 — 對應規格書 六

以批次方式呼叫 ModelGateway，取得每則新聞的留用建議。
單批失敗只回退該批，不讓整個流程中斷（由呼叫端 worker 搭配 JobRepository/BatchRepository 落實）。
"""
from __future__ import annotations

import json
from typing import List, Dict, Any

from app.models.news import NewsItem
from app.utils.text_utils import coerce_model_list, safe_format
from app.services.ai.model_gateway import ModelGateway, GatewayError, GatewayErrorType
from app.services.feedback.feedback_service import log_feedback
from app.utils.logging_setup import get_logger

logger = get_logger("retention_service")

MAX_FEWSHOT_EXAMPLES = 10


def build_human_examples(feedback_repo, news_repo, max_examples: int = MAX_FEWSHOT_EXAMPLES) -> str:
    """人工留用修正紀錄組成的少樣本範例（方案D），注入細評 prompt 讓模型學習
    編輯的判斷偏好。桌面版（原 retention_worker._build_retention_human_examples）
    與網頁版（原 retention.py 路由的 _build_human_examples）原本各自實作一份
    幾乎相同的邏輯，且已經出現分岔：網頁版多了從 reason 讀標題快照（讓「清除
    資料」把 news 清空後範例仍能顯示標題），桌面版多了顯示 ★星等。這裡收斂成
    一份，兩邊都保留各自原有名稱的薄轉接函式，同時拿到完整功能。"""
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
        title = e.reason or (it.title if it else "")
        if not title:
            continue  # 沒有標題快照、對應新聞也已不存在，無法組出有意義的範例
        star_part = ""
        if it is not None:
            stars = it.priority_stars if it.priority_stars else "無"
            star_part = f"★{stars} "
        old_label = "留用" if (e.ai_original_value or "") == "留用" else "不留用"
        new_label = "留用" if e.human_final_value == "留用" else "不留用"
        lines.append(f"- 新聞《{title[:40]}》：AI 原判 {star_part}{old_label} → 人工改判{new_label}")
        if len(lines) >= max_examples:
            break
    return "\n".join(lines)


def apply_human_retention_override(news_repo, feedback_repo, row_id: str, new_value: bool,
                                    old_status: str = "", action: str = "human_override",
                                    operator: str = "user", reason: str = "") -> str:
    """人工在留用初判頁勾選/取消留用時的狀態轉換：更新 retained/retention_status/
    retention_judged_by 並記錄 feedback log，回傳新的 retention_status 字串。

    桌面版與網頁版原本各自在 Qt slot／Flask route 裡重寫一份幾乎相同的邏輯
    （桌面版沒有 QApplication 就無法單元測試這段規則），現在收斂成一份共用函式。
    reason：選填的標題快照（見網頁版 clustering.move()），讓「清除資料」把 news
    列刪除後，few-shot 範例仍能顯示新聞標題。"""
    new_status = "留用" if new_value else "人工不留用"
    news_repo.update_fields(row_id, {
        "retained": 1 if new_value else 0,
        "retention_status": new_status,
        "retention_judged_by": "human",
    })
    log_feedback(feedback_repo, batch_id="", entity_type="retention", entity_id=row_id,
                 ai_original_value=old_status, human_final_value=new_status,
                 action=action, operator=operator, reason=reason)
    return new_status


_FALLBACK_JUDGEMENT: Dict[str, Any] = {
    "score_business_relevance": 0.0, "score_response_requirement": 0.0,
    "score_political_sensitivity": 0.0, "score_media_attention": 0.0,
    "score_public_impact": 0.0, "score_executive_bonus": 0.0, "score_final": 0.0,
    "priority_stars": 1, "should_respond": False, "is_moi_core_business": False,
}


def decide_retain(judgement: Dict[str, Any], priority_threshold: int) -> bool:
    """留用判斷公式：優先級達門檻，或 AI 判斷內政部應該回應，或屬於 MOI 核心業務旗標
    （方案A：獨立訊號，不依附在數字階梯上），符合任一條件即留用。

    正式留用流程（retention_worker.py）與 Prompt 調校驗證流程共用同一份判斷邏輯，
    避免兩邊各自維護一份公式、日後改動時悄悄兜不起來。"""
    return (judgement["priority_stars"] >= priority_threshold
            or judgement["should_respond"]
            or judgement["is_moi_core_business"])


def judge_batch(gateway: ModelGateway, items: List[NewsItem], system_prompt: str,
                 user_template: str, tool_name: str, tool_schema: Dict[str, Any],
                 human_examples: str = "") -> Dict[str, Dict[str, Any]]:
    """
    回傳 {row_id: {score_business_relevance, score_response_requirement, score_political_sensitivity,
                   score_media_attention, score_public_impact, score_executive_bonus, score_final,
                   priority_stars, should_respond, is_moi_core_business}}
    human_examples：近期人工修正範例的純文字區塊（方案D，見 retention_worker._build_retention_human_examples），
    空字串時不影響輸出。
    若該批呼叫失敗，直接拋出例外，交由 worker 標記整批 failed/retryable。
    """
    batch_payload = [
        {
            "row_id": it.row_id,
            "title": it.title,
            "summary": it.summary,
            "source": it.source,
            "published_at": it.published_at,
            "channel": it.channel,
            "is_duplicate": bool(it.duplicate_group_id),
        }
        for it in items
    ]
    examples_section = ""
    if human_examples:
        examples_section = (
            "\n【近期人工修正範例】以下是編輯過去對 AI 留用判斷的修正，"
            "特別留意 AI 過嚴（本應留用卻判不留用）的案例，並將其中反映出的判斷偏好"
            "套用到本次評分：\n" + human_examples + "\n")
    user_content = safe_format(
        user_template, news_batch_json=json.dumps(batch_payload, ensure_ascii=False),
        human_examples_section=examples_section)

    result = gateway.call_with_tool(
        task="retention_judgement",
        system_prompt=system_prompt,
        user_content=user_content,
        tool_name=tool_name,
        tool_schema=tool_schema,
    )
    judgements = coerce_model_list(result.data, "judgements")
    out: Dict[str, Dict[str, Any]] = {}
    for j in judgements:
        if not isinstance(j, dict):
            continue  # 防禦：模型輸出未遵守 schema 的項目直接略過
        rid = j.get("row_id")
        if not rid:
            continue
        out[rid] = {
            "score_business_relevance": float(j.get("business_relevance", 0.0)),
            "score_response_requirement": float(j.get("response_requirement", 0.0)),
            "score_political_sensitivity": float(j.get("political_sensitivity", 0.0)),
            "score_media_attention": float(j.get("media_attention", 0.0)),
            "score_public_impact": float(j.get("public_impact", 0.0)),
            "score_executive_bonus": float(j.get("executive_bonus", 0.0)),
            "score_final": float(j.get("final_score", 0.0)),
            "priority_stars": max(1, min(5, int(j.get("priority_stars", 1)))),
            "should_respond": bool(j.get("should_respond", False)),
            "is_moi_core_business": bool(j.get("is_moi_core_business", False)),
        }
    # 模型漏判的 row_id 保守標記為低優先級（→ 不留用，待人工複核）。但「漏判超過
    # 半批」幾乎一定是輸出被截斷或格式崩壞，默默全標不留用會讓使用者以為 AI 判斷
    # 完成（實際上整批都是後備值）——這種情況改拋錯，讓 worker 把該批標為失敗
    # 可重試，問題看得見。
    missing = [it.row_id for it in items if it.row_id not in out]
    # 只在「夠大的批次」套用整批失敗規則：一兩則的小批次維持既有契約
    # （個別格式錯誤 → 套後備值），避免單則解析失敗也整批重試打轉
    if len(items) >= 4 and len(missing) > len(items) / 2:
        raise GatewayError(
            GatewayErrorType.PARSE_ERROR,
            f"模型只回傳 {len(items) - len(missing)}/{len(items)} 則判斷"
            "（輸出可能被截斷或格式錯誤），整批標記失敗待重試，不套用保守後備值")
    if missing:
        logger.warning(f"留用細評有 {len(missing)} 則未在模型輸出中（{missing[:5]}...），"
                        "已套用保守後備判斷（低優先級、不留用），請人工複核這些新聞")
        for rid in missing:
            out[rid] = dict(_FALLBACK_JUDGEMENT)
    return out


def prefilter_batch(gateway: ModelGateway, items: List[NewsItem], system_prompt: str,
                     user_template: str, tool_name: str, tool_schema: Dict[str, Any]) -> Dict[str, bool]:
    """
    階段一粗篩：回傳 {row_id: is_relevant}。
    漏判的項目 fallback 為 True（放行進入階段二）——粗篩只是省錢用的第一關，
    寧可讓細判階段多花一次呼叫，也不要在粗篩就武斷排除可能重要的新聞。
    若該批呼叫失敗，直接拋出例外，交由 worker 標記整批 failed/retryable。
    """
    batch_payload = [
        {
            "row_id": it.row_id,
            "title": it.title,
            "summary": it.summary,
            "source": it.source,
            "published_at": it.published_at,
            "channel": it.channel,
            "is_duplicate": bool(it.duplicate_group_id),
        }
        for it in items
    ]
    user_content = user_template.format(news_batch_json=json.dumps(batch_payload, ensure_ascii=False))

    result = gateway.call_with_tool(
        task="retention_prefilter",
        system_prompt=system_prompt,
        user_content=user_content,
        tool_name=tool_name,
        tool_schema=tool_schema,
    )
    judgements = coerce_model_list(result.data, "judgements")
    out: Dict[str, bool] = {}
    for j in judgements:
        if not isinstance(j, dict):
            continue
        rid = j.get("row_id")
        if rid:
            out[rid] = bool(j.get("is_relevant", True))
    for it in items:
        if it.row_id not in out:
            out[it.row_id] = True
    irrelevant = sum(1 for v in out.values() if not v)
    if irrelevant == len(items):
        # 整批被粗篩全滅極不尋常（粗篩設計上寧可放行），大聲記下來供排查
        logger.warning(f"粗篩將整批 {len(items)} 則全部判定不相關（全部直接標不留用），"
                        "若非該批確實都是娛樂/財經雜訊，請檢查模型輸出")
    elif irrelevant:
        logger.info(f"粗篩：{len(items)} 則中 {irrelevant} 則判定不相關")
    return out
