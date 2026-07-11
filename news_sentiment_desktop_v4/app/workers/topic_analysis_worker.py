"""
TopicAnalysisWorker — 對應規格十一(議題綜整)、十二(立場分析)

工作流程步驟六「AI 議題綜整」：針對每個議題，先做綜整、再做立場分析，
兩者皆僅依據該議題全部可用新聞正文。單一議題失敗不影響其他議題（增量保存）。
"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from app.models.topic import Topic
from app.repositories.news_repository import NewsRepository
from app.repositories.topic_repository import TopicRepository, StanceRepository
from app.repositories.settings_repository import PromptRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.summarization.summarization_service import summarize_topic
from app.services.stance.stance_service import analyze_stance
from app.prompts.registry import get_active_prompt
from app.utils.text_utils import truncate_at_sentence
from app.utils.logging_setup import get_logger

logger = get_logger("topic_analysis_worker")


class TopicAnalysisWorker(QThread):
    progress = Signal(int, int, str, int, int)   # current, total, message, success, failed
    finished_all = Signal(int, int)                # success_count, failed_count
    topic_failed = Signal(str, str, str)           # topic_id, error_type, error_detail

    def __init__(self, topics: List[Topic], gateway: ModelGateway, news_repo: NewsRepository,
                 topic_repo: TopicRepository, stance_repo: StanceRepository,
                 prompt_repo: PromptRepository, db_path=None, parent=None):
        super().__init__(parent)
        self.topics = topics
        self.gateway = gateway
        self.news_repo = news_repo
        self.topic_repo = topic_repo
        self.stance_repo = stance_repo
        self.prompt_repo = prompt_repo
        self.db_path = db_path
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        # 在本 QThread 執行緒內重新建立 repo（thread-local 連線），理由同
        # clustering_worker.py 的說明——不沿用建構子收到的主執行緒 repo 物件。
        self.news_repo = NewsRepository(self.db_path)
        self.topic_repo = TopicRepository(self.db_path)
        self.stance_repo = StanceRepository(self.db_path)
        self.prompt_repo = PromptRepository(self.db_path)
        summ_cfg = get_active_prompt(self.prompt_repo, "topic_summarization")
        summ_schema = json.loads(summ_cfg.tool_schema_json)
        from app.prompts.summarization_prompt import (
            MAP_REDUCE_CHUNK_SYSTEM_PROMPT, MAP_REDUCE_CHUNK_USER_TEMPLATE,
        )
        stance_cfg = get_active_prompt(self.prompt_repo, "stance_analysis")
        stance_schema = json.loads(stance_cfg.tool_schema_json)

        success_count = 0
        failed_count = 0
        total = len(self.topics)

        for idx, topic in enumerate(self.topics):
            if self._cancel:
                break
            items = self.news_repo.list_by_topic(topic.topic_id)
            items = [it for it in items if it.body_text and it.body_fetch_status != "可疑"]
            self.progress.emit(idx, total, f"綜整議題：{topic.topic_name}", success_count, failed_count)

            if not items:
                failed_count += 1
                self.topic_failed.emit(topic.topic_id, "no_body", "議題內無可用正文新聞")
                continue

            try:
                summary_data = summarize_topic(
                    self.gateway, topic.topic_name, items,
                    summ_cfg.system_prompt, summ_cfg.user_template, summ_schema["name"],
                    summ_schema["schema"], MAP_REDUCE_CHUNK_SYSTEM_PROMPT, MAP_REDUCE_CHUNK_USER_TEMPLATE,
                )
                self.topic_repo.update_fields(topic.topic_id, {
                    # 180 字保險：prompt 已要求上限，模型偶爾超長時於句尾截斷再落庫
                    "summary_150": truncate_at_sentence(summary_data.get("summary_150", ""), 180),
                    "summary_300": summary_data.get("summary_300", ""),
                    "summary_full": summary_data.get("summary_full", ""),
                    "development_progress": summary_data.get("development_progress", ""),
                    "core_disputes": summary_data.get("core_disputes", ""),
                    "key_actors": summary_data.get("key_actors", ""),
                    "possible_impact": summary_data.get("possible_impact", ""),
                    "cited_news_count": summary_data.get("cited_news_count", len(items)),
                    "summarized_at": time.time(),
                    "summarized_by_model": self.gateway.resolve_model_id("topic_summarization"),
                })
            except GatewayError as e:
                failed_count += 1
                self.topic_failed.emit(topic.topic_id, e.error_type, e.message)
                logger.warning(f"議題 {topic.topic_name} 綜整失敗: {e.message}")
                continue  # 綜整失敗則跳過該議題的立場分析，避免用不完整資料誤判

            try:
                stances = analyze_stance(
                    self.gateway, topic.topic_id, topic.topic_name, items,
                    stance_cfg.system_prompt, stance_cfg.user_template, stance_schema["name"],
                    stance_schema["schema"],
                )
                self.stance_repo.delete_by_topic(topic.topic_id)
                self.stance_repo.upsert_many(stances)
                self.topic_repo.update_fields(topic.topic_id, {
                    "has_identifiable_stance": 1 if stances else 0,
                })
            except GatewayError as e:
                # 立場分析失敗不影響已完成的綜整結果
                logger.warning(f"議題 {topic.topic_name} 立場分析失敗: {e.message}")

            success_count += 1

        self.finished_all.emit(success_count, failed_count)
