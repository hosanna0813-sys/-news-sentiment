"""Prompt 調校建議草案 Repository"""
from __future__ import annotations

import time
from typing import List, Optional

from app.models.prompt_tuning import PromptTuningDraft
from app.repositories.db import get_connection


class PromptTuningRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def upsert(self, draft: PromptTuningDraft) -> None:
        d = draft.to_dict()
        d["updated_at"] = time.time()
        cols = list(d.keys())
        placeholders = ",".join(["?"] * len(cols))
        update_clause = ",".join([f"{c}=excluded.{c}" for c in cols if c != "draft_id"])
        sql = (f"INSERT INTO prompt_tuning_drafts ({','.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT(draft_id) DO UPDATE SET {update_clause}")
        with self.conn:
            self.conn.execute(sql, [d[c] for c in cols])

    def update_status(self, draft_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE prompt_tuning_drafts SET status=?, updated_at=? WHERE draft_id=?",
                               (status, time.time(), draft_id))

    def update_validation_result(self, draft_id: str, status: str, metrics_json: str) -> None:
        """驗證 worker 執行完成後呼叫（成功 -> 已驗證 / 失敗 -> 驗證失敗）"""
        with self.conn:
            self.conn.execute(
                "UPDATE prompt_tuning_drafts SET status=?, validation_metrics_json=?, updated_at=? "
                "WHERE draft_id=?",
                (status, metrics_json, time.time(), draft_id))

    def list_all(self, status: Optional[str] = None) -> List[PromptTuningDraft]:
        if status:
            cur = self.conn.execute(
                "SELECT * FROM prompt_tuning_drafts WHERE status=? ORDER BY updated_at DESC", (status,))
        else:
            cur = self.conn.execute("SELECT * FROM prompt_tuning_drafts ORDER BY updated_at DESC")
        return [PromptTuningDraft.from_row(dict(r)) for r in cur.fetchall()]

    def get(self, draft_id: str) -> Optional[PromptTuningDraft]:
        cur = self.conn.execute("SELECT * FROM prompt_tuning_drafts WHERE draft_id=?", (draft_id,))
        row = cur.fetchone()
        return PromptTuningDraft.from_row(dict(row)) if row else None

    def latest_created_at_for_task(self, task: str) -> float:
        """距上次為此任務產生提案已經過了多久——提案防呆（新修正數不足）用"""
        cur = self.conn.execute(
            "SELECT MAX(created_at) c FROM prompt_tuning_drafts WHERE task=?", (task,))
        row = cur.fetchone()
        return row["c"] or 0.0
