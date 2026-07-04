"""Prompt Repository（十六）與 App 設定 Repository（四、十四、正文抓取設定）"""
from __future__ import annotations

import json
import time
from typing import List, Optional

from app.models.prompt_config import PromptConfig
from app.models.settings import AppSettings
from app.repositories.db import get_connection


class PromptRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def save_new_version(self, cfg: PromptConfig) -> PromptConfig:
        """儲存為新版本（版本號自動遞增），保留歷史版本"""
        cur = self.conn.execute("SELECT MAX(version) v FROM prompts WHERE task=?", (cfg.task,))
        row = cur.fetchone()
        next_version = (row["v"] or 0) + 1
        cfg.version = next_version
        cfg.last_modified_at = time.time()
        with self.conn:
            # 停用同任務其他版本
            self.conn.execute("UPDATE prompts SET enabled=0 WHERE task=?", (cfg.task,))
            self.conn.execute(
                "INSERT INTO prompts (task,version,system_prompt,user_template,tool_schema_json,"
                "enabled,is_default,last_modified_at) VALUES (?,?,?,?,?,?,?,?)",
                (cfg.task, cfg.version, cfg.system_prompt, cfg.user_template, cfg.tool_schema_json,
                 int(cfg.enabled), int(cfg.is_default), cfg.last_modified_at),
            )
        return cfg

    def get_active(self, task: str) -> Optional[PromptConfig]:
        cur = self.conn.execute(
            "SELECT * FROM prompts WHERE task=? AND enabled=1 ORDER BY version DESC LIMIT 1", (task,))
        row = cur.fetchone()
        return PromptConfig.from_row(dict(row)) if row else None

    def list_versions(self, task: str) -> List[PromptConfig]:
        cur = self.conn.execute("SELECT * FROM prompts WHERE task=? ORDER BY version DESC", (task,))
        return [PromptConfig.from_row(dict(r)) for r in cur.fetchall()]

    def activate_version(self, task: str, version: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE prompts SET enabled=0 WHERE task=?", (task,))
            self.conn.execute("UPDATE prompts SET enabled=1 WHERE task=? AND version=?", (task, version))

    def restore_default(self, task: str) -> Optional[PromptConfig]:
        cur = self.conn.execute(
            "SELECT * FROM prompts WHERE task=? AND is_default=1 ORDER BY version ASC LIMIT 1", (task,))
        row = cur.fetchone()
        if not row:
            return None
        default_cfg = PromptConfig.from_row(dict(row))
        # 以「新版本」的形式重新啟用預設內容，維持版本歷史完整
        new_cfg = PromptConfig(task=task, system_prompt=default_cfg.system_prompt,
                                user_template=default_cfg.user_template,
                                tool_schema_json=default_cfg.tool_schema_json, is_default=False)
        return self.save_new_version(new_cfg)

    def ensure_seeded(self, task: str, default_cfg: PromptConfig) -> None:
        """啟動時若該任務尚無任何版本，寫入預設版本（is_default=1，做為還原基準）"""
        existing = self.list_versions(task)
        if existing:
            return
        default_cfg.task = task
        default_cfg.is_default = True
        default_cfg.version = 1
        default_cfg.enabled = True
        default_cfg.last_modified_at = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT INTO prompts (task,version,system_prompt,user_template,tool_schema_json,"
                "enabled,is_default,last_modified_at) VALUES (?,?,?,?,?,?,?,?)",
                (default_cfg.task, default_cfg.version, default_cfg.system_prompt,
                 default_cfg.user_template, default_cfg.tool_schema_json, 1, 1,
                 default_cfg.last_modified_at),
            )


class AppSettingsRepository:
    """儲存非機密系統設定（API Key 除外，見 utils/secure_key_store.py）"""

    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def save(self, settings: AppSettings) -> None:
        value_json = json.dumps(settings.to_dict(), ensure_ascii=False)
        with self.conn:
            self.conn.execute(
                "INSERT INTO app_settings (key, value_json) VALUES ('app_settings', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (value_json,),
            )

    def load(self) -> AppSettings:
        cur = self.conn.execute("SELECT value_json FROM app_settings WHERE key='app_settings'")
        row = cur.fetchone()
        if not row:
            return AppSettings()
        try:
            data = json.loads(row["value_json"])
            settings = AppSettings()
            settings.api.__dict__.update(data.get("api", {}))
            settings.scraping.__dict__.update(data.get("scraping", {}))
            settings.word_export.__dict__.update(data.get("word_export", {}))
            settings.gmail.__dict__.update(data.get("gmail", {}))
            settings.task_models = data.get("task_models", settings.task_models)
            return settings
        except Exception:
            return AppSettings()

