"""AI 議題分群頁面 — 對應規格書 九（工作流程步驟4）"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar, QListWidget,
)

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.workers.clustering_worker import ClusteringWorker


class ClusteringPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 4：AI 議題分群")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(QLabel("只依據已取得正文的留用新聞進行分群；正文不足者會標記為「正文不足待人工確認」。"))

        toolbar = QHBoxLayout()
        self.btn_start = QPushButton("執行 AI 議題分群")
        mark_primary(self.btn_start)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel = QPushButton("取消")
        mark_danger(self.btn_cancel)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_cancel)
        from PySide6.QtWidgets import QCheckBox
        self.chk_incremental = QCheckBox("增量分群（保留既有議題結構，只分類尚未歸入議題的新聞）")
        toolbar.addWidget(self.chk_incremental)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        root.addWidget(self.progress_label)

        root.addWidget(QLabel("目前議題清單："))
        self.topic_list = QListWidget()
        root.addWidget(self.topic_list, 1)

        self.refresh_topics()

    def _on_start(self):
        self._worker = ClusteringWorker(
            self.ctx.gateway, self.ctx.news_repo, self.ctx.topic_repo, self.ctx.prompt_repo,
            bucket_size=self.ctx.settings.api.batch_size_clustering,
            incremental=self.chk_incremental.isChecked(),
            feedback_repo=self.ctx.feedback_repo,
            keyword_taxonomy=self.ctx.settings.keyword_taxonomy,
            granularity=self.ctx.settings.api.clustering_granularity,
        )
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.finished_error.connect(self._on_finished_error)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.request_cancel()

    def _on_progress(self, current, total, message):
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        self.progress_label.setText(message)

    def _on_finished_ok(self, topic_count: int):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        msg = f"分群完成，共產生 {topic_count} 個議題"
        failed = getattr(self._worker, "failed_buckets", 0)
        if failed:
            msg += f"（注意：{failed} 個分桶的 AI 呼叫失敗被略過，重新執行分群可補跑）"
        self.progress_label.setText(msg)
        self.refresh_topics()

    def _on_finished_error(self, message: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"分群失敗：{message}")

    def refresh_topics(self):
        self.topic_list.clear()
        topics = self.ctx.topic_repo.list_active()
        for t in topics:
            members = self.ctx.news_repo.list_by_topic(t.topic_id)
            self.topic_list.addItem(f"{t.topic_name}（{len(members)} 則）")
        # 已有議題結構時，預設走增量分群保護人工確認成果（可手動取消改全量重分）
        if topics and not self.chk_incremental.isChecked():
            self.chk_incremental.setChecked(True)
