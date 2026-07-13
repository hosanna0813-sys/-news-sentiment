"""正文抓取頁面 — 對應規格書 八"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView,
)

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.workers.scraping_worker import build_scraping_worker
from app.services.scraping.body_scraper import BodyScraper


class ScrapingPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._active_browser = None
        self._build_ui()

    def shutdown_cleanup(self):
        """程式退出前的最後防線：確保 Playwright 瀏覽器與 driver 已關閉"""
        if self._worker is not None:
            try:
                self._worker.request_cancel()
                self._worker.wait(3000)
            except Exception:
                pass
        if self._active_browser is not None:
            try:
                self._active_browser.close()
            except Exception:
                pass
            self._active_browser = None

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 3：抓取正文")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(QLabel("僅抓取已留用新聞的正文；已有 Excel 正文或已成功抓取者不會重複抓取。"))

        toolbar = QHBoxLayout()
        self.btn_start = QPushButton("抓取已留用新聞正文")
        mark_primary(self.btn_start)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel = QPushButton("取消")
        mark_danger(self.btn_cancel)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_retry_failed = QPushButton("重試失敗項目")
        self.btn_retry_failed.clicked.connect(self._on_retry_failed)
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_cancel)
        toolbar.addWidget(self.btn_retry_failed)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        root.addWidget(self.progress_label)

        self.table = QTableWidget(0, 4)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(["標題", "來源", "抓取狀態", "詳細原因"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        # 站點成功率儀表板（V4.2.0）
        self.stats_table = QTableWidget(0, 7)
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.setHorizontalHeaderLabels(
            ["站點", "成功率", "成功", "失敗", "略過", "平均耗時(秒)", "最後成功時間"])
        self.stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.alert_label = QLabel("")
        self.alert_label.setObjectName("alertLabel")
        self.alert_label.setWordWrap(True)

        from PySide6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tab_results = QWidget()
        lay1 = QVBoxLayout(tab_results)
        lay1.setContentsMargins(0, 4, 0, 0)
        lay1.addWidget(self.table)
        tabs.addTab(tab_results, "本次抓取結果")
        tab_stats = QWidget()
        lay2 = QVBoxLayout(tab_stats)
        lay2.setContentsMargins(0, 4, 0, 0)
        lay2.addWidget(self.alert_label)
        lay2.addWidget(self.stats_table)
        tabs.addTab(tab_stats, "站點成功率儀表板")
        root.addWidget(tabs, 1)
        self._refresh_stats()

    def _on_start(self):
        items = self.ctx.news_repo.list_retained_without_body()
        if not items:
            self.progress_label.setText("目前沒有需要抓取正文的留用新聞")
            return
        self._run_worker(items, self._make_scraper())

    def _on_retry_failed(self):
        items = [it for it in self.ctx.news_repo.list_all() if it.body_fetch_status == "失敗"]
        if not items:
            self.progress_label.setText("目前沒有失敗項目可重試")
            return
        for it in items:
            it.body_fetch_status = "未抓取"
        self._run_worker(items, self._make_scraper())

    def _make_scraper(self) -> BodyScraper:
        s = self.ctx.settings.scraping
        return BodyScraper(
            per_domain_delay_sec=s.per_domain_delay_sec,
            timeout_sec=s.request_timeout_sec,
            user_agent=s.user_agent,
            respect_robots_txt=s.respect_robots_txt,
            verify_ssl=getattr(s, "verify_ssl", True),
            site_selectors=getattr(s, "site_selectors", {}) or {},
        )

    def _run_worker(self, items, scraper):
        self.table.setRowCount(0)
        browser_factory = None
        s = self.ctx.settings.scraping
        if getattr(s, "use_browser_rendering", False):
            def browser_factory():
                from app.services.scraping.playwright_scraper import PlaywrightScraper
                ps = PlaywrightScraper(
                    per_domain_delay_sec=s.per_domain_delay_sec,
                    timeout_sec=getattr(s, "browser_timeout_sec", 45),
                    respect_robots_txt=s.respect_robots_txt,
                    gne_noise_nodes=getattr(s, "gne_noise_nodes", {}) or {},
                )
                ps.start()
                self._active_browser = ps  # 記錄實例，供程式退出時最後防線清理
                return ps
        self._worker = build_scraping_worker(
            items, scraper, self.ctx.job_repo, self.ctx.batch_repo,
            browser_scraper_factory=browser_factory,
            stats_repo=self.ctx.scrape_stats_repo,
        )
        self._worker.finished_job.connect(lambda *a: setattr(self, "_active_browser", None))
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(items))
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_job.connect(self._on_finished)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.request_cancel()

    def _on_progress(self, job_id, job_type, current, total, success, failed, skipped, message):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(
            f"{message}　{current}/{total}　成功 {success}　失敗 {failed}　略過 {skipped}")
        self._refresh_table()

    def _refresh_table(self):
        items = [it for it in self.ctx.news_repo.list_all()
                 if it.retained and it.body_fetch_status in ("成功", "失敗", "略過")]
        self.table.setRowCount(len(items))
        for row, it in enumerate(items):
            self.table.setItem(row, 0, QTableWidgetItem(it.title))
            self.table.setItem(row, 1, QTableWidgetItem(it.source))
            self.table.setItem(row, 2, QTableWidgetItem(it.body_fetch_status))
            self.table.setItem(row, 3, QTableWidgetItem(it.body_fetch_detail))

    def _refresh_stats(self):
        """更新站點成功率儀表板與連續失敗警示"""
        from datetime import datetime
        from PySide6.QtGui import QColor, QBrush
        try:
            stats = self.ctx.scrape_stats_repo.list_all()
            alerts = self.ctx.scrape_stats_repo.list_alerts(threshold=3)
        except Exception:
            return
        self.stats_table.setRowCount(len(stats))
        for row, s in enumerate(stats):
            last_ok = (datetime.fromtimestamp(s.last_success_at).strftime("%m/%d %H:%M")
                        if s.last_success_at else "從未成功")
            cells = [s.domain, f"{s.success_rate:.0%}", str(s.success_count),
                      str(s.fail_count), str(s.skip_count), f"{s.avg_elapsed_sec:.1f}", last_ok]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if s.consecutive_failures >= 3:
                    item.setBackground(QBrush(QColor(255, 205, 210)))  # 淡紅：需注意
                self.stats_table.setItem(row, col, item)
        if alerts:
            names = "、".join(f"{a.domain}（連續失敗 {a.consecutive_failures} 次，"
                                f"最後狀態：{a.last_status}）" for a in alerts[:5])
            self.alert_label.setText(
                f"⚠ 以下站點連續失敗，可能已改版或封鎖，請檢查：{names}")
        else:
            self.alert_label.setText("")

    def _on_finished(self, job_id, status):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"正文抓取工作結束，狀態：{status}")
        self._refresh_table()
        self._refresh_stats()
