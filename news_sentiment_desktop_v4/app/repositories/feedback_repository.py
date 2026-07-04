"""Feedback log / Case / RuleDraft Repository — 對應規格十三"""
from __future__ import annotations

import time
from typing import List, Dict, Any, Optional

from app.models.feedback import FeedbackLogEntry, CaseRecord, RuleDraft
from app.repositories.db import get_connection


class FeedbackRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def add(self, entry: FeedbackLogEntry) -> None:
        d = entry.to_dict()
        with self.conn:
            self.conn.execute(
                "INSERT INTO feedback_log (feedback_id,batch_id,entity_type,entity_id,"
                "ai_original_value,human_final_value,action,reason,operator,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d["feedback_id"], d["batch_id"], d["entity_type"], d["entity_id"],
                 d["ai_original_value"], d["human_final_value"], d["action"], d["reason"],
                 d["operator"], d["created_at"]),
            )

    def list_all(self, entity_type: Optional[str] = None) -> List[FeedbackLogEntry]:
        if entity_type:
            cur = self.conn.execute("SELECT * FROM feedback_log WHERE entity_type=? ORDER BY created_at DESC",
                                     (entity_type,))
        else:
            cur = self.conn.execute("SELECT * FROM feedback_log ORDER BY created_at DESC")
        return [FeedbackLogEntry.from_row(dict(r)) for r in cur.fetchall()]


class CaseRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def add(self, case: CaseRecord) -> None:
        d = case.to_dict()
        with self.conn:
            self.conn.execute(
                "INSERT INTO case_records (case_id,case_type,description,source_feedback_ids,created_at) "
                "VALUES (?,?,?,?,?)",
                (d["case_id"], d["case_type"], d["description"], d["source_feedback_ids"], d["created_at"]),
            )

    def list_all(self) -> List[CaseRecord]:
        cur = self.conn.execute("SELECT * FROM case_records ORDER BY created_at DESC")
        return [CaseRecord.from_row(dict(r)) for r in cur.fetchall()]


class RuleRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def upsert(self, rule: RuleDraft) -> None:
        d = rule.to_dict()
        d["updated_at"] = time.time()
        cols = list(d.keys())
        placeholders = ",".join(["?"] * len(cols))
        update_clause = ",".join([f"{c}=excluded.{c}" for c in cols if c != "rule_id"])
        sql = (f"INSERT INTO rule_drafts ({','.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT(rule_id) DO UPDATE SET {update_clause}")
        with self.conn:
            self.conn.execute(sql, [d[c] for c in cols])

    def update_status(self, rule_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE rule_drafts SET status=?, updated_at=? WHERE rule_id=?",
                               (status, time.time(), rule_id))

    def list_all(self, status: Optional[str] = None) -> List[RuleDraft]:
        if status:
            cur = self.conn.execute("SELECT * FROM rule_drafts WHERE status=? ORDER BY updated_at DESC",
                                     (status,))
        else:
            cur = self.conn.execute("SELECT * FROM rule_drafts ORDER BY updated_at DESC")
        return [RuleDraft.from_row(dict(r)) for r in cur.fetchall()]

    def get(self, rule_id: str) -> Optional[RuleDraft]:
        cur = self.conn.execute("SELECT * FROM rule_drafts WHERE rule_id=?", (rule_id,))
        row = cur.fetchone()
        return RuleDraft.from_row(dict(row)) if row else None
