"""
議題分群服務 — 對應規格書 九

流程：
    1. 將新聞依時間、來源做候選分桶（避免一次把所有正文送入模型）。
    2. AI 分批判斷群內關聯，建立初步議題。
    3. 進行跨批次議題合併（AI）。
    4. 顯示分群理由與信心。

正文不足的新聞（body_word_count 過短）不參與正式分群，標記為「正文不足待人工確認」，
不得硬併到其他議題。
"""
from __future__ import annotations

import json
from typing import List, Dict, Any
from datetime import datetime

from typing import List

from app.models.news import NewsItem
from app.services.ai.model_gateway import ModelGateway
from app.services.feedback.feedback_service import log_feedback
from app.utils.text_utils import safe_json_loads, coerce_model_list, safe_format
from app.utils.text_utils import new_id
from app.utils.logging_setup import get_logger

logger = get_logger("clustering_service")

MIN_BODY_WORDS_FOR_CLUSTERING = 50
BODY_EXCERPT_LEN = 1200  # 送入分群 prompt 的正文截斷長度，控制單批 token 量


def assign_news_to_topic(news_repo, feedback_repo, row_ids: List[str], topic_id: str,
                          topic_name: str, action: str, operator: str = "user") -> None:
    """人工把新聞歸入（既有或新建）議題：寫回 final_topic_id/name、清除低信心標記、
    記錄 feedback log。抽成獨立函式，因為桌面版議題調整頁的建立新議題／移入議題／
    拆分議題／合併議題四個操作原本各自呼叫一段幾乎相同的迴圈（且沒有 QApplication
    無法單獨測試）。"""
    for rid in row_ids:
        it = news_repo.get(rid)
        old_topic = it.final_topic_name if it else ""
        news_repo.update_fields(rid, {
            "final_topic_id": topic_id, "final_topic_name": topic_name,
            "clustering_confidence": 0,  # 人工確認過的歸屬，清除低信心標記
        })
        log_feedback(feedback_repo, batch_id="", entity_type="clustering", entity_id=rid,
                     ai_original_value=old_topic, human_final_value=topic_name,
                     action=action, operator=operator)


def unassign_news_from_topic(news_repo, feedback_repo, row_ids: List[str],
                              action: str = "human_unassign", operator: str = "user") -> None:
    """人工把新聞移出議題（拖回未分類清單／按下「不納入」按鈕皆呼叫這個函式，
    原本這兩個操作在頁面裡各自重複實作一次）。"""
    for rid in row_ids:
        it = news_repo.get(rid)
        old_topic = it.final_topic_name if it else ""
        news_repo.update_fields(rid, {"final_topic_id": "", "final_topic_name": ""})
        log_feedback(feedback_repo, batch_id="", entity_type="clustering", entity_id=rid,
                     ai_original_value=old_topic, human_final_value="（不納入任何議題）",
                     action=action, operator=operator)


def split_insufficient_body(items: List[NewsItem]) -> (List[NewsItem], List[NewsItem]):
    """回傳 (可分群新聞, 正文不足新聞)；「可疑」正文視為不足，不進入分群（V4.2.0）"""
    ok, insufficient = [], []
    for it in items:
        if (it.body_word_count >= MIN_BODY_WORDS_FOR_CLUSTERING and it.body_text
                and it.body_fetch_status != "可疑"):
            ok.append(it)
        else:
            insufficient.append(it)
    return ok, insufficient


def bucket_candidates(items: List[NewsItem], bucket_size: int = 15) -> List[List[NewsItem]]:
    """依時間排序後切成固定大小的候選分桶（簡單但可控；避免一次送全部正文）"""
    def sort_key(it: NewsItem):
        try:
            return datetime.fromisoformat(it.published_at) if it.published_at else datetime.min
        except Exception:
            return datetime.min

    sorted_items = sorted(items, key=sort_key)
    return [sorted_items[i:i + bucket_size] for i in range(0, len(sorted_items), bucket_size)]


def cluster_batch(gateway: ModelGateway, items: List[NewsItem], system_prompt: str,
                   user_template: str, tool_name: str, tool_schema: Dict[str, Any],
                   existing_topics: List[Dict[str, Any]] = None,
                   human_examples: str = "") -> List[Dict[str, Any]]:
    """對一個候選分桶呼叫 AI 進行分群，回傳 [{"topic_name","member_row_ids","reason","confidence"}]

    V4.2.0：
    - existing_topics：既有已確認議題清單（增量分群）。有值時要求模型優先將新聞
      歸入既有議題（直接沿用該議題的 topic_id），真正的新事件才建新議題。
    - human_examples：過去人工修正分群的 few-shot 範例文字，注入 prompt 供模型學習偏好。
    """
    payload = [
        {"row_id": it.row_id, "title": it.title,
         "body_excerpt": (it.body_text or "")[:BODY_EXCERPT_LEN]}
        for it in items
    ]
    existing_section = ""
    if existing_topics:
        existing_section = (
            "\n【既有議題清單（增量分群）】以下是先前已建立（多數經人工確認）的議題。"
            "請優先判斷每則新聞是否屬於其中之一：屬於者，輸出的候選議題請「直接沿用該議題的 "
            "topic_id 與 topic_name」；只有確實不屬於任何既有議題的新事件，才建立新議題"
            "（新議題不要填 topic_id，由系統產生）。\n"
            + json.dumps(existing_topics, ensure_ascii=False) + "\n")
    examples_section = ""
    if human_examples:
        examples_section = (
            "\n【過去人工修正範例】以下是編輯過去對 AI 分群結果的修正，"
            "請學習其中的歸類偏好並套用到本次分群：\n" + human_examples + "\n")

    user_content = safe_format(
        user_template, news_batch_json=json.dumps(payload, ensure_ascii=False),
        existing_topics_section=existing_section, human_examples_section=examples_section)
    result = gateway.call_with_tool(
        task="topic_clustering", system_prompt=system_prompt, user_content=user_content,
        tool_name=tool_name, tool_schema=tool_schema,
    )
    topics = coerce_model_list(result.data, "topics")
    # 防禦性驗證（V4.1.4）：模型輸出可能不完全遵守 schema（尤其 json_mode 降級時），
    # 例如 topics 內出現字串而非物件。逐筆驗證並正規化，不合格式者略過並記錄，
    # 不讓整批分群崩潰。
    normalized = []
    for t in topics:
        if not isinstance(t, dict):
            logger.warning(f"忽略非物件的分群輸出項目: {t!r}")
            continue
        member_ids = t.get("member_row_ids", [])
        if not isinstance(member_ids, list):
            member_ids = [member_ids] if member_ids else []
        member_ids = [str(m) for m in member_ids if m]
        if not member_ids:
            logger.warning(f"忽略無成員的分群輸出項目: {t.get('topic_name', '?')!r}")
            continue
        normalized.append({
            "topic_id": t.get("topic_id") or new_id("topic_"),
            "topic_name": str(t.get("topic_name", "未命名議題")),
            "member_row_ids": member_ids,
            "reason": str(t.get("reason", "")),
            "confidence": float(t.get("confidence", 0.5) or 0.5),
        })
    return normalized


def merge_candidate_topics(gateway: ModelGateway, candidate_topics: List[Dict[str, Any]],
                            system_prompt: str, user_template: str, tool_name: str,
                            tool_schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """跨批次議題整合：輸入多個候選議題（含摘要資訊），輸出最終合併方案"""
    if not candidate_topics:
        return []
    payload = [
        {"topic_id": t["topic_id"], "topic_name": t["topic_name"],
         "member_count": len(t.get("member_row_ids", [])),
         "sample_titles": t.get("sample_titles", [])}
        for t in candidate_topics
    ]
    user_content = user_template.format(candidate_topics_json=json.dumps(payload, ensure_ascii=False))
    result = gateway.call_with_tool(
        task="topic_merge", system_prompt=system_prompt, user_content=user_content,
        tool_name=tool_name, tool_schema=tool_schema,
    )
    groups = coerce_model_list(result.data, "merged_groups")
    # 防禦性驗證（V4.1.4）：同 cluster_batch，過濾不合格式的輸出項目
    normalized = []
    for g in groups:
        if not isinstance(g, dict):
            logger.warning(f"忽略非物件的整合輸出項目: {g!r}")
            continue
        src_ids = g.get("source_topic_ids", [])
        if not isinstance(src_ids, list):
            src_ids = [src_ids] if src_ids else []
        src_ids = [str(s) for s in src_ids if s]
        if not src_ids:
            continue
        normalized.append({
            "final_topic_name": str(g.get("final_topic_name", "未命名議題")),
            "source_topic_ids": src_ids,
            "reason": str(g.get("reason", "")),
        })
    return normalized
