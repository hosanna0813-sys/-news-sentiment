"""Word 早報匯出頁面 — 對應規格書 十四（工作流程步驟8）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QCheckBox, QLineEdit,
    QFileDialog, QMessageBox, QComboBox, QSpinBox, QFormLayout, QGroupBox,
)

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.exporters.word_exporter import export_daily_report, export_simple_topic_list
from app.utils.paths import get_exports_dir


class ExportPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 8：Word 早報匯出")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        opt_group = QGroupBox("匯出選項")
        form = QFormLayout(opt_group)
        s = self.ctx.settings.word_export
        self.chk_links = QCheckBox("附新聞連結")
        self.chk_links.setChecked(s.include_news_links)
        self.chk_excerpts = QCheckBox("附正文證據摘錄")
        self.chk_excerpts.setChecked(s.include_body_excerpts)
        self.chk_missing = QCheckBox("輸出未取得正文新聞清單")
        self.chk_missing.setChecked(s.include_missing_body_list)
        form.addRow(self.chk_links)
        form.addRow(self.chk_excerpts)
        form.addRow(self.chk_missing)
        root.addWidget(opt_group)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(get_exports_dir() / "新聞輿情早報.docx"))
        btn_browse = QPushButton("另存為...")
        btn_browse.clicked.connect(self._on_browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(btn_browse)
        root.addLayout(path_row)

        btn_export = QPushButton("匯出 Word 早報")
        mark_primary(btn_export)
        btn_export.clicked.connect(self._on_export)
        root.addWidget(btn_export)

        btn_simple = QPushButton("匯出簡易清單（議題＋標題＋連結）")
        btn_simple.clicked.connect(self._on_export_simple)
        root.addWidget(btn_simple)

        self.status_label = QLabel("")
        root.addWidget(self.status_label)
        root.addStretch()

    def _on_browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "另存新聞輿情早報", self.path_edit.text(),
                                               "Word 文件 (*.docx)")
        if path:
            self.path_edit.setText(path)

    def _on_export(self):
        try:
            topics = self.ctx.topic_repo.list_active()
            if not topics:
                self.status_label.setText("目前沒有議題可匯出，請先完成議題分群與綜整")
                return

            news_by_topic = {t.topic_id: self.ctx.news_repo.list_by_topic(t.topic_id) for t in topics}
            stances_by_topic = {t.topic_id: self.ctx.stance_repo.list_by_topic(t.topic_id) for t in topics}
            missing_body_news = [it for it in self.ctx.news_repo.list_all()
                                  if it.retained and not it.has_body]

            settings = self.ctx.settings.word_export
            settings.include_news_links = self.chk_links.isChecked()
            settings.include_body_excerpts = self.chk_excerpts.isChecked()
            settings.include_missing_body_list = self.chk_missing.isChecked()

            out_path = export_daily_report(
                self.path_edit.text(), topics, news_by_topic, stances_by_topic,
                missing_body_news, settings,
            )
            self.status_label.setText(f"匯出成功：{out_path}")
            QMessageBox.information(self, "匯出成功", f"已輸出至：\n{out_path}")
        except Exception as e:
            self.status_label.setText(f"匯出失敗：{e}")
            QMessageBox.critical(self, "匯出失敗", str(e))

    def _on_export_simple(self):
        """簡易清單：議題名稱分組，底下每則新聞「標題＋連結」，不含摘要等內容"""
        try:
            topics = self.ctx.topic_repo.list_active()
            if not topics:
                self.status_label.setText("目前沒有議題可匯出，請先完成議題分群")
                return
            from datetime import datetime
            default_name = f"新聞議題清單_{datetime.now():%Y%m%d_%H%M}.docx"
            path, _ = QFileDialog.getSaveFileName(
                self, "另存簡易議題清單", str(get_exports_dir() / default_name),
                "Word 文件 (*.docx)")
            if not path:
                return
            news_by_topic = {t.topic_id: self.ctx.news_repo.list_by_topic(t.topic_id)
                              for t in topics}
            out_path = export_simple_topic_list(
                path, topics, news_by_topic, self.ctx.settings.word_export)
            self.status_label.setText(f"匯出成功：{out_path}")
            QMessageBox.information(self, "匯出成功", f"已輸出至：\n{out_path}")
        except Exception as e:
            self.status_label.setText(f"匯出失敗：{e}")
            QMessageBox.critical(self, "匯出失敗", str(e))
