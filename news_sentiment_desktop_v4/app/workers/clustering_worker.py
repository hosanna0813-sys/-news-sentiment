"""
ClusteringWorker — 對應規格九（V4.2.0 擴充）

流程：候選分桶 → AI 分批分群 → 跨批次議題合併 → 寫回資料庫。

V4.2.0 新增：
1. 增量分群：incremental=True 時只處理「尚未歸入任何議題」的新聞，並把既有
   議題（含範例標題）注入 prompt，要求模型優先歸入既有議題（直接沿用
   topic_id），真正的新事件才建新議題——人工確認過的議題結構不會被重跑打散。
2. few-shot 閉環學習：讀取回饋 log 中的人工分群修正（拖曳/合併/拆分等），
   組成範例注入 prompt，讓模型學習編輯的歸類偏好。
3. 逐則信心分數完整落庫（含合併分支），供 UI 標示低信心新聞。
"""
from __future__ import annotations

import json
from typing import List, Optional, Dict, Any

from PySide6.QtCore import QThread, Signal

from app.models.news import NewsItem
from app.models.topic import Topic
from app.repositories.news_repository import NewsRepository
from app.repositories.topic_repository import TopicRepository
from app.repositories.settings_repository import PromptRepository
from app.repositories.feedback_repository import FeedbackRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.clustering.clustering_service import (
    split_insufficient_body, bucket_candidates, cluster_batch, merge_candidate_topics,
    build_combined_clustering_examples,
)
from app.prompts.registry import get_active_prompt
from app.services.taxonomy import prepend_keyword_context
from app.utils.text_utils import new_id
from app.utils.logging_setup import get_logger

logger = get_logger("clustering_worker")

MAX_FEWSHOT_EXAMPLES = 10
MAX_EXISTING_TOPICS_IN_PROMPT = 30


class ClusteringWorker(QThread):
    progress = Signal(int, int, str)     # current, total, message
    finished_ok = Signal(int)            # 最終議題數（本次新建+沿用既有）
    finished_error = Signal(str)

    def __init__(self, gateway: ModelGateway, news_repo: NewsRepository, topic_repo: TopicRepository,
                 prompt_repo: PromptRepository, bucket_size: int = 15,
                 incremental: bool = False, feedback_repo=None, db_path=None,
                 keyword_taxonomy: str = "", granularity: str = "standard", parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.news_repo = news_repo
        self.topic_repo = topic_repo
        self.prompt_repo = prompt_repo
        self.bucket_size = bucket_size
        self.incremental = incremental
        self.feedback_repo = feedback_repo
        self.db_path = db_path
        self.keyword_taxonomy = keyword_taxonomy
        self.granularity = granularity
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    # ---------- few-shot 範例（閉環學習） ----------
    def _build_human_examples(self) -> str:
        """已收斂至 clustering_service.build_combined_clustering_examples()（原本這裡
        與網頁版 app/web/routes/clustering.py 各自重複實作一份，且都遺漏了改名
        （topic_naming）與議題合併（human_merge_topic）兩類最直接的粒度／命名訊號），
        保留原方法名稱供既有測試沿用。"""
        if self.feedback_repo is None:
            return ""
        return build_combined_clustering_examples(self.feedback_repo, self.news_repo,
                                                    MAX_FEWSHOT_EXAMPLES)

    # ---------- 既有議題（增量分群） ----------
    def _build_existing_topics(self) -> List[Dict[str, Any]]:
        existing = []
        for t in self.topic_repo.list_active():
            members = self.news_repo.list_by_topic(t.topic_id)
            if not members:
                continue
            existing.append({
                "topic_id": t.topic_id,
                "topic_name": t.topic_name,
                "sample_titles": [m.title for m in members[:3]],
            })
            if len(existing) >= MAX_EXISTING_TOPICS_IN_PROMPT:
                break
        return existing

    def run(self) -> None:
        # 在本 QThread 執行緒內重新建立 repo（thread-local 連線），不沿用建構子
        # 收到、在主執行緒建立的 repo 物件——sqlite3 連線物件不可跨執行緒共用，
        # 否則主執行緒同時操作 UI 時可能與本執行緒的寫入互相干擾（比照
        # retention_worker.py 的既有慣例）。
        self.news_repo = NewsRepository(self.db_path)
        self.topic_repo = TopicRepository(self.db_path)
        self.prompt_repo = PromptRepository(self.db_path)
        if self.feedback_repo is not None:
            self.feedback_repo = FeedbackRepository(self.db_path)
        try:
            all_items = self.news_repo.list_retained_with_body()

            existing_topics: List[Dict[str, Any]] = []
            existing_topic_ids: set = set()
            existing_name_lookup: Dict[str, str] = {}
            if self.incremental:
                existing_topics = self._build_existing_topics()
                existing_topic_ids = {t["topic_id"] for t in existing_topics}
                existing_name_lookup = {t["topic_id"]: t["topic_name"] for t in existing_topics}
                # 增量模式：只處理尚未歸入議題的新聞，已確認結構不動
                all_items = [it for it in all_items
                              if not it.final_topic_id
                              or it.final_topic_name == "正文不足待人工確認"]

            clusterable, insufficient = split_insufficient_body(all_items)

            self.news_repo.update_fields_bulk([
                {"row_id": it.row_id, "final_topic_id": "",
                 "final_topic_name": "正文不足待人工確認"}
                for it in insufficient
            ])

            if not clusterable:
                self.finished_ok.emit(len(existing_topic_ids))
                return

            # 設定頁「議題／關鍵字彙整表」接在 few-shot 範例前，供模型參考
            # 業務議題分類命名（網頁版已有，桌面版補齊）
            human_examples = prepend_keyword_context(
                self.keyword_taxonomy, self._build_human_examples())

            buckets = bucket_candidates(clusterable, self.bucket_size)
            clustering_cfg = get_active_prompt(self.prompt_repo, "topic_clustering")
            clustering_schema = json.loads(clustering_cfg.tool_schema_json)
            title_lookup = {it.row_id: it.title for it in clusterable}

            candidate_topics = []          # 新議題候選（進入跨批次整合）
            assigned_to_existing = []       # 直接歸入既有議題的 (row_id, topic_id, reason, conf)

            for idx, bucket in enumerate(buckets):
                if self._cancel:
                    self.finished_error.emit("使用者已取消")
                    return
                self.progress.emit(idx, len(buckets), f"分群候選分桶 {idx + 1}/{len(buckets)}")
                try:
                    topics = cluster_batch(
                        self.gateway, bucket, clustering_cfg.system_prompt,
                        clustering_cfg.user_template,
                        clustering_schema["name"], clustering_schema["schema"],
                        existing_topics=existing_topics, human_examples=human_examples,
                        granularity=self.granularity,
                    )
                except GatewayError as e:
                    logger.warning(f"分桶 {idx} 分群失敗，暫緩處理: {e.message}")
                    continue

                for t in topics:
                    if t["topic_id"] in existing_topic_ids:
                        # 模型判定屬於既有議題：直接沿用，不進入合併流程
                        for rid in t.get("member_row_ids", []):
                            assigned_to_existing.append(
                                (rid, t["topic_id"], t.get("reason", ""), t.get("confidence", 0.5)))
                    else:
                        # 跨批次整合只看得到名稱＋範例標題，多給兩則讓合併判斷更有依據
                        t["sample_titles"] = [title_lookup.get(rid, "")
                                                for rid in t.get("member_row_ids", [])[:5]]
                        candidate_topics.append(t)

            # ---- 寫回：歸入既有議題的新聞 ----
            existing_updates = []
            for rid, tid, reason, conf in assigned_to_existing:
                existing_updates.append({
                    "row_id": rid, "final_topic_id": tid,
                    "final_topic_name": existing_name_lookup.get(tid, ""),
                    "clustering_reason": reason, "clustering_confidence": conf,
                })
            if existing_updates:
                self.news_repo.update_fields_bulk(existing_updates)

            # ---- 跨批次議題整合（僅新議題候選） ----
            merged_groups = []
            if candidate_topics:
                self.progress.emit(len(buckets), len(buckets), "進行跨批次議題整合...")
                merge_cfg = get_active_prompt(self.prompt_repo, "topic_merge")
                merge_schema = json.loads(merge_cfg.tool_schema_json)
                try:
                    merged_groups = merge_candidate_topics(
                        self.gateway, candidate_topics, merge_cfg.system_prompt,
                        merge_cfg.user_template, merge_schema["name"], merge_schema["schema"],
                        granularity=self.granularity,
                    )
                except GatewayError as e:
                    logger.warning(f"跨批次整合失敗，改用未合併的候選議題: {e.message}")
                    merged_groups = [
                        {"final_topic_name": t["topic_name"], "source_topic_ids": [t["topic_id"]],
                         "reason": ""}
                        for t in candidate_topics
                    ]

            candidate_by_id = {t["topic_id"]: t for t in candidate_topics}
            final_topics: List[Topic] = []
            news_updates = []

            merged_source_ids = set()
            for group in merged_groups:
                src_ids = [s for s in group.get("source_topic_ids", []) if s in candidate_by_id]
                if not src_ids:
                    continue
                final_id = new_id("ftopic_")
                final_name = group["final_topic_name"]
                final_topics.append(Topic(topic_id=final_id, topic_name=final_name))
                for src_id in src_ids:
                    merged_source_ids.add(src_id)
                    src = candidate_by_id[src_id]
                    for rid in src.get("member_row_ids", []):
                        news_updates.append({
                            "row_id": rid, "final_topic_id": final_id,
                            "final_topic_name": final_name,
                            "initial_topic_id": src_id,
                            "initial_topic_name": src.get("topic_name", ""),
                            "clustering_reason": src.get("reason", ""),
                            "clustering_confidence": src.get("confidence", 0.5),
                        })

            # 未被納入任何合併群組的候選議題，各自成為獨立最終議題
            for t in candidate_topics:
                if t["topic_id"] in merged_source_ids:
                    continue
                final_id = new_id("ftopic_")
                final_topics.append(Topic(topic_id=final_id, topic_name=t["topic_name"]))
                for rid in t.get("member_row_ids", []):
                    news_updates.append({
                        "row_id": rid, "final_topic_id": final_id,
                        "final_topic_name": t["topic_name"],
                        "initial_topic_id": t["topic_id"],
                        "initial_topic_name": t["topic_name"],
                        "clustering_reason": t.get("reason", ""),
                        "clustering_confidence": t.get("confidence", 0.5),
                    })

            self.topic_repo.upsert_many(final_topics)
            self.news_repo.update_fields_bulk(news_updates)

            total = len(final_topics) + len({tid for _, tid, _, _ in assigned_to_existing})
            self.finished_ok.emit(total if not self.incremental
                                    else len(final_topics) + len(existing_topic_ids))
        except Exception as e:
            logger.exception("議題分群發生未預期錯誤")
            self.finished_error.emit(str(e))
