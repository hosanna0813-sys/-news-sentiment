"""回饋 log 輔助函式 與 規則草案生成服務 — 對應規格書 十三"""
from __future__ import annotations

import json
from typing import List, Dict, Any

from app.models.feedback import FeedbackLogEntry, RuleDraft
from app.repositories.feedback_repository import FeedbackRepository
from app.services.ai.model_gateway import ModelGateway
from app.utils.text_utils import new_id, coerce_model_list
from app.utils.logging_setup import get_logger

logger = get_logger("feedback_service")


def log_feedback(repo: FeedbackRepository, batch_id: str, entity_type: str, entity_id: str,
                  ai_original_value: str, human_final_value: str, action: str,
                  reason: str = "", operator: str = "") -> None:
    """記錄一筆人工修正回饋（規格十三 A 層：原始回饋 log 完整保存）"""
    entry = FeedbackLogEntry(
        feedback_id=new_id("fb_"), batch_id=batch_id, entity_type=entity_type, entity_id=entity_id,
        ai_original_value=ai_original_value, human_final_value=human_final_value,
        action=action, reason=reason, operator=operator,
    )
    repo.add(entry)


def generate_rule_drafts(gateway: ModelGateway, feedback_entries: List[FeedbackLogEntry],
                          system_prompt: str, user_template: str, tool_name: str,
                          tool_schema: Dict[str, Any]) -> List[RuleDraft]:
    """AI 根據回饋提出規則草案（不自動啟用，需人工採用）

    V4.1.7 修正：
    - 只使用「人工修正」紀錄（action 以 human_ 開頭或 human_final_value 非空）；
      AI 自身判斷紀錄（ai_judge）是雜訊，會淹沒真正的修正訊號。
    - 壓縮 payload：長值截斷、只送必要欄位、上限取最近 300 筆，避免超出上下文。
    - 略過模型回傳中缺 rule_text 或 name 的無效草案。
    """
    human_entries = [e for e in feedback_entries
                      if (e.action or "").startswith("human_") or (e.human_final_value or "").strip()]
    if not human_entries:
        return []
    human_entries = human_entries[:300]  # list_all 已按時間新到舊排序

    def _clip(s: str, n: int = 120) -> str:
        s = s or ""
        return s if len(s) <= n else s[:n] + "…"

    payload = [
        {"entity_type": e.entity_type, "ai_original_value": _clip(e.ai_original_value),
         "human_final_value": _clip(e.human_final_value), "action": e.action,
         "reason": _clip(e.reason)}
        for e in human_entries
    ]
    user_content = user_template.format(feedback_batch_json=json.dumps(payload, ensure_ascii=False))
    result = gateway.call_with_tool(
        task="rule_draft", system_prompt=system_prompt, user_content=user_content,
        tool_name=tool_name, tool_schema=tool_schema,
    )
    raw_drafts = coerce_model_list(result.data, "rule_drafts")
    drafts = []
    for d in raw_drafts:
        if not isinstance(d, dict):
            continue  # 防禦：模型輸出未遵守 schema 的項目直接略過
        name = str(d.get("name", "")).strip()
        rule_text = str(d.get("rule_text", "")).strip()
        if not name or not rule_text:
            logger.warning(f"略過缺少名稱或規則內容的無效草案: {d!r}")
            continue
        drafts.append(RuleDraft(
            rule_id=new_id("rule_"),
            name=name,
            scope=d.get("scope", ""),
            rule_text=rule_text,
            supporting_case_count=int(d.get("supporting_case_count", 0) or 0),
            representative_cases=d.get("representative_cases", ""),
            risk_notes=d.get("risk_notes", ""),
            priority=d.get("priority", "中"),
            status="draft",
            generated_by_model=result.model_used,
        ))
    return drafts
