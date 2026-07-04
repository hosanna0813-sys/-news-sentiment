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
from app.services.ai.model_gateway import ModelGateway
from app.utils.logging_setup import get_logger

logger = get_logger("retention_service")


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
    # 若模型漏判某些 row_id，保守標記為低優先級（待人工複核），不可假裝已判斷完成，
    # 也不可預設為高優先級去打擾長官
    for it in items:
        if it.row_id not in out:
            out[it.row_id] = dict(_FALLBACK_JUDGEMENT)
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
    return out
