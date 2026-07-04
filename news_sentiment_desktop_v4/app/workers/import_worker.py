"""ImportWorker — 背景執行 Excel/CSV 匯入，避免大檔案匯入時 GUI 凍結"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.services.importer.excel_importer import import_file, ImportResult
from app.repositories.news_repository import NewsRepository
from app.repositories.db import get_connection
from app.utils.logging_setup import get_logger

logger = get_logger("import_worker")


class ImportWorker(QThread):
    progress = Signal(str)                  # message
    finished_ok = Signal(object)             # ImportResult
    finished_error = Signal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.file_path = file_path

    def run(self) -> None:
        try:
            self.progress.emit("讀取檔案中...")
            result: ImportResult = import_file(self.file_path)
            self.progress.emit(f"寫入資料庫中（{len(result.items)} 筆）...")
            # worker 執行緒需自己的 DB 連線（sqlite3 連線不可跨執行緒共用同一物件）
            repo = NewsRepository(db_path=None)
            repo.conn = get_connection()  # thread-local，會在本執行緒建立新連線
            repo.upsert_many(result.items)
            self.progress.emit("匯入完成")
            self.finished_ok.emit(result)
        except Exception as e:
            logger.exception("匯入失敗")
            self.finished_error.emit(str(e))
