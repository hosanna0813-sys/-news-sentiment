"""ScrapeStatsRepository — 站點爬取成功率統計（V4.2.0，爬取層可靠度儀表板）"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.repositories.db import get_connection


@dataclass
class DomainStat:
    domain: str
    success_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    total_elapsed_sec: float = 0.0
    consecutive_failures: int = 0
    last_status: str = ""
    last_detail: str = ""
    last_success_at: Optional[float] = None
    last_attempt_at: Optional[float] = None

    @property
    def total_attempts(self) -> int:
        return self.success_count + self.fail_count + self.skip_count

    @property
    def success_rate(self) -> float:
        denom = self.success_count + self.fail_count  # 略過(robots等)不列入分母
        return (self.success_count / denom) if denom else 0.0

    @property
    def avg_elapsed_sec(self) -> float:
        return (self.total_elapsed_sec / self.total_attempts) if self.total_attempts else 0.0


class ScrapeStatsRepository:
    def __init__(self, db_path: Optional[Path] = None):
        self.conn = get_connection(db_path)

    def record(self, domain: str, status: str, elapsed_sec: float, detail: str = "") -> None:
        """記錄一次抓取結果。status: 成功 / 失敗 / 可疑 / 略過"""
        now = time.time()
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO scrape_stats (domain) VALUES (?)", (domain,))
            if status == "成功":
                self.conn.execute(
                    "UPDATE scrape_stats SET success_count=success_count+1, "
                    "consecutive_failures=0, last_success_at=?, last_status=?, last_detail=?, "
                    "last_attempt_at=?, total_elapsed_sec=total_elapsed_sec+? WHERE domain=?",
                    (now, status, detail[:300], now, elapsed_sec, domain))
            elif status == "略過":
                self.conn.execute(
                    "UPDATE scrape_stats SET skip_count=skip_count+1, last_status=?, "
                    "last_detail=?, last_attempt_at=?, total_elapsed_sec=total_elapsed_sec+? "
                    "WHERE domain=?",
                    (status, detail[:300], now, elapsed_sec, domain))
            else:  # 失敗 / 可疑
                self.conn.execute(
                    "UPDATE scrape_stats SET fail_count=fail_count+1, "
                    "consecutive_failures=consecutive_failures+1, last_status=?, last_detail=?, "
                    "last_attempt_at=?, total_elapsed_sec=total_elapsed_sec+? WHERE domain=?",
                    (status, detail[:300], now, elapsed_sec, domain))

    def list_all(self) -> List[DomainStat]:
        cur = self.conn.execute(
            "SELECT * FROM scrape_stats ORDER BY (fail_count*1.0)/(success_count+fail_count+0.001) DESC")
        return [DomainStat(**dict(r)) for r in cur.fetchall()]

    def list_alerts(self, threshold: int = 3) -> List[DomainStat]:
        """連續失敗達門檻的站點（可能改版或封鎖，需主動提示）"""
        cur = self.conn.execute(
            "SELECT * FROM scrape_stats WHERE consecutive_failures >= ? "
            "ORDER BY consecutive_failures DESC", (threshold,))
        return [DomainStat(**dict(r)) for r in cur.fetchall()]

    def delete_all(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM scrape_stats")
