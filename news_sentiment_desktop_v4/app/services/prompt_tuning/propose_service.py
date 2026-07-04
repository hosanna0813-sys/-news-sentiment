"""Prompt 調校建議 — 第一步：讀取近期人工留用修正紀錄，AI 提出文字改良提案

只呼叫一次 API（非批次），提案內容只允許修改 SYSTEM_PROMPT / USER_TEMPLATE 的文字內容，
不涉及結構化輸出欄位變動（那類改動需要同步改 NewsItem/DB/worker，不是純文字提案能安全做的）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.news_repository import NewsRepository
from app.repositories.prompt_tuning_repository import PromptTuningRepository
from app.repositories.settings_repository import PromptRepository
from app.models.prompt_tuning import PromptTuningDraft
from app.services.ai.model_gateway import ModelGateway
from app.prompts.registry import get_active_prompt
from app.utils.text_utils import new_id
from app.utils.logging_setup import get_logger

logger = get_logger("prompt_tuning_propose_service")

MIN_NEW_CORRECTIONS = 5          # 距上次提案後新修正數不足就不呼叫 API（省錢防呆）
MAX_PROPOSE_CORRECTIONS = 30     # 給 AI 看的修正案例上限（比少樣本用的 10 筆更寬鬆，方便看出模式）

REQUIRED_PLACEHOLDERS = ("{human_examples_section}", "{news_batch_json}")


class TooFewCorrectionsError(Exception):
    def __init__(self, count: int):
        super().__init__(f"距上次提案後只累積 {count} 筆人工修正，未達最低門檻 {MIN_NEW_CORRECTIONS} 筆")
        self.count = count


class ProposalRejectedError(Exception):
    """模型回傳的提案未通過服務端防呆檢查（例如遺漏必要佔位符）"""


def count_new_corrections_since(feedback_repo: FeedbackRepository, since_ts: float) -> int:
    entries = feedback_repo.list_all(entity_type="retention")
    return sum(1 for e in entries
               if (e.action or "").startswith("human_") and e.created_at > since_ts)


def build_correction_payload(feedback_repo: FeedbackRepository, news_repo: NewsRepository,
                              max_examples: int = MAX_PROPOSE_CORRECTIONS) -> List[Dict[str, Any]]:
    """篩選邏輯比照 retention_worker._build_retention_human_examples()，但回傳原始 dict 清單
    （給 JSON payload 用，不是格式化文字），上限比少樣本的 10 筆更寬鬆。"""
    entries = feedback_repo.list_all(entity_type="retention")
    payload: List[Dict[str, Any]] = []
    for e in entries:
        if not (e.action or "").startswith("human_"):
            continue
        if not (e.human_final_value or "").strip():
            continue
        it = news_repo.get(e.entity_id)
        if it is None:
            continue
        payload.append({
            "title": it.title[:60],
            "ai_original_value": e.ai_original_value or "",
            "human_final_value": e.human_final_value,
            "ai_priority_stars": it.priority_stars,
        })
        if len(payload) >= max_examples:
            break
    return payload


def generate_prompt_tuning_proposal(gateway: ModelGateway, prompt_repo: PromptRepository,
                                     feedback_repo: FeedbackRepository, news_repo: NewsRepository,
                                     tuning_repo: PromptTuningRepository) -> PromptTuningDraft:
    since_ts = tuning_repo.latest_created_at_for_task("retention_judgement")
    new_count = count_new_corrections_since(feedback_repo, since_ts)
    if new_count < MIN_NEW_CORRECTIONS:
        raise TooFewCorrectionsError(new_count)

    current_cfg = get_active_prompt(prompt_repo, "retention_judgement")
    propose_cfg = get_active_prompt(prompt_repo, "prompt_tuning_propose")
    propose_schema_obj = json.loads(propose_cfg.tool_schema_json)

    payload = build_correction_payload(feedback_repo, news_repo)
    user_content = propose_cfg.user_template.format(
        current_version=current_cfg.version,
        current_system_prompt=current_cfg.system_prompt,
        current_user_template=current_cfg.user_template,
        correction_batch_json=json.dumps(payload, ensure_ascii=False),
    )

    result = gateway.call_with_tool(
        task="prompt_tuning_propose",
        system_prompt=propose_cfg.system_prompt,
        user_content=user_content,
        tool_name=propose_schema_obj["name"],
        tool_schema=propose_schema_obj["schema"],
    )
    data = result.data if isinstance(result.data, dict) else {}
    proposed_system_prompt = str(data.get("proposed_system_prompt", "")).strip()
    proposed_user_template = str(data.get("proposed_user_template", "")).strip()
    rationale = str(data.get("rationale", "")).strip()

    missing = [p for p in REQUIRED_PLACEHOLDERS if p not in proposed_user_template]
    if not proposed_system_prompt or not proposed_user_template or missing:
        raise ProposalRejectedError(
            f"提案未通過防呆檢查（缺少必要佔位符：{missing}，或內容為空），已捨棄不存檔")

    draft = PromptTuningDraft(
        draft_id=new_id("pt_"),
        task="retention_judgement",
        based_on_version=current_cfg.version,
        proposed_system_prompt=proposed_system_prompt,
        proposed_user_template=proposed_user_template,
        rationale=rationale,
        status="待驗證",
        generated_by_model=result.model_used,
        correction_count_used=len(payload),
    )
    tuning_repo.upsert(draft)
    return draft
