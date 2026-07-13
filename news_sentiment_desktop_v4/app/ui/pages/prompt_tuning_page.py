"""Prompt 調校建議頁面 — AI 依人工修正提案 retention_judgement 的文字改良，
套用前必須先花真實 API 費用跑一次自動驗證（比對目前/建議 prompt 的復原率與誤判率）給使用者看。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QTextEdit, QSplitter, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from app.controllers.app_context import AppContext
from app.models.prompt_config import PromptConfig
from app.workers.prompt_tuning_propose_worker import PromptTuningProposeWorker
from app.workers.prompt_tuning_validate_worker import (
    build_prompt_tuning_validate_worker, select_validation_samples,
)
from app.services.prompt_tuning.validate_service import compute_validation_metrics, estimate_validation_cost
from app.services.retention.retention_service import decide_retain

DRAFT_ID_ROLE = Qt.UserRole + 1
METRIC_ROWS = [
    ("修正樣本數", "correction_sample_size", None),
    ("控制樣本數", "control_sample_size", None),
    ("修正樣本準確率", "current_accuracy_on_corrections", "proposed_accuracy_on_corrections"),
    ("控制樣本準確率", "current_accuracy_on_control", "proposed_accuracy_on_control"),
    ("復原數／復原率", "recovery_count", "recovery_rate"),
    ("誤判數／誤判率", "false_positive_count", "false_positive_rate"),
    ("預估花費(USD)", "estimated_cost_usd", None),
]


class PromptTuningPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._propose_worker = None
        self._validate_worker = None
        self._current_draft_id = None
        self._build_ui()
        self.refresh_drafts()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 8：Prompt 調校建議")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        root.addWidget(QLabel(
            "AI 依近期人工留用修正紀錄提出留用判斷 Prompt 的文字改良建議；"
            "套用前必須先花真實 API 費用跑一次自動驗證，看過復原率／誤判率再決定是否套用。"))

        toolbar = QHBoxLayout()
        self.btn_generate = QPushButton("產生建議")
        self.btn_generate.clicked.connect(self._on_generate)
        toolbar.addWidget(self.btn_generate)
        toolbar.addStretch()
        root.addLayout(toolbar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        splitter = QSplitter(Qt.Horizontal)
        self.draft_list = QListWidget()
        self.draft_list.itemClicked.connect(self._on_draft_clicked)
        splitter.addWidget(self.draft_list)

        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        detail_layout.addWidget(self.detail_text, 1)

        self.metrics_table = QTableWidget(0, 4)
        self.metrics_table.setAlternatingRowColors(True)
        self.metrics_table.setHorizontalHeaderLabels(["指標", "目前 Prompt", "建議 Prompt", "差異"])
        self.metrics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        detail_layout.addWidget(self.metrics_table)

        btn_row = QHBoxLayout()
        self.btn_validate = QPushButton("驗證")
        self.btn_validate.clicked.connect(self._on_validate_clicked)
        self.btn_adopt = QPushButton("套用")
        self.btn_adopt.clicked.connect(self._on_adopt_clicked)
        self.btn_reject = QPushButton("拒絕")
        self.btn_reject.clicked.connect(self._on_reject_clicked)
        btn_row.addWidget(self.btn_validate)
        btn_row.addWidget(self.btn_adopt)
        btn_row.addWidget(self.btn_reject)
        detail_layout.addLayout(btn_row)
        splitter.addWidget(detail_box)

        root.addWidget(splitter, 1)

    # ---------- 產生建議 ----------
    def _on_generate(self):
        self._propose_worker = PromptTuningProposeWorker(
            self.ctx.gateway, self.ctx.prompt_repo, self.ctx.feedback_repo,
            self.ctx.news_repo, self.ctx.prompt_tuning_repo,
        )
        self.btn_generate.setEnabled(False)
        self.status_label.setText("正在依近期人工修正紀錄產生提案...")
        self._propose_worker.finished_ok.connect(self._on_propose_ok)
        self._propose_worker.finished_error.connect(self._on_propose_error)
        self._propose_worker.finished_too_few.connect(self._on_propose_too_few)
        self._propose_worker.start()

    def _on_propose_ok(self, draft_id: str):
        self.btn_generate.setEnabled(True)
        self.status_label.setText("已產生新提案")
        self.refresh_drafts()

    def _on_propose_error(self, message: str):
        self.btn_generate.setEnabled(True)
        self.status_label.setText(f"提案產生失敗：{message}")

    def _on_propose_too_few(self, count: int):
        self.btn_generate.setEnabled(True)
        self.status_label.setText(f"距上次提案後只累積 {count} 筆人工修正，暫不建議產生新提案（未呼叫API）")

    # ---------- 列表／詳情 ----------
    def refresh_drafts(self):
        self.draft_list.clear()
        for d in self.ctx.prompt_tuning_repo.list_all():
            created = datetime.fromtimestamp(d.created_at).strftime("%m/%d %H:%M")
            li = QListWidgetItem(f"[{d.status}] {created}（based on v{d.based_on_version}）")
            li.setData(DRAFT_ID_ROLE, d.draft_id)
            self.draft_list.addItem(li)

    def _on_draft_clicked(self, item: QListWidgetItem):
        draft_id = item.data(DRAFT_ID_ROLE)
        self._current_draft_id = draft_id
        self._render_draft(draft_id)

    def _render_draft(self, draft_id: str):
        d = self.ctx.prompt_tuning_repo.get(draft_id)
        if not d:
            return
        text = (f"狀態：{d.status}\n依據版本：v{d.based_on_version}\n"
                f"參考修正筆數：{d.correction_count_used}\n產生模型：{d.generated_by_model}\n\n"
                f"調整理由：\n{d.rationale}\n\n"
                f"【提案 SYSTEM_PROMPT】\n{d.proposed_system_prompt}\n\n"
                f"【提案 USER_TEMPLATE】\n{d.proposed_user_template}")
        self.detail_text.setPlainText(text)
        self._render_metrics(d)

    def _render_metrics(self, d):
        self.metrics_table.setRowCount(0)
        if d.status not in ("已驗證", "已套用"):
            return
        try:
            metrics = json.loads(d.validation_metrics_json)
        except Exception:
            return
        if not metrics:
            return
        self.metrics_table.setRowCount(len(METRIC_ROWS))
        for row, (label, cur_key, prop_key) in enumerate(METRIC_ROWS):
            self.metrics_table.setItem(row, 0, QTableWidgetItem(label))
            cur_val = metrics.get(cur_key)
            self.metrics_table.setItem(row, 1, QTableWidgetItem(self._fmt(cur_key, cur_val)))
            if prop_key:
                prop_val = metrics.get(prop_key)
                self.metrics_table.setItem(row, 2, QTableWidgetItem(self._fmt(prop_key, prop_val)))
                diff_item = QTableWidgetItem(self._diff_text(cur_key, prop_key, metrics))
                self._color_diff(diff_item, cur_key, prop_key, metrics)
                self.metrics_table.setItem(row, 3, diff_item)
            else:
                self.metrics_table.setItem(row, 2, QTableWidgetItem(""))
                self.metrics_table.setItem(row, 3, QTableWidgetItem(""))

    @staticmethod
    def _fmt(key: str, val) -> str:
        if val is None:
            return "-"
        if "rate" in key or "accuracy" in key:
            return f"{val:.1%}"
        if "cost" in key:
            return f"${val:.2f}"
        return str(val)

    @staticmethod
    def _diff_text(cur_key: str, prop_key: str, metrics: dict) -> str:
        cur_val, prop_val = metrics.get(cur_key), metrics.get(prop_key)
        if cur_val is None or prop_val is None:
            return "-"
        delta = prop_val - cur_val
        if "rate" in prop_key or "accuracy" in prop_key:
            return f"{delta:+.1%}"
        return f"{delta:+g}"

    @staticmethod
    def _color_diff(item: QTableWidgetItem, cur_key: str, prop_key: str, metrics: dict) -> None:
        cur_val, prop_val = metrics.get(cur_key), metrics.get(prop_key)
        if cur_val is None or prop_val is None:
            return
        delta = prop_val - cur_val
        is_bad_metric = "false_positive" in prop_key  # 誤判率越低越好，其餘指標越高越好
        improved = (delta < 0) if is_bad_metric else (delta > 0)
        worsened = (delta > 0) if is_bad_metric else (delta < 0)
        if improved:
            item.setBackground(QBrush(QColor(200, 230, 201)))   # 淡綠
        elif worsened:
            item.setBackground(QBrush(QColor(255, 205, 210)))   # 淡紅

    # ---------- 驗證 ----------
    def _on_validate_clicked(self):
        if not self._current_draft_id:
            QMessageBox.information(self, "提示", "請先選擇一筆提案")
            return
        draft = self.ctx.prompt_tuning_repo.get(self._current_draft_id)
        if not draft:
            return
        correction_items, control_items = select_validation_samples(
            draft, self.ctx.prompt_repo, self.ctx.news_repo)
        est_cost = estimate_validation_cost(len(correction_items), len(control_items))
        reply = QMessageBox.question(
            self, "確認驗證成本",
            f"即將對 {len(correction_items)} 筆人工修正樣本 + {len(control_items)} 筆邊界對照樣本"
            f"各執行「目前 Prompt」與「建議 Prompt」兩次真實 API 呼叫，"
            f"預估花費約 ${est_cost:.2f} USD。是否繼續？",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        self.ctx.prompt_tuning_repo.update_status(draft.draft_id, "驗證中")
        self.refresh_drafts()
        self._validate_worker = build_prompt_tuning_validate_worker(
            draft, correction_items, control_items, self.ctx.gateway, self.ctx.prompt_repo,
            self.ctx.feedback_repo, self.ctx.news_repo, self.ctx.job_repo, self.ctx.batch_repo,
        )
        self.btn_validate.setEnabled(False)
        self.status_label.setText("驗證進行中...")
        self._validate_worker.progress.connect(self._on_validate_progress)
        self._validate_worker.finished_job.connect(self._on_validate_finished)
        self._validate_worker.start()

    def _on_validate_progress(self, job_id, job_type, current, total, success, failed, skipped, message):
        self.status_label.setText(f"{message}　{current}/{total}")

    def _on_validate_finished(self, job_id, status):
        self.btn_validate.setEnabled(True)
        ctx = self._validate_worker.prompt_tuning_context
        draft_id = ctx["draft_id"]
        job = self.ctx.job_repo.get(job_id)
        if status != "completed" or job is None or job.success_count == 0:
            self.ctx.prompt_tuning_repo.update_validation_result(draft_id, "驗證失敗", "{}")
            self.status_label.setText("驗證失敗，可重新嘗試")
            self.refresh_drafts()
            return
        est_cost = estimate_validation_cost(len(ctx["correction_items"]), len(ctx["control_items"]))
        metrics = compute_validation_metrics(
            ctx["correction_items"], ctx["control_items"], ctx["current_results"],
            ctx["proposed_results"], retain_fn=decide_retain,
            priority_threshold=self.ctx.settings.api.retention_priority_threshold,
            estimated_cost_usd=est_cost,
            error_note="" if job.failed_count == 0 else f"{job.failed_count} 個批次失敗，指標僅反映成功批次",
        )
        self.ctx.prompt_tuning_repo.update_validation_result(
            draft_id, "已驗證", json.dumps(asdict(metrics), ensure_ascii=False))
        self.status_label.setText("驗證完成")
        self.refresh_drafts()
        self._render_draft(draft_id)

    # ---------- 套用／拒絕 ----------
    def _on_adopt_clicked(self):
        if not self._current_draft_id:
            QMessageBox.information(self, "提示", "請先選擇一筆提案")
            return
        draft = self.ctx.prompt_tuning_repo.get(self._current_draft_id)
        if not draft:
            return
        if draft.status != "已驗證":
            QMessageBox.warning(self, "提示", "尚未通過驗證，無法套用")
            return
        active = self.ctx.prompt_repo.get_active("retention_judgement")
        if active and active.version != draft.based_on_version:
            if QMessageBox.question(
                    self, "警告",
                    "目前使用中的 Prompt 版本已變更，驗證結果可能已過時，是否仍要套用？",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        new_cfg = PromptConfig(
            task="retention_judgement", system_prompt=draft.proposed_system_prompt,
            user_template=draft.proposed_user_template,
            tool_schema_json=active.tool_schema_json if active else "{}",
        )
        self.ctx.prompt_repo.save_new_version(new_cfg)
        self.ctx.prompt_tuning_repo.update_status(draft.draft_id, "已套用")
        self.status_label.setText("已套用，留用判斷將於下次執行時使用新 Prompt")
        self.refresh_drafts()

    def _on_reject_clicked(self):
        if not self._current_draft_id:
            QMessageBox.information(self, "提示", "請先選擇一筆提案")
            return
        self.ctx.prompt_tuning_repo.update_status(self._current_draft_id, "已拒絕")
        self.refresh_drafts()
