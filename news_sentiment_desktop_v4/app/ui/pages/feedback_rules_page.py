"""回饋與規則草案頁面 — 對應規格書 十三（工作流程步驟7）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QTextEdit, QSplitter, QMessageBox,
)
from PySide6.QtCore import Qt

from app.controllers.app_context import AppContext
from app.workers.rule_draft_worker import RuleDraftWorker

RULE_ID_ROLE = Qt.UserRole + 1


class FeedbackRulesPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._build_ui()
        self.refresh_rules()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 7：回饋與規則草案")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(QLabel("規則草案由 AI 依人工回饋歸納產生，不會自動生效，需人工採用／編輯／停用／刪除。"))

        toolbar = QHBoxLayout()
        self.btn_generate = QPushButton("依目前回饋 log 產生規則草案")
        self.btn_generate.clicked.connect(self._on_generate)
        toolbar.addWidget(self.btn_generate)
        toolbar.addStretch()
        root.addLayout(toolbar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        splitter = QSplitter(Qt.Horizontal)
        self.rule_list = QListWidget()
        self.rule_list.itemClicked.connect(self._on_rule_clicked)
        splitter.addWidget(self.rule_list)

        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        detail_layout.addWidget(self.detail_text)
        btn_row = QHBoxLayout()
        self.btn_adopt = QPushButton("採用")
        self.btn_adopt.clicked.connect(lambda: self._set_status("adopted"))
        self.btn_disable = QPushButton("停用")
        self.btn_disable.clicked.connect(lambda: self._set_status("disabled"))
        self.btn_delete = QPushButton("刪除")
        self.btn_delete.clicked.connect(lambda: self._set_status("deleted"))
        btn_row.addWidget(self.btn_adopt)
        btn_row.addWidget(self.btn_disable)
        btn_row.addWidget(self.btn_delete)
        detail_layout.addLayout(btn_row)
        splitter.addWidget(detail_box)

        root.addWidget(splitter, 1)
        self._current_rule_id = None

    def _on_generate(self):
        entries = self.ctx.feedback_repo.list_all()
        human_entries = [e for e in entries
                          if (e.action or "").startswith("human_")
                          or (e.human_final_value or "").strip()]
        if not human_entries:
            self.status_label.setText(
                "目前沒有人工修正紀錄可供歸納。規則草案是從「人工修正 AI 判斷」的紀錄"
                "學習而來——請先在留用頁調整勾選、或在議題調整頁移動／合併／改名議題，"
                "累積一些修正後再產生規則草案。")
            return
        self._worker = RuleDraftWorker(human_entries, self.ctx.gateway, self.ctx.rule_repo,
                                        self.ctx.prompt_repo)
        self.btn_generate.setEnabled(False)
        self.status_label.setText(f"依 {len(human_entries)} 筆人工修正紀錄生成規則草案中...")
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.finished_error.connect(self._on_finished_error)
        self._worker.start()

    def _on_finished_ok(self, count: int):
        self.btn_generate.setEnabled(True)
        self.status_label.setText(f"已產生 {count} 筆規則草案")
        self.refresh_rules()

    def _on_finished_error(self, message: str):
        self.btn_generate.setEnabled(True)
        self.status_label.setText(f"規則草案生成失敗：{message}")

    def refresh_rules(self):
        self.rule_list.clear()
        for r in self.ctx.rule_repo.list_all():
            li = QListWidgetItem(f"[{r.status}] {r.name}（優先級：{r.priority}）")
            li.setData(RULE_ID_ROLE, r.rule_id)
            self.rule_list.addItem(li)

    def _on_rule_clicked(self, item: QListWidgetItem):
        rule_id = item.data(RULE_ID_ROLE)
        self._current_rule_id = rule_id
        r = self.ctx.rule_repo.get(rule_id)
        if not r:
            return
        text = (f"名稱：{r.name}\n狀態：{r.status}\n版本：{r.version}\n適用範圍：{r.scope}\n\n"
                f"規則內容：\n{r.rule_text}\n\n支持案例數：{r.supporting_case_count}\n"
                f"代表性案例：{r.representative_cases}\n\n風險或例外情況：\n{r.risk_notes}\n\n"
                f"建議優先級：{r.priority}\n產生模型：{r.generated_by_model}")
        self.detail_text.setPlainText(text)

    def _set_status(self, status: str):
        if not self._current_rule_id:
            QMessageBox.information(self, "提示", "請先選擇一筆規則草案")
            return
        self.ctx.rule_repo.update_status(self._current_rule_id, status)
        self.refresh_rules()
