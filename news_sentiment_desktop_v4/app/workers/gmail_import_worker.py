"""GmailAuthWorker / GmailImportWorker — 背景執行 OAuth 授權與 Gmail 匯入，避免 GUI 凍結"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.repositories.news_repository import NewsRepository
from app.repositories.db import get_connection
from app.utils.logging_setup import get_logger

logger = get_logger("gmail_import_worker")


class GmailAuthWorker(QThread):
    """執行 OAuth 同意流程（會開瀏覽器並阻塞等待使用者授權）"""
    finished_ok = Signal()
    finished_error = Signal(str)

    def __init__(self, client_id: str, client_secret: str, parent=None):
        super().__init__(parent)
        self.client_id = client_id
        self.client_secret = client_secret

    def run(self) -> None:
        try:
            from app.services.gmail.gmail_auth import run_oauth_flow
            run_oauth_flow(self.client_id, self.client_secret)
            self.finished_ok.emit()
        except Exception as e:
            logger.exception("Gmail OAuth 授權失敗")
            self.finished_error.emit(str(e))


class GmailImportWorker(QThread):
    progress = Signal(str)                  # message
    finished_ok = Signal(object)             # ImportResult
    finished_error = Signal(str)

    def __init__(self, gmail_settings, start_dt, end_dt, parent=None):
        super().__init__(parent)
        self.gmail_settings = gmail_settings
        self.start_dt = start_dt
        self.end_dt = end_dt

    def run(self) -> None:
        try:
            self.progress.emit("連接 Gmail 中...")
            from app.services.gmail.gmail_importer import import_from_gmail, GmailImportError
            result = import_from_gmail(self.gmail_settings, self.start_dt, self.end_dt,
                                        progress_cb=self.progress.emit)
            self.progress.emit(f"寫入資料庫中（{len(result.items)} 筆）...")
            repo = NewsRepository(db_path=None)
            repo.conn = get_connection()
            # 重新匯入同一封報紙監測報告：補既有列的全文連結、不插入重複列
            from app.services.gmail.gmail_report_parser import repair_newspaper_rows
            to_insert, repaired = repair_newspaper_rows(repo, result.items)
            repo.upsert_many(to_insert)
            if repaired:
                self.progress.emit(f"已為 {repaired} 則既有報紙新聞補上全文連結（未新增重複列）")
            self.progress.emit("匯入完成")
            self.finished_ok.emit(result)
        except Exception as e:
            logger.exception("Gmail 匯入失敗")
            self.finished_error.emit(str(e))
