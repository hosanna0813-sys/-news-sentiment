"""
Prompt Registry

統一管理「程式內建預設 Prompt」與「資料庫中使用者可編輯版本」之間的關係：
    - 啟動時呼叫 seed_defaults()，將本檔案內建的預設 Prompt 寫入 DB（is_default=1），
      做為使用者「還原預設」時的基準，且僅在該任務尚無任何版本時才寫入，
      不會覆蓋使用者已修改的版本。
    - 執行期間透過 get_active_prompt(task) 取得目前啟用版本（若使用者未修改過，
      即為預設版本）。
"""
from __future__ import annotations

from typing import Dict, Any

from app.models.prompt_config import PromptConfig
from app.repositories.settings_repository import PromptRepository
from app.prompts import (
    retention_prompt, clustering_prompt, summarization_prompt, stance_prompt, rule_draft_prompt,
    prompt_tuning_prompt,
)
import json

_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "retention_prefilter": {
        "system_prompt": retention_prompt.PREFILTER_SYSTEM_PROMPT,
        "user_template": retention_prompt.PREFILTER_USER_TEMPLATE,
        "tool_schema": {"name": retention_prompt.PREFILTER_TOOL_NAME,
                         "schema": retention_prompt.PREFILTER_TOOL_SCHEMA},
    },
    "retention_judgement": {
        "system_prompt": retention_prompt.SYSTEM_PROMPT,
        "user_template": retention_prompt.USER_TEMPLATE,
        "tool_schema": {"name": retention_prompt.TOOL_NAME, "schema": retention_prompt.TOOL_SCHEMA},
    },
    "topic_clustering": {
        "system_prompt": clustering_prompt.CLUSTERING_SYSTEM_PROMPT,
        "user_template": clustering_prompt.CLUSTERING_USER_TEMPLATE,
        "tool_schema": {"name": clustering_prompt.CLUSTERING_TOOL_NAME,
                         "schema": clustering_prompt.CLUSTERING_TOOL_SCHEMA},
    },
    "topic_merge": {
        "system_prompt": clustering_prompt.MERGE_SYSTEM_PROMPT,
        "user_template": clustering_prompt.MERGE_USER_TEMPLATE,
        "tool_schema": {"name": clustering_prompt.MERGE_TOOL_NAME,
                         "schema": clustering_prompt.MERGE_TOOL_SCHEMA},
    },
    "topic_naming": {
        # 命名已內含於分群/整合 tool schema 的 topic_name 欄位，此任務保留供未來獨立微調使用
        "system_prompt": "你是新聞議題命名助理，請依「主體＋行動／事件＋核心爭點」格式重新命名議題，避免籠統名稱。",
        "user_template": "議題內容摘要：{topic_context}\n請提供更精準的議題名稱。",
        "tool_schema": {"name": "submit_topic_name",
                         "schema": {"type": "object",
                                    "properties": {"topic_name": {"type": "string"}},
                                    "required": ["topic_name"]}},
    },
    "topic_summarization": {
        "system_prompt": summarization_prompt.SUMMARIZATION_SYSTEM_PROMPT,
        "user_template": summarization_prompt.SUMMARIZATION_USER_TEMPLATE,
        "tool_schema": {"name": summarization_prompt.SUMMARIZATION_TOOL_NAME,
                         "schema": summarization_prompt.SUMMARIZATION_TOOL_SCHEMA},
    },
    "stance_analysis": {
        "system_prompt": stance_prompt.STANCE_SYSTEM_PROMPT,
        "user_template": stance_prompt.STANCE_USER_TEMPLATE,
        "tool_schema": {"name": stance_prompt.STANCE_TOOL_NAME, "schema": stance_prompt.STANCE_TOOL_SCHEMA},
    },
    "rule_draft": {
        "system_prompt": rule_draft_prompt.RULE_DRAFT_SYSTEM_PROMPT,
        "user_template": rule_draft_prompt.RULE_DRAFT_USER_TEMPLATE,
        "tool_schema": {"name": rule_draft_prompt.RULE_DRAFT_TOOL_NAME,
                         "schema": rule_draft_prompt.RULE_DRAFT_TOOL_SCHEMA},
    },
    "prompt_tuning_propose": {
        "system_prompt": prompt_tuning_prompt.PROPOSE_SYSTEM_PROMPT,
        "user_template": prompt_tuning_prompt.PROPOSE_USER_TEMPLATE,
        "tool_schema": {"name": prompt_tuning_prompt.PROPOSE_TOOL_NAME,
                         "schema": prompt_tuning_prompt.PROPOSE_TOOL_SCHEMA},
    },
}


def seed_defaults(repo: PromptRepository) -> None:
    for task, d in _DEFAULTS.items():
        cfg = PromptConfig(
            task=task, system_prompt=d["system_prompt"], user_template=d["user_template"],
            tool_schema_json=json.dumps(d["tool_schema"], ensure_ascii=False),
        )
        repo.ensure_seeded(task, cfg)
        # 預設 Prompt 升級（V4.1.3）：若目前啟用版本「仍是系統預設」（使用者
        # 未曾修改過），且程式內建預設內容已更新，則以新預設寫入為新版本並啟用。
        # 使用者已自行修改過的 Prompt（active 非 is_default）完全不動。
        active = repo.get_active(task)
        if active is not None and active.is_default:
            # 比較須包含 tool_schema_json：schema-only 的預設變更（例如分群 schema
            # 補 topic_id 欄位）也要能升級到既有資料庫，否則永遠傳播不出去
            default_schema_json = json.dumps(d["tool_schema"], ensure_ascii=False)
            if (active.system_prompt != d["system_prompt"]
                    or active.user_template != d["user_template"]
                    or active.tool_schema_json != default_schema_json):
                new_cfg = PromptConfig(
                    task=task, system_prompt=d["system_prompt"],
                    user_template=d["user_template"],
                    tool_schema_json=json.dumps(d["tool_schema"], ensure_ascii=False),
                    is_default=True,
                )
                repo.save_new_version(new_cfg)


def get_active_prompt(repo: PromptRepository, task: str) -> PromptConfig:
    cfg = repo.get_active(task)
    if cfg is not None:
        return cfg
    # 理論上 seed_defaults 已寫入，這裡做為最後防線直接回傳記憶體內建版本
    d = _DEFAULTS[task]
    return PromptConfig(task=task, system_prompt=d["system_prompt"], user_template=d["user_template"],
                         tool_schema_json=json.dumps(d["tool_schema"], ensure_ascii=False), is_default=True)
