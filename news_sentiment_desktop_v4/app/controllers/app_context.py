"""
AppContext — 應用程式組裝根 (Composition Root)

集中建立所有 Repository 與 ModelGateway，避免各 UI 頁面各自建立連線或
散落直接呼叫 Anthropic API（規格四要求所有 AI 請求須經由單一 ModelGateway）。
"""
from __future__ import annotations

from typing import Dict, Any

from app.repositories.db import init_db, get_connection
from app.repositories.news_repository import NewsRepository
from app.repositories.topic_repository import TopicRepository, StanceRepository
from app.repositories.feedback_repository import FeedbackRepository, CaseRepository, RuleRepository
from app.repositories.job_repository import JobRepository, BatchRepository
from app.repositories.settings_repository import PromptRepository, AppSettingsRepository
from app.repositories.prompt_tuning_repository import PromptTuningRepository
from app.repositories.scrape_stats_repository import ScrapeStatsRepository
from app.services.ai.model_gateway import ModelGateway
from app.utils.secure_key_store import load_api_key, load_openai_api_key
from app.utils.logging_setup import setup_logging, get_logger
from app.prompts.registry import seed_defaults


class AppContext:
    def __init__(self, debug: bool = False):
        setup_logging(debug=debug)
        self.logger = get_logger("app_context")
        init_db()

        self.news_repo = NewsRepository()
        self.topic_repo = TopicRepository()
        self.stance_repo = StanceRepository()
        self.feedback_repo = FeedbackRepository()
        self.case_repo = CaseRepository()
        self.rule_repo = RuleRepository()
        self.job_repo = JobRepository()
        self.batch_repo = BatchRepository()
        self.prompt_repo = PromptRepository()
        self.prompt_tuning_repo = PromptTuningRepository()
        self.settings_repo = AppSettingsRepository()
        self.scrape_stats_repo = ScrapeStatsRepository()

        seed_defaults(self.prompt_repo)

        self.settings = self.settings_repo.load()

        self.gateway = self._build_gateway()
        self.logger.info(f"AppContext 初始化完成（AI 供應商：{self.settings.api.provider}）")

    def _build_gateway(self):
        """依 settings.api.provider 建立對應供應商的閘道（介面相同，服務層無感）"""
        api = self.settings.api
        if api.provider == "openai":
            from app.services.ai.openai_gateway import OpenAIGateway
            return OpenAIGateway(
                api_key_provider=load_openai_api_key,
                task_model_lookup=self._task_model_lookup,
                default_model=api.openai_default_model,
                request_timeout_sec=api.request_timeout_sec,
                max_retries=api.max_retries,
                retry_backoff_base_sec=api.retry_backoff_base_sec,
            )
        return ModelGateway(
            api_key_provider=load_api_key,
            task_model_lookup=self._task_model_lookup,
            request_timeout_sec=api.request_timeout_sec,
            max_retries=api.max_retries,
            retry_backoff_base_sec=api.retry_backoff_base_sec,
            default_model=api.default_model,
        )

    def _task_model_lookup(self, task: str) -> Dict[str, Any]:
        for m in self.settings.task_models:
            if m.get("task") == task:
                return m
        # 找不到設定時的保守預設
        return {"task": task, "model_id": "claude-sonnet-5", "max_tokens": 4096, "temperature": 0.3,
                "use_extended_thinking": False, "use_message_batches": False}

    def reload_settings(self) -> None:
        self.settings = self.settings_repo.load()
        # 供應商或 OpenAI 預設模型可能改變：直接重建閘道（成本極低）。
        # 進行中的 worker 持有舊閘道參照會把該批跑完，新工作使用新閘道。
        self.gateway = self._build_gateway()

    def save_settings(self) -> None:
        self.settings_repo.save(self.settings)
        self.reload_settings()
