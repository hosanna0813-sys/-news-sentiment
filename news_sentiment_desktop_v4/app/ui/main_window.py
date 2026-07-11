"""
主視窗 — 對應規格書 三、UI／UX 設計要求

版面：上方工具列 + 左側導覽（工作流程） + 中央工作區（QStackedWidget） + 下方狀態列。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget,
    QToolBar, QStatusBar, QLabel,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from app.controllers.app_context import AppContext
from app.ui.pages import (
    ImportPage, RetentionPage, ScrapingPage, ClusteringPage, TopicAdjustmentPage,
    SummarizationPage, FeedbackRulesPage, PromptTuningPage, ExportPage, SettingsPage,
)

NAV_ITEMS = [
    "1. 匯入新聞", "2. 確認留用", "3. 抓取正文", "4. AI 議題分群", "5. 人工調整議題",
    "6. AI 議題綜整", "7. 回饋與規則草案", "8. Prompt 調校建議", "9. Word 匯出", "10. 系統設定",
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("新聞輿情系統 Desktop V4.3.0")
        self.resize(1600, 960)
        self._build_toolbar()
        self._build_status_bar()   # 必須先建立狀態列，因為 _build_central 內的
        self._build_central()       # setCurrentRow(0) 會立即觸發 _on_nav_changed 使用 status_bar

    def _build_toolbar(self):
        toolbar = QToolBar("主工具列")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        act_refresh = QAction("重新整理", self)
        act_refresh.triggered.connect(self._on_refresh_current_page)
        toolbar.addAction(act_refresh)

        toolbar.addSeparator()
        self.api_status_label = QLabel(self._api_status_text())
        toolbar.addWidget(self.api_status_label)

    def _api_status_text(self) -> str:
        from app.utils.secure_key_store import load_api_key, mask_api_key
        return f"API Key：{mask_api_key(load_api_key())}"

    def _build_central(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(180)
        for label in NAV_ITEMS:
            QListWidgetItem(label, self.nav_list)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        self.import_page = ImportPage(self.ctx)
        self.retention_page = RetentionPage(self.ctx)
        self.scraping_page = ScrapingPage(self.ctx)
        self.clustering_page = ClusteringPage(self.ctx)
        self.topic_adjustment_page = TopicAdjustmentPage(self.ctx)
        self.summarization_page = SummarizationPage(self.ctx)
        self.feedback_rules_page = FeedbackRulesPage(self.ctx)
        self.prompt_tuning_page = PromptTuningPage(self.ctx)
        self.export_page = ExportPage(self.ctx)
        self.settings_page = SettingsPage(self.ctx)

        for page in (self.import_page, self.retention_page, self.scraping_page, self.clustering_page,
                     self.topic_adjustment_page, self.summarization_page, self.feedback_rules_page,
                     self.prompt_tuning_page, self.export_page, self.settings_page):
            self.stack.addWidget(page)

        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav_list.setCurrentRow(0)

    def _build_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就緒")

    def showEvent(self, event):
        super().showEvent(event)
        # 視窗第一次顯示後才檢查可續跑工作（避免建構期間彈出 modal 對話框）
        if not getattr(self, "_resume_checked", False):
            self._resume_checked = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, self._check_resumable_jobs)

    JOB_TYPE_LABELS = {
        "retention": "AI 留用初判", "scraping": "正文抓取",
        "clustering": "議題分群", "summarization": "議題綜整", "stance": "立場分析",
        "prompt_validation": "Prompt 調校驗證",
    }

    def _check_resumable_jobs(self):
        """啟動時偵測上次未完成（可續跑）的工作並主動提示使用者（規格十五）"""
        try:
            resumable = self.ctx.job_repo.list_resumable()
        except Exception:
            return
        if not resumable:
            return
        lines = []
        for job in resumable[:5]:
            label = self.JOB_TYPE_LABELS.get(job.job_type, job.job_type)
            lines.append(f"・{label}：進度 {job.progress_current}/{job.total_items}"
                          f"（成功 {job.success_count}，失敗 {job.failed_count}）")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "偵測到未完成的工作",
            "上次有以下工作尚未完成：\n\n" + "\n".join(lines) +
            "\n\n請前往對應的工作頁面，再次按下執行按鈕即可從中斷處續跑"
            "（已完成的批次不會重做）。")
        self.status_bar.showMessage(f"偵測到 {len(resumable)} 項未完成工作，可續跑")

    def _on_nav_changed(self, index: int):
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        self._on_refresh_current_page()
        self.status_bar.showMessage(NAV_ITEMS[index])

    def _on_refresh_current_page(self):
        page = self.stack.currentWidget()
        if hasattr(page, "reload_data"):
            page.reload_data()
        elif hasattr(page, "refresh_all"):
            page.refresh_all()
        elif hasattr(page, "refresh_topics"):
            page.refresh_topics()
        elif hasattr(page, "refresh_rules"):
            page.refresh_rules()
        elif hasattr(page, "refresh_drafts"):
            page.refresh_drafts()

    def closeEvent(self, event):
        """程式關閉時確保背景瀏覽器（Playwright driver）已收乾淨，避免殘留程序崩潰"""
        try:
            self.scraping_page.shutdown_cleanup()
        except Exception:
            pass
        super().closeEvent(event)
