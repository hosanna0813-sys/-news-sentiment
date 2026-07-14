"""新聞留用介面 — 對應規格書 七"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox,
    QTableView, QSplitter, QTextEdit, QGroupBox, QFormLayout, QProgressBar, QHeaderView,
    QAbstractItemView, QCheckBox, QScrollArea,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.ui.widgets.news_table_model import NewsTableModel, COLUMNS
from app.workers.retention_worker import build_retention_worker
from app.services.retention.retention_service import apply_human_retention_override

FILTER_OPTIONS = ["全部", "已留用", "AI建議不留用", "人工不留用", "待確認", "需回應", "正文已取得", "正文未取得"]
SORT_OPTIONS = ["時間", "來源", "優先級", "重複群組"]


class RetentionPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self._all_items = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("步驟 2：確認留用")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("重新整理清單")
        self.btn_refresh.clicked.connect(self.reload_data)
        self.btn_ai_judge = QPushButton("執行 AI 留用初判")
        mark_primary(self.btn_ai_judge)
        self.btn_ai_judge.clicked.connect(self._on_ai_judge)
        self.btn_retry_failed = QPushButton("重試失敗批次")
        self.btn_retry_failed.clicked.connect(self._on_retry_failed_batches)
        self.btn_cancel_job = QPushButton("取消")
        mark_danger(self.btn_cancel_job)
        self.btn_cancel_job.setEnabled(False)
        self.btn_cancel_job.clicked.connect(self._on_cancel_job)
        # 批次留用/不留用：配合多選（Shift/Ctrl 點選）一次處理多列
        self.btn_bulk_retain = QPushButton("✔ 留用選取列")
        self.btn_bulk_retain.clicked.connect(lambda: self._set_retained_for_selection(True))
        self.btn_bulk_unretain = QPushButton("✘ 不留用選取列")
        self.btn_bulk_unretain.clicked.connect(lambda: self._set_retained_for_selection(False))
        toolbar.addWidget(self.btn_refresh)
        toolbar.addWidget(self.btn_bulk_retain)
        toolbar.addWidget(self.btn_bulk_unretain)
        toolbar.addWidget(self.btn_ai_judge)
        toolbar.addWidget(self.btn_retry_failed)
        toolbar.addWidget(self.btn_cancel_job)
        toolbar.addStretch()
        root.addLayout(toolbar)

        hint = QLabel("提示：點「留用」欄的整個格子即可切換，不必點中小方塊；"
                       "按住 Shift／Ctrl 可多選列，再按上方按鈕或空白鍵批次切換。")
        hint.setObjectName("hintLabel")
        root.addWidget(hint)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        root.addWidget(self.progress_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("篩選："))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(FILTER_OPTIONS)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_combo)

        filter_row.addWidget(QLabel("搜尋："))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("標題、來源、關鍵字")
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_edit)

        filter_row.addWidget(QLabel("排序："))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_OPTIONS)
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.sort_combo)
        root.addLayout(filter_row)

        splitter = QSplitter(Qt.Horizontal)

        self.table_view = QTableView()
        self.table_view.setAlternatingRowColors(True)
        self.model = NewsTableModel([])
        self.table_view.setModel(self.model)
        header = self.table_view.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(
                col, QHeaderView.Stretch if col == 1 else QHeaderView.ResizeToContents)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # 列高加大：勾選框與文字更好點、更好讀（原本用預設列高偏擠）
        self.table_view.verticalHeader().setDefaultSectionSize(34)
        # 點「留用」欄整格即切換——取代 Qt 原生「必須點中勾選框小方塊」的行為
        self.table_view.clicked.connect(self._on_table_clicked)
        # 空白鍵：批次切換所有選取列（以焦點列切換後的新值為準）
        from PySide6.QtGui import QShortcut, QKeySequence
        space = QShortcut(QKeySequence(Qt.Key_Space), self.table_view)
        space.setContext(Qt.WidgetShortcut)
        space.activated.connect(self._on_space_toggle)
        sel_model = self.table_view.selectionModel()
        if sel_model:
            sel_model.currentRowChanged.connect(self._on_row_selected)
        self.model.retention_toggled.connect(self._on_retention_toggled)
        splitter.addWidget(self.table_view)

        self.preview_panel = self._build_preview_panel()
        splitter.addWidget(self.preview_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root.addWidget(splitter, 1)

    def _build_preview_panel(self) -> QWidget:
        panel = QGroupBox("新聞預覽")
        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        layout = QVBoxLayout(content)

        form = QFormLayout()
        self.lbl_p_title = QLabel("-")
        self.lbl_p_title.setWordWrap(True)
        self.lbl_p_source = QLabel("-")
        self.lbl_p_source.setWordWrap(True)
        self.lbl_p_time = QLabel("-")
        self.lbl_p_time.setWordWrap(True)
        self.lbl_p_author = QLabel("-")
        self.lbl_p_author.setWordWrap(True)
        self.lbl_p_channel = QLabel("-")
        self.lbl_p_channel.setWordWrap(True)
        self.btn_open_url = QPushButton("開啟原始新聞網址")
        self.btn_open_url.clicked.connect(self._on_open_url)
        form.addRow("標題：", self.lbl_p_title)
        form.addRow("來源：", self.lbl_p_source)
        form.addRow("時間：", self.lbl_p_time)
        form.addRow("作者：", self.lbl_p_author)
        form.addRow("頻道：", self.lbl_p_channel)
        form.addRow("", self.btn_open_url)
        layout.addLayout(form)

        self.lbl_summary_title = QLabel("Excel 摘要：")
        layout.addWidget(self.lbl_summary_title)
        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setMaximumHeight(80)
        layout.addWidget(self.txt_summary)

        layout.addWidget(QLabel("正文狀態："))
        self.lbl_body_status = QLabel("-")
        layout.addWidget(self.lbl_body_status)

        score_form = QFormLayout()
        self.lbl_priority = QLabel("-")
        self.lbl_priority.setWordWrap(True)
        self.lbl_should_respond = QLabel("-")
        self.lbl_should_respond.setWordWrap(True)
        self.lbl_core_business = QLabel("-")
        self.lbl_core_business.setWordWrap(True)
        self.lbl_score_breakdown = QLabel("-")
        self.lbl_score_breakdown.setWordWrap(True)
        score_form.addRow("優先級：", self.lbl_priority)
        score_form.addRow("是否應回應：", self.lbl_should_respond)
        score_form.addRow("MOI核心業務：", self.lbl_core_business)
        score_form.addRow("評分明細：", self.lbl_score_breakdown)
        layout.addLayout(score_form)

        self.chk_retain = QCheckBox("留用此新聞")
        self.chk_retain.stateChanged.connect(self._on_preview_checkbox_changed)
        layout.addWidget(self.chk_retain)

        layout.addWidget(QLabel("人工註記："))
        self.txt_manual_note = QTextEdit()
        self.txt_manual_note.setMaximumHeight(80)
        self.txt_manual_note.textChanged.connect(self._on_manual_note_changed)
        layout.addWidget(self.txt_manual_note)

        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        self._current_row_id = None
        return panel

    # ---------- 資料載入 ----------
    def reload_data(self):
        self._all_items = self.ctx.news_repo.list_all()
        self._apply_filter()

    def _apply_filter(self):
        items = list(self._all_items)
        f = self.filter_combo.currentText()
        if f == "已留用":
            items = [it for it in items if it.retention_status == "留用"]
        elif f == "AI建議不留用":
            items = [it for it in items if it.retention_status == "AI建議不留用"]
        elif f == "人工不留用":
            items = [it for it in items if it.retention_status == "人工不留用"]
        elif f == "待確認":
            items = [it for it in items if it.retention_status == "待確認"]
        elif f == "需回應":
            items = [it for it in items if it.should_respond]
        elif f == "正文已取得":
            items = [it for it in items if it.has_body]
        elif f == "正文未取得":
            items = [it for it in items if not it.has_body]

        kw = self.search_edit.text().strip().lower()
        if kw:
            items = [it for it in items if kw in it.title.lower() or kw in it.source.lower()]

        sort_key = self.sort_combo.currentText()
        if sort_key == "時間":
            items.sort(key=lambda it: it.published_at or "", reverse=True)
        elif sort_key == "來源":
            items.sort(key=lambda it: it.source or "")
        elif sort_key == "優先級":
            items.sort(key=lambda it: (it.priority_stars, it.score_final), reverse=True)
        elif sort_key == "重複群組":
            items.sort(key=lambda it: it.duplicate_group_id or "")

        self.model.set_items(items)

    # ---------- 預覽 ----------
    def _on_row_selected(self, current, previous):
        row = current.row()
        item = self.model.item_at(row)
        if item is None:
            return
        self._current_row_id = item.row_id
        self.lbl_p_title.setText(item.title)
        self.lbl_p_source.setText(item.source)
        self.lbl_p_time.setText(item.published_at)
        self.lbl_p_author.setText(item.author)
        self.lbl_p_channel.setText(item.channel)
        if item.has_body:
            self.lbl_summary_title.setText("正文：")
            # 顯示層清理（不動資料庫原文）：來源網頁/剪報常把同一段文字拆成
            # 好幾行，直接顯示會一截一截的——攤平句中斷行、保留段落分隔。
            # 網頁版預覽已用同一函式處理，桌面版補齊。
            from app.utils.text_utils import clean_body_for_preview
            self.txt_summary.setPlainText(clean_body_for_preview(item.body_text))
        elif item.summary:
            self.lbl_summary_title.setText("Excel 摘要：")
            self.txt_summary.setPlainText(item.summary)
        else:
            # 沒正文也沒摘要（報紙監測新聞抓取前的常態）：空白框會讓人誤會壞掉，
            # 改成說明目前狀態與下一步
            self.lbl_summary_title.setText("正文／摘要：")
            if item.url:
                self.txt_summary.setPlainText(
                    "（尚未取得正文）\n\n"
                    "此新聞有全文連結：勾選留用後，到「步驟 3：抓取正文」執行抓取，"
                    "正文就會顯示在這裡（抓取只處理已留用的新聞）。\n"
                    "也可以按上方「開啟原始新聞網址」直接查看原文。")
            else:
                self.txt_summary.setPlainText("（此新聞沒有可用的正文、摘要或原文連結）")
        body_source_label = item.body_source
        # 報紙監測的內部標記字樣是「無原文連結」；重新匯入補上連結後照原字樣顯示
        # 會誤導使用者，依實際有無連結調整顯示（僅顯示層，內部標記不動）
        if body_source_label == "報紙監測（無原文連結）" and item.url:
            body_source_label = "報紙監測（有剪報全文連結）"
        self.lbl_body_status.setText(f"{item.body_fetch_status}（{body_source_label}）")
        stars = item.priority_stars or 0
        self.lbl_priority.setText("★" * stars + "☆" * (5 - stars) if stars else "（尚無 AI 判斷）")
        self.lbl_should_respond.setText("是" if item.should_respond else "否")
        self.lbl_core_business.setText("是" if item.is_moi_core_business else "否")
        self.lbl_score_breakdown.setText(
            f"業務關聯 {item.score_business_relevance:g}／回應必要 {item.score_response_requirement:g}／"
            f"政治敏感 {item.score_political_sensitivity:g}／媒體關注 {item.score_media_attention:g}／"
            f"公眾影響 {item.score_public_impact:g}／加分 {item.score_executive_bonus:g}／"
            f"總分 {item.score_final:g}")
        self.chk_retain.blockSignals(True)
        self.chk_retain.setChecked(bool(item.retained))
        self.chk_retain.blockSignals(False)
        self.txt_manual_note.blockSignals(True)
        self.txt_manual_note.setPlainText(item.manual_note or "")
        self.txt_manual_note.blockSignals(False)

    def _on_open_url(self):
        item = self._get_current_item()
        if item and item.url:
            QDesktopServices.openUrl(QUrl(item.url))

    def _get_current_item(self):
        if not self._current_row_id:
            return None
        for it in self._all_items:
            if it.row_id == self._current_row_id:
                return it
        return None

    def _on_preview_checkbox_changed(self, state):
        item = self._get_current_item()
        if not item:
            return
        old_status = item.retention_status
        new_val = state == 2  # Qt.Checked
        new_status = apply_human_retention_override(
            self.ctx.news_repo, self.ctx.feedback_repo, item.row_id, new_val,
            old_status=old_status, action="human_override")
        item.retained = new_val
        item.retention_status = new_status
        item.retention_judged_by = "human"
        self.model.refresh_row(item.row_id)

    def _on_manual_note_changed(self):
        item = self._get_current_item()
        if not item:
            return
        note = self.txt_manual_note.toPlainText()
        item.manual_note = note
        self.ctx.news_repo.update_fields(item.row_id, {"manual_note": note})

    def _on_retention_toggled(self, row_id: str, new_value: bool):
        """來自表格勾選欄位的變更（規格：改勾選不得讓清單跳回第一列/不得重新載入整張表格）"""
        apply_human_retention_override(
            self.ctx.news_repo, self.ctx.feedback_repo, row_id, new_value,
            old_status="", action="human_override_table")
        # 被切換的列剛好是右側預覽中的那一則時，同步預覽面板的勾選框
        if row_id == self._current_row_id:
            self.chk_retain.blockSignals(True)
            self.chk_retain.setChecked(new_value)
            self.chk_retain.blockSignals(False)

    # ---------- 留用切換（整格點擊／批次／空白鍵） ----------
    def _on_table_clicked(self, index):
        if index.isValid() and index.column() == 0:
            self.model.toggle_retained(index.row())

    def _selected_rows(self) -> list:
        sel_model = self.table_view.selectionModel()
        if sel_model is None:
            return []
        return sorted({idx.row() for idx in sel_model.selectedRows()})

    def _set_retained_for_selection(self, value: bool):
        rows = self._selected_rows()
        if not rows:
            self.progress_label.setText("請先在清單中選取要處理的列（可按住 Shift／Ctrl 多選）")
            return
        for row in rows:
            self.model.set_retained(row, value)
        self.progress_label.setText(
            f"已將 {len(rows)} 列設為「{'留用' if value else '不留用'}」")

    def _on_space_toggle(self):
        """空白鍵：以焦點列切換後的新值為準，套用到所有選取列"""
        rows = self._selected_rows()
        if not rows:
            return
        focus = self.table_view.currentIndex().row()
        anchor = self.model.item_at(focus if focus in rows else rows[0])
        if anchor is None:
            return
        target = not anchor.retained
        for row in rows:
            self.model.set_retained(row, target)

    # ---------- AI 留用初判 ----------
    def _on_ai_judge(self):
        pending_items = [it for it in self._all_items if it.retention_judged_by != "human"]
        if not pending_items:
            self.progress_label.setText("目前沒有需要 AI 判斷的新聞")
            return
        # 續跑：若有上次未完成的留用初判工作，接續其 job_id（已完成批次會被略過）
        resumable = self.ctx.job_repo.list_resumable(job_type="retention")
        resume_job_id = resumable[0].job_id if resumable else None
        if resume_job_id:
            self.progress_label.setText("偵測到上次未完成的留用初判工作，將從中斷處續跑...")
        batch_size = self.ctx.settings.api.batch_size_retention
        self._worker = build_retention_worker(
            pending_items, batch_size, self.ctx.gateway, self.ctx.prompt_repo,
            self.ctx.job_repo, self.ctx.batch_repo,
            priority_threshold=self.ctx.settings.api.retention_priority_threshold,
            max_concurrency=self.ctx.settings.api.retention_max_concurrency,
            resume_job_id=resume_job_id,
            feedback_repo=self.ctx.feedback_repo,
            keyword_taxonomy=self.ctx.settings.keyword_taxonomy,
        )
        self._start_worker(len(pending_items))

    def _on_retry_failed_batches(self):
        """重試最近一次留用初判工作中失敗（retryable）的批次"""
        jobs = self.ctx.job_repo.list_all()
        retention_jobs = [j for j in jobs if j.job_type == "retention"]
        if not retention_jobs:
            self.progress_label.setText("尚無留用初判工作可重試")
            return
        last_job = retention_jobs[0]
        retryable = self.ctx.batch_repo.list_pending_or_retryable(last_job.job_id)
        if not retryable:
            self.progress_label.setText("最近一次留用初判工作沒有失敗批次")
            return
        import json as _json
        retry_row_ids = set()
        for b in retryable:
            retry_row_ids.update(_json.loads(b.item_ids_json))
        items = [it for it in self._all_items if it.row_id in retry_row_ids]
        if not items:
            self.progress_label.setText("失敗批次中的新聞已不存在於目前清單")
            return
        self._worker = build_retention_worker(
            items, self.ctx.settings.api.batch_size_retention, self.ctx.gateway,
            self.ctx.prompt_repo, self.ctx.job_repo, self.ctx.batch_repo,
            priority_threshold=self.ctx.settings.api.retention_priority_threshold,
            max_concurrency=self.ctx.settings.api.retention_max_concurrency,
            feedback_repo=self.ctx.feedback_repo,
            keyword_taxonomy=self.ctx.settings.keyword_taxonomy,
        )
        self._start_worker(len(items))

    def _start_worker(self, total: int):
        self.btn_ai_judge.setEnabled(False)
        self.btn_retry_failed.setEnabled(False)
        self.btn_cancel_job.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, total)
        self._worker.progress.connect(self._on_job_progress)
        self._worker.batch_failed.connect(self._on_batch_failed)
        self._worker.finished_job.connect(self._on_job_finished)
        self._worker.start()

    def _on_cancel_job(self):
        if self._worker:
            self._worker.request_cancel()

    def _on_job_progress(self, job_id, job_type, current, total, success, failed, skipped, message):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(
            f"{message}　{current}/{total}　成功 {success}　失敗 {failed}　略過 {skipped}")

    def _on_batch_failed(self, job_id, batch_index, error_type, error_detail):
        self.progress_label.setText(f"批次 {batch_index} 失敗（{error_type}）：{error_detail}")

    def _on_job_finished(self, job_id, status):
        self.btn_ai_judge.setEnabled(True)
        self.btn_retry_failed.setEnabled(True)
        self.btn_cancel_job.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"AI 留用初判工作結束，狀態：{status}")
        self.reload_data()
