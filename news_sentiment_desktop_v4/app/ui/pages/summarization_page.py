"""議題綜整頁面 — 對應規格書 十一、十二（工作流程步驟6）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar, QListWidget,
    QListWidgetItem, QTextEdit, QSplitter,
)
from PySide6.QtCore import Qt

from app.controllers.app_context import AppContext
from app.workers.topic_analysis_worker import TopicAnalysisWorker

TOPIC_ID_ROLE = Qt.UserRole + 1


class SummarizationPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._build_ui()
        self.refresh_topics()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 6：AI 議題綜整")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        toolbar = QHBoxLayout()
        self.btn_start = QPushButton("執行議題綜整與立場分析")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_cancel)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        root.addWidget(self.progress_label)

        splitter = QSplitter(Qt.Horizontal)
        self.topic_list = QListWidget()
        self.topic_list.itemClicked.connect(self._on_topic_clicked)
        splitter.addWidget(self.topic_list)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        splitter.addWidget(self.detail_text)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    def _on_start(self):
        topics = self.ctx.topic_repo.list_active()
        if not topics:
            self.progress_label.setText("目前沒有議題可綜整，請先完成議題分群")
            return
        self._worker = TopicAnalysisWorker(
            topics, self.ctx.gateway, self.ctx.news_repo, self.ctx.topic_repo,
            self.ctx.stance_repo, self.ctx.prompt_repo,
        )
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(topics))
        self._worker.progress.connect(self._on_progress)
        self._worker.topic_failed.connect(self._on_topic_failed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.request_cancel()

    def _on_progress(self, current, total, message, success, failed):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"{message}　{current}/{total}　成功 {success}　失敗 {failed}")

    def _on_topic_failed(self, topic_id, error_type, error_detail):
        self.progress_label.setText(f"議題 {topic_id} 綜整失敗（{error_type}）：{error_detail}")

    def _on_finished(self, success_count, failed_count):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"綜整完成：成功 {success_count}，失敗 {failed_count}")
        self.refresh_topics()

    def refresh_topics(self):
        self.topic_list.clear()
        for t in self.ctx.topic_repo.list_active():
            status = "✓已綜整" if t.summarized_at else "未綜整"
            li = QListWidgetItem(f"[{status}] {t.topic_name}")
            li.setData(TOPIC_ID_ROLE, t.topic_id)
            self.topic_list.addItem(li)

    def _on_topic_clicked(self, item: QListWidgetItem):
        topic_id = item.data(TOPIC_ID_ROLE)
        t = self.ctx.topic_repo.get(topic_id)
        if not t:
            return
        stances = self.ctx.stance_repo.list_by_topic(topic_id)
        lines = [
            f"【{t.topic_name}】", "",
            f"150字摘要：\n{t.summary_150 or '（尚未綜整）'}", "",
            f"300字摘要：\n{t.summary_300 or ''}", "",
            f"事件發展與關鍵進度：\n{t.development_progress or ''}", "",
            f"核心爭點：\n{t.core_disputes or ''}", "",
            f"主要行動者與發言：\n{t.key_actors or ''}", "",
            f"可能後續影響：\n{t.possible_impact or ''}", "",
        ]
        if t.has_identifiable_stance and stances:
            lines.append("主要論述與立場：")
            for s in stances:
                lines.append(f"　[{s.stance_type}] {s.speaker}（{s.organization}）：{s.claim}")
        else:
            lines.append("（本議題無明確可辨識立場，不顯示立場區塊）")
        self.detail_text.setPlainText("\n".join(lines))
