"""PromptTuningProposeWorker — 對應 Prompt 調校建議第一步：讀取近期人工修正紀錄產生提案（單次 API 呼叫）"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.news_repository import NewsRepository
from app.repositories.prompt_tuning_repository import PromptTuningRepository
from app.repositories.settings_repository import PromptRepository
from app.services.ai.model_gateway import ModelGateway, GatewayError
from app.services.prompt_tuning.propose_service import (
    generate_prompt_tuning_proposal, TooFewCorrectionsError, ProposalRejectedError,
)
from app.utils.logging_setup import get_logger

logger = get_logger("prompt_tuning_propose_worker")


class PromptTuningProposeWorker(QThread):
    finished_ok = Signal(str)       # draft_id
    finished_error = Signal(str)
    finished_too_few = Signal(int)  # 距上次提案累積的新修正數（未達門檻）

    def __init__(self, gateway: ModelGateway, prompt_repo: PromptRepository,
                 feedback_repo: FeedbackRepository, news_repo: NewsRepository,
                 tuning_repo: PromptTuningRepository, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.prompt_repo = prompt_repo
        self.feedback_repo = feedback_repo
        self.news_repo = news_repo
        self.tuning_repo = tuning_repo

    def run(self) -> None:
        try:
            draft = generate_prompt_tuning_proposal(
                self.gateway, self.prompt_repo, self.feedback_repo, self.news_repo, self.tuning_repo,
            )
            self.finished_ok.emit(draft.draft_id)
        except TooFewCorrectionsError as e:
            self.finished_too_few.emit(e.count)
        except ProposalRejectedError as e:
            self.finished_error.emit(str(e))
        except GatewayError as e:
            self.finished_error.emit(f"{e.error_type}: {e.message}")
        except Exception as e:
            logger.exception("Prompt 調校建議生成失敗")
            self.finished_error.emit(str(e))
