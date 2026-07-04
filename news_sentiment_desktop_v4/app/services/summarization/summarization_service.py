"""
議題綜整服務 — 對應規格書 十一

單一議題正文過長、超出模型 context window 或成本考量時，使用
「分段萃取 → 中間摘要 → 最終整合」流程（map-reduce 式摘要）。
"""
from __future__ import annotations

import json
from typing import List, Dict, Any

from app.models.news import NewsItem
from app.services.ai.model_gateway import ModelGateway
from app.utils.logging_setup import get_logger

logger = get_logger("summarization_service")

# 觸發 map-reduce 的正文總字數門檻（保守值，避免超出 context window / 成本失控）
MAP_REDUCE_TRIGGER_CHARS = 15000
CHUNK_SIZE_ITEMS = 8


def summarize_topic(gateway: ModelGateway, topic_name: str, items: List[NewsItem],
                     system_prompt: str, user_template: str, tool_name: str,
                     tool_schema: Dict[str, Any],
                     map_reduce_system_prompt: str, map_reduce_user_template: str) -> Dict[str, Any]:
    total_chars = sum(len(it.body_text or "") for it in items)

    if total_chars <= MAP_REDUCE_TRIGGER_CHARS:
        news_payload = [
            {"row_id": it.row_id, "title": it.title, "source": it.source,
             "published_at": it.published_at, "body_text": it.body_text}
            for it in items
        ]
    else:
        logger.info(f"議題「{topic_name}」正文總長度 {total_chars} 字，觸發 map-reduce 中間摘要流程")
        chunks = [items[i:i + CHUNK_SIZE_ITEMS] for i in range(0, len(items), CHUNK_SIZE_ITEMS)]
        intermediate_summaries = []
        for idx, chunk in enumerate(chunks):
            chunk_payload = [
                {"row_id": it.row_id, "title": it.title, "body_text": it.body_text} for it in chunk
            ]
            user_content = map_reduce_user_template.format(
                topic_name=topic_name, chunk_news_json=json.dumps(chunk_payload, ensure_ascii=False))
            # map 階段僅需中間產物文字，不強制結構化輸出
            summary_text = _run_map_chunk(gateway, map_reduce_system_prompt, user_content)
            intermediate_summaries.append({
                "chunk_index": idx,
                "member_row_ids": [it.row_id for it in chunk],
                "summary_text": summary_text,
            })
        news_payload = [
            {"row_id": s["member_row_ids"], "title": f"中間摘要批次 {s['chunk_index']+1}",
             "source": "map-reduce", "published_at": "", "body_text": s["summary_text"]}
            for s in intermediate_summaries
        ]

    user_content = user_template.format(topic_name=topic_name,
                                         topic_news_json=json.dumps(news_payload, ensure_ascii=False))
    result = gateway.call_with_tool(
        task="topic_summarization", system_prompt=system_prompt, user_content=user_content,
        tool_name=tool_name, tool_schema=tool_schema,
    )
    data = result.data if isinstance(result.data, dict) else {}
    data["cited_news_count"] = len(items)
    return data


def _run_map_chunk(gateway: ModelGateway, system_prompt: str, user_content: str) -> str:
    """map 階段：呼叫模型產生中間摘要純文字（經過 Gateway 的參數自癒與能力過濾）"""
    return gateway.call_text(task="topic_summarization", system_prompt=system_prompt,
                              user_content=user_content, max_tokens=2048)
