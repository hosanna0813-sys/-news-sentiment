"""Topic / Stance Repository"""
from __future__ import annotations

import time
from typing import List, Optional, Dict, Any

from app.models.topic import Topic, Stance
from app.repositories.db import get_connection

_TOPIC_COLS = list(Topic.__dataclass_fields__.keys())
_STANCE_COLS = list(Stance.__dataclass_fields__.keys())


class TopicRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def upsert_many(self, topics: List[Topic]) -> None:
        if not topics:
            return
        cols = _TOPIC_COLS
        placeholders = ",".join(["?"] * len(cols))
        col_list = ",".join(cols)
        update_clause = ",".join([f"{c}=excluded.{c}" for c in cols if c != "topic_id"])
        sql = (f"INSERT INTO topics ({col_list}) VALUES ({placeholders}) "
               f"ON CONFLICT(topic_id) DO UPDATE SET {update_clause}")
        with self.conn:
            self.conn.executemany(sql, [[t.to_dict()[c] for c in cols] for t in topics])

    def upsert_one(self, topic: Topic) -> None:
        self.upsert_many([topic])

    def update_fields(self, topic_id: str, fields: Dict[str, Any]) -> None:
        fields = dict(fields)
        fields["updated_at"] = time.time()
        set_clause = ",".join([f"{k}=?" for k in fields.keys()])
        with self.conn:
            self.conn.execute(f"UPDATE topics SET {set_clause} WHERE topic_id=?",
                               list(fields.values()) + [topic_id])

    def get(self, topic_id: str) -> Optional[Topic]:
        cur = self.conn.execute("SELECT * FROM topics WHERE topic_id=?", (topic_id,))
        row = cur.fetchone()
        return Topic.from_row(dict(row)) if row else None

    def list_active(self) -> List[Topic]:
        # 排序：人工排過序的（display_order 1..N）在前、依序排列；
        # 尚未排序的（0，例如排序後才新增的議題）依建立時間附在後面
        cur = self.conn.execute(
            "SELECT * FROM topics WHERE status='active' "
            "ORDER BY CASE WHEN display_order > 0 THEN 0 ELSE 1 END, display_order, created_at")
        return [Topic.from_row(dict(r)) for r in cur.fetchall()]

    def save_display_order(self, ordered_topic_ids: List[str]) -> None:
        """把使用者拖曳後的順序（清單由上到下）寫回：依序賦予 1..N。
        Word 匯出的議題編號、各頁議題清單都會跟著這個順序。"""
        with self.conn:
            for order, topic_id in enumerate(ordered_topic_ids, start=1):
                self.conn.execute(
                    "UPDATE topics SET display_order=?, updated_at=? WHERE topic_id=?",
                    (order, time.time(), topic_id))

    def delete(self, topic_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE topics SET status='deleted' WHERE topic_id=?", (topic_id,))

    def mark_merged(self, topic_id: str, merged_into: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE topics SET status='merged', merged_into=? WHERE topic_id=?",
                               (merged_into, topic_id))

    def delete_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM topics")


class StanceRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    def upsert_many(self, stances: List[Stance]) -> None:
        if not stances:
            return
        cols = _STANCE_COLS
        placeholders = ",".join(["?"] * len(cols))
        col_list = ",".join(cols)
        update_clause = ",".join([f"{c}=excluded.{c}" for c in cols if c != "stance_id"])
        sql = (f"INSERT INTO stances ({col_list}) VALUES ({placeholders}) "
               f"ON CONFLICT(stance_id) DO UPDATE SET {update_clause}")
        with self.conn:
            self.conn.executemany(sql, [[s.to_dict()[c] for c in cols] for s in stances])

    def list_by_topic(self, topic_id: str) -> List[Stance]:
        cur = self.conn.execute("SELECT * FROM stances WHERE topic_id=?", (topic_id,))
        return [Stance.from_row(dict(r)) for r in cur.fetchall()]

    def delete_by_topic(self, topic_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM stances WHERE topic_id=?", (topic_id,))

    def delete_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM stances")
