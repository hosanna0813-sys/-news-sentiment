"""RuleDraftWorker — 對應規格十三：AI 依回饋提出規則草案（不自動啟用）"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QThread, Signal

from app.models.feedback import FeedbackLogEntry
from app.repositories.feedback_repository import RuleRepository
from app.repositories.settings_repository import PromptRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.feedback.feedback_service import generate_rule_drafts
from app.prompts.registry import get_active_prompt
from app.utils.logging_setup import get_logger
import json

logger = get_logger("rule_draft_worker")


class RuleDraftWorker(QThread):
    finished_ok = Signal(int)
    finished_error = Signal(str)

    def __init__(self, feedback_entries: List[FeedbackLogEntry], gateway: ModelGateway,
                 rule_repo: RuleRepository, prompt_repo: PromptRepository, parent=None):
        super().__init__(parent)
        self.feedback_entries = feedback_entries
        self.gateway = gateway
        self.rule_repo = rule_repo
        self.prompt_repo = prompt_repo

    def run(self) -> None:
        try:
            cfg = get_active_prompt(self.prompt_repo, "rule_draft")
            schema = json.loads(cfg.tool_schema_json)
            drafts = generate_rule_drafts(
                self.gateway, self.feedback_entries, cfg.system_prompt, cfg.user_template,
                schema["name"], schema["schema"],
            )
            for d in drafts:
                self.rule_repo.upsert(d)
            self.finished_ok.emit(len(drafts))
        except GatewayError as e:
            self.finished_error.emit(f"{e.error_type}: {e.message}")
        except Exception as e:
            logger.exception("規則草案生成失敗")
            self.finished_error.emit(str(e))
