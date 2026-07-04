"""立場分析服務 — 對應規格書 十二"""
from __future__ import annotations

import json
from typing import List, Dict, Any

from app.models.news import NewsItem
from app.models.topic import Stance
from app.services.ai.model_gateway import ModelGateway
from app.utils.text_utils import new_id, coerce_model_list
from app.utils.logging_setup import get_logger

logger = get_logger("stance_service")

VALID_STANCE_TYPES = ("支持", "反對／質疑", "官方回應")


def analyze_stance(gateway: ModelGateway, topic_id: str, topic_name: str, items: List[NewsItem],
                    system_prompt: str, user_template: str, tool_name: str,
                    tool_schema: Dict[str, Any]) -> List[Stance]:
    payload = [
        {"row_id": it.row_id, "title": it.title, "body_text": it.body_text}
        for it in items if it.body_text
    ]
    if not payload:
        return []
    user_content = user_template.format(topic_name=topic_name,
                                         topic_news_json=json.dumps(payload, ensure_ascii=False))
    result = gateway.call_with_tool(
        task="stance_analysis", system_prompt=system_prompt, user_content=user_content,
        tool_name=tool_name, tool_schema=tool_schema,
    )
    raw_stances = coerce_model_list(result.data, "stances")

    stances: List[Stance] = []
    for s in raw_stances:
        if not isinstance(s, dict):
            continue  # 防禦：模型輸出未遵守 schema 的項目直接略過
        stance_type = s.get("stance_type", "")
        if stance_type not in VALID_STANCE_TYPES:
            # 立場類別固定三種，非固定類別的輸出視為無效並略過（不可自行發明新類別）
            logger.warning(f"忽略非固定類別的立場輸出: {stance_type}")
            continue
        stances.append(Stance(
            stance_id=new_id("stance_"),
            topic_id=topic_id,
            stance_type=stance_type,
            speaker=s.get("speaker", ""),
            organization=s.get("organization", ""),
            claim=s.get("claim", ""),
            evidence_news_id=s.get("evidence_news_id", ""),
            evidence_excerpt=s.get("evidence_excerpt", ""),
            confidence=float(s.get("confidence", 0.5)),
        ))
    return stances
