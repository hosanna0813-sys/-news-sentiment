"""匯入新聞頁面 — 對應規格書 五"""
from __future__ import annotations

import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QTextEdit,
    QProgressBar, QGroupBox, QFormLayout, QDialog, QDateTimeEdit, QDialogButtonBox,
)
from PySide6.QtCore import Qt, QDateTime

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.workers.import_worker import ImportWorker
from app.workers.gmail_import_worker import GmailImportWorker

DEFAULT_GMAIL_WINDOW_HOURS = 23


class ImportPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker: ImportWorker | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("步驟 1：匯入新聞")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        btn_row = QHBoxLayout()
        self.btn_pick = QPushButton("選擇 Excel / CSV 檔案...")
        self.btn_pick.clicked.connect(self._on_pick_file)
        btn_row.addWidget(self.btn_pick)
        self.btn_gmail = QPushButton("從 Gmail 擷取（指定時間區間）...")
        mark_primary(self.btn_gmail)
        self.btn_gmail.clicked.connect(self._on_fetch_gmail)
        btn_row.addWidget(self.btn_gmail)
        self.btn_clear = QPushButton("清除已匯入新聞")
        mark_danger(self.btn_clear)
        self.btn_clear.clicked.connect(self._on_clear_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        summary_group = QGroupBox("匯入結果摘要")
        form = QFormLayout(summary_group)
        self.lbl_total = QLabel("-")
        self.lbl_sheets = QLabel("-")
        self.lbl_duplicate = QLabel("-")
        self.lbl_missing_url = QLabel("-")
        self.lbl_has_body = QLabel("-")
        self.lbl_summary_only = QLabel("-")
        form.addRow("實際讀入筆數：", self.lbl_total)
        form.addRow("工作表數：", self.lbl_sheets)
        form.addRow("重複資料數：", self.lbl_duplicate)
        form.addRow("缺少網址數：", self.lbl_missing_url)
        form.addRow("已有正文數：", self.lbl_has_body)
        form.addRow("僅有摘要數：", self.lbl_summary_only)
        layout.addWidget(summary_group)

        col_group = QGroupBox("欄位對應結果")
        col_layout = QVBoxLayout(col_group)
        self.col_mapping_text = QTextEdit()
        self.col_mapping_text.setReadOnly(True)
        self.col_mapping_text.setMaximumHeight(150)
        col_layout.addWidget(self.col_mapping_text)
        layout.addWidget(col_group)

        layout.addStretch()

    def _on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇新聞資料檔案", "", "新聞資料檔案 (*.xlsx *.xlsm *.csv)")
        if not path:
            return
        self._start_import(path)

    def _on_fetch_gmail(self):
        from app.services.gmail.gmail_auth import get_valid_credentials
        if get_valid_credentials() is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "尚未連接 Gmail",
                "請先到「系統設定 → Gmail 匯入設定」完成帳號連接與篩選條件設定。")
            return
        if not self.ctx.settings.gmail.sender_email_filter:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "尚未設定寄件者",
                "請先到「系統設定 → Gmail 匯入設定」填寫寄件者信箱篩選條件。")
            return
        window = self._prompt_gmail_range()
        if window is None:
            return
        start_dt, end_dt = window
        self._start_gmail_import(start_dt, end_dt)

    def _prompt_gmail_range(self):
        """彈出對話框讓使用者指定擷取區間（起訖日期時間），取消回傳 None"""
        dialog = QDialog(self)
        dialog.setWindowTitle("選擇 Gmail 擷取區間")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("將擷取此區間內（含端點），來自同一寄件者的所有符合信件："))

        form = QFormLayout()
        now = QDateTime.currentDateTime()
        start_edit = QDateTimeEdit(now.addSecs(-DEFAULT_GMAIL_WINDOW_HOURS * 3600))
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        end_edit = QDateTimeEdit(now)
        end_edit.setCalendarPopup(True)
        end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow("起始時間：", start_edit)
        form.addRow("結束時間：", end_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None
        return start_edit.dateTime().toPython(), end_edit.dateTime().toPython()

    def _start_gmail_import(self, start_dt: datetime.datetime, end_dt: datetime.datetime):
        self.btn_pick.setEnabled(False)
        self.btn_gmail.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("連接 Gmail 中，請稍候...")

        self._worker = GmailImportWorker(self.ctx.settings.gmail, start_dt, end_dt)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished_ok.connect(self._on_gmail_import_finished)
        self._worker.finished_error.connect(self._on_gmail_import_error)
        self._worker.start()

    def _on_gmail_import_finished(self, result):
        self.btn_gmail.setEnabled(True)
        self._on_import_finished(result)

    def _on_gmail_import_error(self, message: str):
        self.btn_gmail.setEnabled(True)
        self._on_import_error(message)

    def _on_clear_all(self):
        from PySide6.QtWidgets import QMessageBox
        count = len(self.ctx.news_repo.list_all())
        if count == 0:
            self.status_label.setText("目前沒有已匯入的新聞")
            return
        confirm = QMessageBox.question(
            self, "確認清除",
            f"將刪除全部 {count} 則已匯入新聞，以及由其衍生的議題、立場分析結果與工作佇列。\n\n"
            "回饋 log、案例庫、規則庫與 Prompt 設定會保留。\n\n"
            "此動作無法復原，確定要清除嗎？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        deleted = self.ctx.news_repo.delete_all()
        self.ctx.stance_repo.delete_all()
        self.ctx.topic_repo.delete_all()
        self.ctx.batch_repo.delete_all()
        self.ctx.job_repo.delete_all()
        try:
            self.ctx.scrape_stats_repo.delete_all()
        except Exception:
            pass
        self.status_label.setText(f"已清除 {deleted} 則新聞及其衍生資料")
        for lbl in (self.lbl_total, self.lbl_sheets, self.lbl_duplicate,
                     self.lbl_missing_url, self.lbl_has_body, self.lbl_summary_only):
            lbl.setText("-")
        self.col_mapping_text.clear()

    def _start_import(self, path: str):
        self.btn_pick.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("匯入中，請稍候...")

        self._worker = ImportWorker(path)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished_ok.connect(self._on_import_finished)
        self._worker.finished_error.connect(self._on_import_error)
        self._worker.start()

    def _on_import_finished(self, result):
        self.progress_bar.setVisible(False)
        self.btn_pick.setEnabled(True)
        self.status_label.setText(f"匯入完成：{result.file_name}")

        self.lbl_total.setText(str(result.total_rows))
        self.lbl_sheets.setText(str(result.sheet_count))
        self.lbl_duplicate.setText(str(result.duplicate_rows))
        self.lbl_missing_url.setText(str(result.missing_url_rows))
        self.lbl_has_body.setText(str(result.has_body_rows))
        self.lbl_summary_only.setText(str(result.summary_only_rows))

        lines = []
        for sheet, mapping in result.column_mapping_summary.items():
            mapped = ", ".join(f"{k}→{v}" for k, v in mapping.items()) or "（無可辨識欄位）"
            lines.append(f"【{sheet}】{mapped}")
        self.col_mapping_text.setPlainText("\n".join(lines))

    def _on_import_error(self, message: str):
        self.progress_bar.setVisible(False)
        self.btn_pick.setEnabled(True)
        self.status_label.setText(f"匯入失敗：{message}")
