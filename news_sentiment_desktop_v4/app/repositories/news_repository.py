"""
News Repository

負責 NewsItem 的 CRUD，並保證：
    - 即使標題/網址/內容完全重複，也不會因 row_id 衝突造成回寫錯誤
      （row_id 為匯入時新產生的 UUID，恆唯一，不依賴來源 news_id）。
    - 所有寫入採 upsert（INSERT ... ON CONFLICT DO UPDATE），支援增量保存。
    - 提供 update_fields()，只更新指定欄位，避免整列覆蓋導致競態遺失資料
      （例如使用者正在編輯 manual_note 時，背景 worker 同時在寫 body_text）。
"""
from __future__ import annotations

import time
from typing import Iterable, Optional, List, Dict, Any

from app.models.news import NewsItem
from app.repositories.db import get_connection

_COLUMNS = list(NewsItem.__dataclass_fields__.keys())


class NewsRepository:
    def __init__(self, db_path=None):
        self.conn = get_connection(db_path)

    # ---------- 寫入 ----------
    def upsert_many(self, items: Iterable[NewsItem]) -> None:
        rows = [it.to_dict() for it in items]
        if not rows:
            return
        cols = _COLUMNS
        placeholders = ",".join(["?"] * len(cols))
        col_list = ",".join(cols)
        update_clause = ",".join([f"{c}=excluded.{c}" for c in cols if c != "row_id"])
        sql = (f"INSERT INTO news ({col_list}) VALUES ({placeholders}) "
               f"ON CONFLICT(row_id) DO UPDATE SET {update_clause}")
        with self.conn:
            self.conn.executemany(sql, [[r[c] for c in cols] for r in rows])

    def upsert_one(self, item: NewsItem) -> None:
        self.upsert_many([item])

    def update_fields(self, row_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = time.time()
        set_clause = ",".join([f"{k}=?" for k in fields.keys()])
        sql = f"UPDATE news SET {set_clause} WHERE row_id=?"
        with self.conn:
            self.conn.execute(sql, list(fields.values()) + [row_id])

    def update_fields_bulk(self, updates: List[Dict[str, Any]]) -> None:
        """updates: [{"row_id": ..., <field>: value, ...}, ...]；每筆欄位可不同，逐筆執行但共用 transaction"""
        with self.conn:
            for u in updates:
                u = dict(u)
                row_id = u.pop("row_id")
                if not u:
                    continue
                u["updated_at"] = time.time()
                set_clause = ",".join([f"{k}=?" for k in u.keys()])
                self.conn.execute(f"UPDATE news SET {set_clause} WHERE row_id=?",
                                   list(u.values()) + [row_id])

    # ---------- 讀取 ----------
    def get(self, row_id: str) -> Optional[NewsItem]:
        cur = self.conn.execute("SELECT * FROM news WHERE row_id=?", (row_id,))
        row = cur.fetchone()
        return NewsItem.from_row(dict(row)) if row else None

    def list_all(self, import_batch_id: Optional[str] = None) -> List[NewsItem]:
        if import_batch_id:
            cur = self.conn.execute("SELECT * FROM news WHERE import_batch_id=? ORDER BY created_at",
                                     (import_batch_id,))
        else:
            cur = self.conn.execute("SELECT * FROM news ORDER BY created_at")
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_by_retention_status(self, status: str) -> List[NewsItem]:
        cur = self.conn.execute("SELECT * FROM news WHERE retention_status=? ORDER BY created_at",
                                 (status,))
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_retained_without_body(self) -> List[NewsItem]:
        cur = self.conn.execute(
            "SELECT * FROM news WHERE retained=1 AND (body_text IS NULL OR body_text='') "
            "AND body_fetch_status NOT IN ('成功')"
        )
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_retained_with_body(self) -> List[NewsItem]:
        cur = self.conn.execute(
            "SELECT * FROM news WHERE retained=1 AND body_text IS NOT NULL AND body_text != ''"
        )
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_human_corrected_since(self, since_ts: float, limit: int) -> List[NewsItem]:
        """Prompt 調校驗證用的「修正樣本」：人工覆核過、且是在指定時間點之後更新的新聞，
        依 updated_at 新到舊排序，上限 limit 筆。"""
        cur = self.conn.execute(
            "SELECT * FROM news WHERE retention_judged_by='human' AND updated_at > ? "
            "ORDER BY updated_at DESC LIMIT ?", (since_ts, limit))
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_boundary_control_sample(self, limit: int) -> List[NewsItem]:
        """Prompt 調校驗證用的「對照樣本」：目前卡在留用門檻邊界、AI 自行判斷（未經人工覆核）、
        視為原本正確排除的新聞——跟人工手動驗證時使用的邊界雜訊抽樣邏輯一致。"""
        cur = self.conn.execute(
            "SELECT * FROM news WHERE priority_stars=2 AND should_respond=0 "
            "AND retention_judged_by != 'human' ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def list_by_topic(self, final_topic_id: str) -> List[NewsItem]:
        cur = self.conn.execute("SELECT * FROM news WHERE final_topic_id=? ORDER BY published_at",
                                 (final_topic_id,))
        return [NewsItem.from_row(dict(r)) for r in cur.fetchall()]

    def count_by_retention_status(self) -> Dict[str, int]:
        cur = self.conn.execute("SELECT retention_status, COUNT(*) c FROM news GROUP BY retention_status")
        return {r["retention_status"]: r["c"] for r in cur.fetchall()}

    def find_potential_duplicates(self) -> Dict[str, List[str]]:
        """以標題(去空白)+來源做簡易重複偵測，回傳 {duplicate_group_id: [row_id,...]}"""
        cur = self.conn.execute("SELECT row_id, title, url FROM news")
        groups: Dict[str, List[str]] = {}
        for r in cur.fetchall():
            key = (r["title"] or "").strip().lower()
            if not key:
                continue
            groups.setdefault(key, []).append(r["row_id"])
        return {k: v for k, v in groups.items() if len(v) > 1}

    # ---------- 清除 ----------
    def delete_all(self) -> int:
        """刪除所有新聞資料，回傳刪除筆數（不影響回饋 log／案例庫／規則庫）"""
        cur = self.conn.execute("SELECT COUNT(*) c FROM news")
        count = cur.fetchone()["c"]
        with self.conn:
            self.conn.execute("DELETE FROM news")
            self.conn.execute("DELETE FROM import_batches")
        return count

    # ---------- 統計（匯入完成摘要，對應規格五） ----------
    def import_summary(self, import_batch_id: str) -> Dict[str, int]:
        cur = self.conn.execute("SELECT * FROM news WHERE import_batch_id=?", (import_batch_id,))
        rows = [dict(r) for r in cur.fetchall()]
        total = len(rows)
        missing_url = sum(1 for r in rows if not r.get("url"))
        has_body = sum(1 for r in rows if r.get("excel_body") or r.get("body_text"))
        summary_only = sum(1 for r in rows if r.get("summary") and not (r.get("excel_body") or r.get("body_text")))
        return {
            "total": total,
            "missing_url": missing_url,
            "has_body": has_body,
            "summary_only": summary_only,
        }
