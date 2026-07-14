"""系統設定頁面 — 對應規格書 四（API/模型）、十六（Prompt）、八（抓取）、十四（Word樣式）"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QFormLayout,
    QGroupBox, QTabWidget, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QMessageBox,
    QTextEdit, QListWidget, QListWidgetItem, QSplitter, QFileDialog, QScrollArea,
)
from PySide6.QtCore import Qt

from app.ui.theme import mark_primary, mark_danger
from app.controllers.app_context import AppContext
from app.utils.secure_key_store import (
    save_api_key, load_api_key, clear_api_key, mask_api_key,
    save_openai_api_key, load_openai_api_key, clear_openai_api_key,
    load_gmail_credentials, clear_gmail_credentials,
)
from app.models.prompt_config import PromptConfig, PROMPT_TASKS
from app.models.settings import ModelTaskConfig
from app.workers.gmail_import_worker import GmailAuthWorker

MODEL_CHOICES = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"]
# OpenAI 常用型號（下拉快速選擇用；清單外的新型號仍可直接在欄位輸入）
OPENAI_MODEL_CHOICES = ["gpt-5.5", "gpt-5.5-mini"]
ALL_MODEL_CHOICES = MODEL_CHOICES + OPENAI_MODEL_CHOICES


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("系統設定")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._scrollable(self._build_api_tab()), "AI 供應商 / API")
        tabs.addTab(self._scrollable(self._build_model_tab()), "任務模型設定")
        tabs.addTab(self._build_prompt_tab(), "Prompt 編輯器")  # 編輯器需要撐滿高度，不包捲動區
        tabs.addTab(self._scrollable(self._build_scraping_tab()), "正文抓取設定")
        tabs.addTab(self._scrollable(self._build_word_tab()), "Word 輸出樣式")
        tabs.addTab(self._scrollable(self._build_gmail_tab()), "Gmail 匯入設定")
        root.addWidget(tabs, 1)

    @staticmethod
    def _scrollable(widget: QWidget) -> QWidget:
        """把分頁內容包進可捲動區域，避免欄位一多超出視窗高度就被裁掉看不到"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    # ---------- API Key ----------
    def _build_api_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # ---- AI 供應商切換（V4.3.0）----
        provider_group = QGroupBox("AI 供應商（所有分析功能共用）")
        provider_form = QFormLayout(provider_group)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI（ChatGPT）", "openai")
        self.provider_combo.addItem("Anthropic（Claude）", "anthropic")
        idx = self.provider_combo.findData(self.ctx.settings.api.provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        provider_form.addRow("使用供應商：", self.provider_combo)
        self.openai_default_model_combo = QComboBox()
        self.openai_default_model_combo.setEditable(True)   # 新型號可直接輸入
        self.openai_default_model_combo.addItems(OPENAI_MODEL_CHOICES)
        self.openai_default_model_combo.setCurrentText(self.ctx.settings.api.openai_default_model)
        provider_form.addRow("OpenAI 預設模型：", self.openai_default_model_combo)
        provider_form.addRow(QLabel(
            "說明：任務模型設定中仍是 claude-* 型號時，OpenAI 供應商會自動改用上方預設模型；\n"
            "也可到「任務模型設定」逐一輸入 OpenAI 型號。切換後按下方「儲存 API 設定」生效。"))
        layout.addWidget(provider_group)

        # ---- OpenAI API Key ----
        openai_group = QGroupBox("OpenAI API Key")
        openai_form = QFormLayout(openai_group)
        self.openai_key_display = QLabel(mask_api_key(load_openai_api_key()))
        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.Password)
        self.openai_key_input.setPlaceholderText("輸入新的 OpenAI API Key（sk-...）")
        openai_form.addRow("目前狀態：", self.openai_key_display)
        openai_form.addRow("新的 API Key：", self.openai_key_input)
        openai_btn_row = QHBoxLayout()
        btn_openai_save = QPushButton("儲存")
        mark_primary(btn_openai_save)
        btn_openai_save.clicked.connect(self._on_save_openai_key)
        btn_openai_clear = QPushButton("一鍵清除")
        mark_danger(btn_openai_clear)
        btn_openai_clear.clicked.connect(self._on_clear_openai_key)
        openai_btn_row.addWidget(btn_openai_save)
        openai_btn_row.addWidget(btn_openai_clear)
        openai_form.addRow(openai_btn_row)
        layout.addWidget(openai_group)

        key_group = QGroupBox("Anthropic API Key")
        form = QFormLayout(key_group)
        self.key_display = QLabel(mask_api_key(load_api_key()))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("輸入新的 API Key（sk-ant-...）")
        form.addRow("目前狀態：", self.key_display)
        form.addRow("新的 API Key：", self.key_input)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("儲存")
        mark_primary(btn_save)
        btn_save.clicked.connect(self._on_save_key)
        btn_test = QPushButton("測試連線（目前供應商）")
        btn_test.clicked.connect(self._on_test_key)
        btn_clear = QPushButton("一鍵清除")
        mark_danger(btn_clear)
        btn_clear.clicked.connect(self._on_clear_key)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addWidget(btn_clear)
        form.addRow(btn_row)
        layout.addWidget(key_group)

        self.key_status_label = QLabel("")
        self.key_status_label.setWordWrap(True)
        layout.addWidget(self.key_status_label)

        api_group = QGroupBox("API 呼叫設定")
        api_form = QFormLayout(api_group)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setValue(self.ctx.settings.api.request_timeout_sec)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(self.ctx.settings.api.max_retries)
        self.backoff_spin = QDoubleSpinBox()
        self.backoff_spin.setRange(0.5, 30.0)
        self.backoff_spin.setValue(self.ctx.settings.api.retry_backoff_base_sec)
        self.batch_retention_spin = QSpinBox()
        self.batch_retention_spin.setRange(1, 100)
        self.batch_retention_spin.setValue(self.ctx.settings.api.batch_size_retention)
        self.batch_clustering_spin = QSpinBox()
        self.batch_clustering_spin.setRange(1, 50)
        self.batch_clustering_spin.setValue(self.ctx.settings.api.batch_size_clustering)
        self.granularity_combo = QComboBox()
        self.granularity_combo.addItem("細（同一具體事件才歸同議題）", "fine")
        self.granularity_combo.addItem("標準（同一事件及其後續、回應）", "standard")
        self.granularity_combo.addItem("粗（同領域積極合併，議題最少）", "coarse")
        gidx = self.granularity_combo.findData(self.ctx.settings.api.clustering_granularity)
        if gidx >= 0:
            self.granularity_combo.setCurrentIndex(gidx)
        self.chk_message_batches = QCheckBox("大量非即時任務啟用 Message Batches API（可降低成本）")
        self.chk_message_batches.setChecked(self.ctx.settings.api.enable_message_batches_api)
        self.retention_threshold_spin = QSpinBox()
        self.retention_threshold_spin.setRange(1, 5)
        self.retention_threshold_spin.setValue(self.ctx.settings.api.retention_priority_threshold)
        self.retention_concurrency_spin = QSpinBox()
        self.retention_concurrency_spin.setRange(1, 10)
        self.retention_concurrency_spin.setValue(self.ctx.settings.api.retention_max_concurrency)

        api_form.addRow("逾時秒數：", self.timeout_spin)
        api_form.addRow("重試次數：", self.retry_spin)
        api_form.addRow("重試退避基數（秒）：", self.backoff_spin)
        api_form.addRow("留用初判批次大小：", self.batch_retention_spin)
        api_form.addRow("議題分群批次大小：", self.batch_clustering_spin)
        api_form.addRow("議題分群粒度：", self.granularity_combo)
        api_form.addRow(self.chk_message_batches)
        api_form.addRow("留用優先級門檻（星，達此星數才留用）：", self.retention_threshold_spin)
        api_form.addRow("留用平行批次數：", self.retention_concurrency_spin)
        btn_save_api = QPushButton("儲存 API 設定")
        mark_primary(btn_save_api)
        btn_save_api.clicked.connect(self._on_save_api_settings)
        api_form.addRow(btn_save_api)
        layout.addWidget(api_group)
        layout.addStretch()
        return w

    def _on_save_key(self):
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.information(self, "提示", "請輸入 API Key")
            return
        try:
            save_api_key(key)
            self.key_display.setText(mask_api_key(key))
            self.key_input.clear()
            self.key_status_label.setText("API Key 已加密儲存")
        except Exception as e:
            QMessageBox.critical(self, "儲存失敗", str(e))

    def _on_test_key(self):
        self.key_status_label.setText("測試連線中...")
        result = self.ctx.gateway.test_connection()
        if result.get("ok"):
            self.key_status_label.setText("✓ 連線成功")
        else:
            self.key_status_label.setText(f"✗ 連線失敗：{result.get('message')}")

    def _on_clear_key(self):
        confirm = QMessageBox.question(self, "確認清除", "確定要清除已儲存的 API Key 嗎？")
        if confirm == QMessageBox.Yes:
            clear_api_key()
            self.key_display.setText(mask_api_key(None))
            self.key_status_label.setText("API Key 已清除")

    def _on_save_openai_key(self):
        key = self.openai_key_input.text().strip()
        if not key:
            QMessageBox.information(self, "提示", "請輸入 OpenAI API Key")
            return
        try:
            save_openai_api_key(key)
            self.openai_key_display.setText(mask_api_key(key))
            self.openai_key_input.clear()
            self.key_status_label.setText("OpenAI API Key 已加密儲存")
        except Exception as e:
            QMessageBox.critical(self, "儲存失敗", str(e))

    def _on_clear_openai_key(self):
        confirm = QMessageBox.question(self, "確認清除", "確定要清除已儲存的 OpenAI API Key 嗎？")
        if confirm == QMessageBox.Yes:
            clear_openai_api_key()
            self.openai_key_display.setText(mask_api_key(None))
            self.key_status_label.setText("OpenAI API Key 已清除")

    def _on_save_api_settings(self):
        old_provider = self.ctx.settings.api.provider
        self.ctx.settings.api.provider = self.provider_combo.currentData()
        self.ctx.settings.api.openai_default_model = (
            self.openai_default_model_combo.currentText().strip() or "gpt-5.5")
        self.ctx.settings.api.request_timeout_sec = self.timeout_spin.value()
        self.ctx.settings.api.max_retries = self.retry_spin.value()
        self.ctx.settings.api.retry_backoff_base_sec = self.backoff_spin.value()
        self.ctx.settings.api.batch_size_retention = self.batch_retention_spin.value()
        self.ctx.settings.api.batch_size_clustering = self.batch_clustering_spin.value()
        self.ctx.settings.api.clustering_granularity = self.granularity_combo.currentData()
        self.ctx.settings.api.enable_message_batches_api = self.chk_message_batches.isChecked()
        self.ctx.settings.api.retention_priority_threshold = self.retention_threshold_spin.value()
        self.ctx.settings.api.retention_max_concurrency = self.retention_concurrency_spin.value()
        self.ctx.save_settings()  # save_settings 內含 reload：閘道會依新供應商重建
        new_provider = self.ctx.settings.api.provider
        msg = "API 設定已儲存"
        if new_provider != old_provider:
            name = "OpenAI（ChatGPT）" if new_provider == "openai" else "Anthropic（Claude）"
            msg += f"\n\nAI 供應商已切換為：{name}\n之後所有分析呼叫（留用/分群/綜整/立場等）都將使用該供應商。"
        QMessageBox.information(self, "已儲存", msg)

    # ---------- 任務模型 ----------
    def _build_model_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        note = QLabel(
            "各 AI 任務使用的模型。欄位可直接輸入任何模型 ID（例如 OpenAI 的 gpt-5.5）。\n"
            "使用 OpenAI 供應商時，仍是 claude-* 的任務會自動改用「AI 供應商 / API」分頁的預設模型。")
        note.setWordWrap(True)
        layout.addWidget(note)

        task_labels = {
            "retention_prefilter": "留用粗篩（步驟2，量大建議用較快模型）",
            "retention_judgement": "留用細評（步驟2）",
            "topic_clustering": "議題分群（步驟4）",
            "topic_merge": "跨批次議題整合（步驟4）",
            "topic_naming": "議題命名（步驟4）",
            "topic_summarization": "議題綜整（步驟6，建議用最強模型）",
            "stance_analysis": "立場分析（步驟6）",
            "rule_draft": "規則草案產生",
            "prompt_tuning_propose": "Prompt 調校建議",
        }
        self.task_model_combos = {}
        form = QFormLayout()
        for m in self.ctx.settings.task_models:
            combo = QComboBox()
            combo.setEditable(True)  # 允許輸入清單以外的新模型 ID
            combo.addItems(MODEL_CHOICES)          # Claude 型號
            combo.insertSeparator(combo.count())
            combo.addItems(OPENAI_MODEL_CHOICES)   # OpenAI 型號
            combo.setCurrentText(m["model_id"])
            self.task_model_combos[m["task"]] = combo
            form.addRow(task_labels.get(m["task"], m["task"]), combo)
        layout.addLayout(form)

        btn_save = QPushButton("儲存任務模型設定")
        mark_primary(btn_save)
        btn_save.clicked.connect(self._on_save_task_models)
        layout.addWidget(btn_save)
        layout.addStretch()
        return w

    def _on_save_task_models(self):
        for m in self.ctx.settings.task_models:
            combo = self.task_model_combos.get(m["task"])
            if combo:
                m["model_id"] = combo.currentText()
        self.ctx.save_settings()
        msg = "任務模型設定已儲存"
        # 常見誤解：把任務模型改成 gpt-* 就以為切到 ChatGPT 了——實際走哪家
        # 由「AI 供應商」決定，跨家的模型 ID 會被自動改回該供應商的預設模型。
        provider = self.ctx.settings.api.provider
        model_ids = [m.get("model_id", "") for m in self.ctx.settings.task_models]
        if provider == "anthropic" and any(mid.startswith("gpt") for mid in model_ids):
            msg += ("\n\n⚠ 注意：目前 AI 供應商是 Anthropic（Claude），"
                    "設為 gpt-* 的任務仍會自動改用預設 Claude 模型執行。\n"
                    "要真正改用 ChatGPT，請到「AI 供應商 / API」分頁把「使用供應商」"
                    "切換為 OpenAI（ChatGPT）並按「儲存 API 設定」。")
        elif provider == "openai" and any(mid.startswith("claude") for mid in model_ids):
            msg += ("\n\n提示：目前供應商是 OpenAI（ChatGPT），"
                    "設為 claude-* 的任務會自動改用「OpenAI 預設模型」執行。")
        QMessageBox.information(self, "已儲存", msg)

    # ---------- Prompt 編輯器（V4.2.1 全面升級） ----------
    # 任務 → 儲存後需重跑的步驟（提示用）
    _PROMPT_TASK_RERUN_HINTS = {
        "retention_prefilter": "步驟 2（留用初判）",
        "retention_judgement": "步驟 2（留用初判）",
        "topic_clustering": "步驟 4（議題分群）",
        "topic_merge": "步驟 4（議題分群）",
        "topic_naming": "步驟 4（議題分群）",
        "topic_summarization": "步驟 6（議題綜整）",
        "stance_analysis": "步驟 6（議題綜整／立場分析）",
        "rule_draft": "規則草案產生",
    }

    def _build_prompt_tab(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)

        self.prompt_task_list = QListWidget()
        for task in PROMPT_TASKS:
            self.prompt_task_list.addItem(task)
        self.prompt_task_list.currentTextChanged.connect(self._on_prompt_task_selected)
        layout.addWidget(self.prompt_task_list, 1)

        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)

        # 版本歷史下拉 + 啟用此版本
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("版本歷史："))
        self.prompt_version_combo = QComboBox()
        self.prompt_version_combo.currentIndexChanged.connect(self._on_prompt_version_selected)
        version_row.addWidget(self.prompt_version_combo, 1)
        self.btn_activate_version = QPushButton("啟用此版本")
        self.btn_activate_version.clicked.connect(self._on_activate_prompt_version)
        version_row.addWidget(self.btn_activate_version)
        editor_layout.addLayout(version_row)

        editor_layout.addWidget(QLabel("System Prompt："))
        self.system_prompt_edit = QTextEdit()
        editor_layout.addWidget(self.system_prompt_edit, 2)
        editor_layout.addWidget(QLabel("User Template（可用 {變數} 佔位符；缺少預設佔位符時儲存前會警告）："))
        self.user_template_edit = QTextEdit()
        editor_layout.addWidget(self.user_template_edit, 2)

        editor_layout.addWidget(QLabel(
            "Tool Schema（JSON）——schema 的 required 決定模型「必填」欄位，"
            "prompt 文字無法覆蓋它；欄位輸出異常時請優先檢查這裡："))
        self.tool_schema_edit = QTextEdit()
        self.tool_schema_edit.setAcceptRichText(False)
        editor_layout.addWidget(self.tool_schema_edit, 2)

        self.prompt_version_label = QLabel("")
        self.prompt_version_label.setWordWrap(True)
        editor_layout.addWidget(self.prompt_version_label)
        hint = QLabel("⚠ 儲存或切換版本後，需重跑對應步驟才會生效（例如綜整 Prompt → 重跑步驟 6）")
        hint.setWordWrap(True)
        editor_layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_save_prompt = QPushButton("儲存為新版本")
        mark_primary(btn_save_prompt)
        btn_save_prompt.clicked.connect(self._on_save_prompt)
        btn_restore = QPushButton("還原預設")
        btn_restore.clicked.connect(self._on_restore_prompt)
        btn_row.addWidget(btn_save_prompt)
        btn_row.addWidget(btn_restore)
        editor_layout.addLayout(btn_row)

        layout.addWidget(editor_box, 3)
        return w

    @staticmethod
    def _format_schema_json(schema_json: str) -> str:
        """Schema JSON 以縮排格式呈現，便於編輯；無法解析時原樣顯示"""
        try:
            return json.dumps(json.loads(schema_json or "{}"), ensure_ascii=False, indent=2)
        except Exception:
            return schema_json or "{}"

    def _load_prompt_cfg_into_editors(self, cfg: PromptConfig):
        self.system_prompt_edit.setPlainText(cfg.system_prompt)
        self.user_template_edit.setPlainText(cfg.user_template)
        self.tool_schema_edit.setPlainText(self._format_schema_json(cfg.tool_schema_json))
        self._current_prompt_cfg = cfg

    def _refresh_prompt_versions(self, task: str):
        """重建版本歷史下拉，並將編輯器載入目前啟用版本"""
        from app.prompts.registry import get_active_prompt
        active = get_active_prompt(self.ctx.prompt_repo, task)
        versions = self.ctx.prompt_repo.list_versions(task)
        self.prompt_version_combo.blockSignals(True)
        self.prompt_version_combo.clear()
        for v in versions:
            label = f"v{v.version}"
            if v.enabled:
                label += "（啟用中）"
            if v.is_default:
                label += "（系統預設）"
            self.prompt_version_combo.addItem(label, v.version)
        idx = self.prompt_version_combo.findData(active.version)
        if idx >= 0:
            self.prompt_version_combo.setCurrentIndex(idx)
        self.prompt_version_combo.blockSignals(False)
        self._load_prompt_cfg_into_editors(active)
        self.prompt_version_label.setText(
            f"目前啟用版本：v{active.version}　是否為系統預設：{active.is_default}")

    def _on_prompt_task_selected(self, task: str):
        if not task:
            return
        self._current_prompt_task = task
        self._refresh_prompt_versions(task)

    def _on_prompt_version_selected(self, index: int):
        """下拉切換版本：載入該版本內容供檢視/比較（尚未啟用，需按「啟用此版本」）"""
        task = getattr(self, "_current_prompt_task", None)
        if not task or index < 0:
            return
        version = self.prompt_version_combo.itemData(index)
        cfg = next((v for v in self.ctx.prompt_repo.list_versions(task)
                    if v.version == version), None)
        if cfg is None:
            return
        self._load_prompt_cfg_into_editors(cfg)
        state = "啟用中" if cfg.enabled else "未啟用（按「啟用此版本」切換）"
        self.prompt_version_label.setText(f"檢視版本：v{cfg.version}　狀態：{state}")

    def _on_activate_prompt_version(self):
        task = getattr(self, "_current_prompt_task", None)
        if not task:
            return
        version = self.prompt_version_combo.currentData()
        if version is None:
            return
        self.ctx.prompt_repo.activate_version(task, version)
        self._refresh_prompt_versions(task)
        QMessageBox.information(
            self, "已啟用",
            f"已啟用 v{version}。\n注意：需重跑{self._PROMPT_TASK_RERUN_HINTS.get(task, '對應步驟')}才會生效。")

    def _missing_default_placeholders(self, task: str, new_template: str) -> list:
        """缺漏警告：找出「系統預設模板有、但新模板沒有」的佔位符。
        缺少佔位符代表模型將收不到該資料（safe_format 只是不噴錯，不會補資料）。"""
        from app.prompts.registry import _DEFAULTS
        from app.utils.text_utils import extract_placeholders
        default_template = _DEFAULTS.get(task, {}).get("user_template", "")
        missing = extract_placeholders(default_template) - extract_placeholders(new_template)
        return sorted(missing)

    def _on_save_prompt(self):
        task = getattr(self, "_current_prompt_task", None)
        if not task:
            return
        # 1) Tool Schema JSON 格式驗證
        schema_text = self.tool_schema_edit.toPlainText().strip() or "{}"
        try:
            parsed_schema = json.loads(schema_text)
            if not isinstance(parsed_schema, dict):
                raise ValueError("最外層必須是 JSON 物件（{...}）")
        except Exception as e:
            QMessageBox.critical(self, "Tool Schema 格式錯誤",
                                  f"Tool Schema 不是合法 JSON，未儲存：\n{e}")
            return
        # 2) 佔位符缺漏警告
        new_template = self.user_template_edit.toPlainText()
        missing = self._missing_default_placeholders(task, new_template)
        if missing:
            confirm = QMessageBox.question(
                self, "佔位符缺漏警告",
                "新模板缺少預設佔位符：{" + "}、{".join(missing) + "}\n"
                "缺少的佔位符對應的資料將不會提供給模型，可能導致輸出不完整。\n"
                "仍要儲存嗎？")
            if confirm != QMessageBox.Yes:
                return
        # 3) 儲存為新版本（自動成為啟用版本）
        cfg = PromptConfig(
            task=task, system_prompt=self.system_prompt_edit.toPlainText(),
            user_template=new_template,
            tool_schema_json=json.dumps(parsed_schema, ensure_ascii=False),
        )
        new_cfg = self.ctx.prompt_repo.save_new_version(cfg)
        self._refresh_prompt_versions(task)
        QMessageBox.information(
            self, "已儲存",
            f"Prompt 已儲存為新版本 v{new_cfg.version} 並啟用。\n"
            f"注意：需重跑{self._PROMPT_TASK_RERUN_HINTS.get(task, '對應步驟')}才會生效。")

    def _on_restore_prompt(self):
        task = getattr(self, "_current_prompt_task", None)
        if not task:
            return
        restored = self.ctx.prompt_repo.restore_default(task)
        if restored:
            self._refresh_prompt_versions(task)
            self.prompt_version_label.setText(f"已還原預設，目前啟用版本：v{restored.version}")

    # ---------- 抓取設定 ----------
    def _build_scraping_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.ctx.settings.scraping
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.5, 30.0)
        self.delay_spin.setValue(s.per_domain_delay_sec)
        self.timeout_scrape_spin = QSpinBox()
        self.timeout_scrape_spin.setRange(5, 120)
        self.timeout_scrape_spin.setValue(s.request_timeout_sec)
        self.ua_edit = QLineEdit(s.user_agent)
        self.robots_chk = QCheckBox("遵守 robots.txt")
        self.robots_chk.setChecked(s.respect_robots_txt)
        self.ssl_chk = QCheckBox("驗證 SSL 憑證（公司代理/防火牆環境若大量 SSL 錯誤可暫時關閉，有安全風險）")
        self.ssl_chk.setChecked(getattr(s, "verify_ssl", True))
        self.browser_chk = QCheckBox("啟用瀏覽器渲染備援（Playwright + GNE；一般抓取無法取得主文時自動改用，"
                                       "需先執行 pip install playwright gne 與 playwright install chromium）")
        self.browser_chk.setChecked(getattr(s, "use_browser_rendering", False))
        self.browser_timeout_spin = QSpinBox()
        self.browser_timeout_spin.setRange(10, 180)
        self.browser_timeout_spin.setValue(getattr(s, "browser_timeout_sec", 45))

        form.addRow("每網域延遲秒數：", self.delay_spin)
        form.addRow("請求逾時秒數：", self.timeout_scrape_spin)
        form.addRow("User-Agent：", self.ua_edit)
        form.addRow(self.robots_chk)
        form.addRow(self.ssl_chk)
        form.addRow(self.browser_chk)
        form.addRow("瀏覽器渲染逾時秒數：", self.browser_timeout_spin)

        btn_save = QPushButton("儲存抓取設定")
        mark_primary(btn_save)
        btn_save.clicked.connect(self._on_save_scraping)
        form.addRow(btn_save)
        return w

    def _on_save_scraping(self):
        s = self.ctx.settings.scraping
        s.per_domain_delay_sec = self.delay_spin.value()
        s.request_timeout_sec = self.timeout_scrape_spin.value()
        s.user_agent = self.ua_edit.text()
        s.respect_robots_txt = self.robots_chk.isChecked()
        s.verify_ssl = self.ssl_chk.isChecked()
        s.use_browser_rendering = self.browser_chk.isChecked()
        s.browser_timeout_sec = self.browser_timeout_spin.value()
        self.ctx.save_settings()
        QMessageBox.information(self, "已儲存", "正文抓取設定已儲存")

    # ---------- Word 樣式 ----------
    def _build_word_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.ctx.settings.word_export

        self.logo_edit = QLineEdit(s.logo_path)
        btn_logo = QPushButton("選擇 Logo...")
        btn_logo.clicked.connect(self._on_pick_logo)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo_edit)
        logo_row.addWidget(btn_logo)

        self.header_edit = QLineEdit(s.header_text)
        self.footer_edit = QLineEdit(s.footer_text)
        self.date_format_edit = QLineEdit(s.date_format)
        self.font_name_edit = QLineEdit(s.font_name)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 36)
        self.font_size_spin.setValue(s.font_size_pt)
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, 60)
        self.spacing_spin.setValue(s.paragraph_spacing_pt)

        form.addRow("Logo：", logo_row)
        form.addRow("頁首文字：", self.header_edit)
        form.addRow("頁尾文字：", self.footer_edit)
        form.addRow("日期格式（strftime）：", self.date_format_edit)
        form.addRow("字型：", self.font_name_edit)
        form.addRow("字級：", self.font_size_spin)
        form.addRow("段落間距（pt）：", self.spacing_spin)

        btn_save = QPushButton("儲存 Word 樣式設定")
        mark_primary(btn_save)
        btn_save.clicked.connect(self._on_save_word_settings)
        form.addRow(btn_save)
        return w

    def _on_pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇 Logo 圖片", "", "圖片 (*.png *.jpg *.jpeg)")
        if path:
            self.logo_edit.setText(path)

    def _on_save_word_settings(self):
        s = self.ctx.settings.word_export
        s.logo_path = self.logo_edit.text()
        s.header_text = self.header_edit.text()
        s.footer_text = self.footer_edit.text()
        s.date_format = self.date_format_edit.text()
        s.font_name = self.font_name_edit.text()
        s.font_size_pt = self.font_size_spin.value()
        s.paragraph_spacing_pt = self.spacing_spin.value()
        self.ctx.save_settings()
        QMessageBox.information(self, "已儲存", "Word 輸出樣式設定已儲存")

    # ---------- Gmail 匯入 ----------
    def _build_gmail_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        conn_group = QGroupBox("Gmail 帳號連接（OAuth，唯讀權限）")
        conn_form = QFormLayout(conn_group)
        self.gmail_client_id_edit = QLineEdit()
        self.gmail_client_id_edit.setPlaceholderText("Google Cloud Console 建立的 OAuth Client ID")
        self.gmail_client_secret_edit = QLineEdit()
        self.gmail_client_secret_edit.setEchoMode(QLineEdit.Password)
        self.gmail_client_secret_edit.setPlaceholderText("對應的 Client Secret")
        conn_form.addRow("Client ID：", self.gmail_client_id_edit)
        conn_form.addRow("Client Secret：", self.gmail_client_secret_edit)

        self.gmail_status_label = QLabel(self._gmail_status_text())
        self.gmail_status_label.setWordWrap(True)
        conn_form.addRow("連接狀態：", self.gmail_status_label)

        btn_row = QHBoxLayout()
        self.btn_connect_gmail = QPushButton("連接 Gmail 帳號")
        self.btn_connect_gmail.clicked.connect(self._on_connect_gmail)
        btn_clear_gmail = QPushButton("清除授權")
        mark_danger(btn_clear_gmail)
        btn_clear_gmail.clicked.connect(self._on_clear_gmail)
        btn_row.addWidget(self.btn_connect_gmail)
        btn_row.addWidget(btn_clear_gmail)
        conn_form.addRow(btn_row)
        layout.addWidget(conn_group)

        filter_group = QGroupBox("匯入篩選設定")
        filter_form = QFormLayout(filter_group)
        g = self.ctx.settings.gmail
        self.gmail_sender_edit = QLineEdit(g.sender_email_filter)
        self.gmail_sender_edit.setPlaceholderText("例：xkm_cs@xkd.com.tw")
        self.gmail_subject_edit = QLineEdit(g.subject_keyword)
        self.gmail_subject_edit.setPlaceholderText("選填，例：網路新聞監測, 報紙新聞監測")

        filter_form.addRow("寄件者信箱：", self.gmail_sender_edit)
        filter_form.addRow("主旨關鍵字：", self.gmail_subject_edit)
        subject_hint = QLabel("可用逗號填多組關鍵字（任一符合即匯入）——同時訂閱網路與報紙"
                               "監測報告時兩封一起撈，不必每次切換；版型會自動判別。")
        subject_hint.setObjectName("hintLabel")
        subject_hint.setWordWrap(True)
        filter_form.addRow("", subject_hint)
        filter_form.addRow(QLabel("擷取的起訖日期時間每次匯入時另外於對話框指定，不在此設定"))

        btn_save_gmail = QPushButton("儲存 Gmail 設定")
        mark_primary(btn_save_gmail)
        btn_save_gmail.clicked.connect(self._on_save_gmail_settings)
        filter_form.addRow(btn_save_gmail)
        layout.addWidget(filter_group)
        layout.addStretch()
        return w

    def _gmail_status_text(self) -> str:
        return "✓ 已連接" if load_gmail_credentials() else "未連接"

    def _on_connect_gmail(self):
        client_id = self.gmail_client_id_edit.text().strip()
        client_secret = self.gmail_client_secret_edit.text().strip()
        if not client_id or not client_secret:
            QMessageBox.information(self, "提示", "請先輸入 Client ID 與 Client Secret")
            return
        self.btn_connect_gmail.setEnabled(False)
        self.gmail_status_label.setText("授權中...（請在瀏覽器完成同意畫面）")
        self._gmail_auth_worker = GmailAuthWorker(client_id, client_secret)
        self._gmail_auth_worker.finished_ok.connect(self._on_gmail_connected)
        self._gmail_auth_worker.finished_error.connect(self._on_gmail_connect_error)
        self._gmail_auth_worker.start()

    def _on_gmail_connected(self):
        self.btn_connect_gmail.setEnabled(True)
        self.gmail_status_label.setText(self._gmail_status_text())
        self.gmail_client_secret_edit.clear()
        QMessageBox.information(self, "已連接", "Gmail 帳號授權成功")

    def _on_gmail_connect_error(self, message: str):
        self.btn_connect_gmail.setEnabled(True)
        self.gmail_status_label.setText(f"✗ 連接失敗：{message}")

    def _on_clear_gmail(self):
        confirm = QMessageBox.question(self, "確認清除", "確定要清除已儲存的 Gmail 授權嗎？")
        if confirm == QMessageBox.Yes:
            clear_gmail_credentials()
            self.gmail_status_label.setText(self._gmail_status_text())

    def _on_save_gmail_settings(self):
        g = self.ctx.settings.gmail
        g.sender_email_filter = self.gmail_sender_edit.text().strip()
        g.subject_keyword = self.gmail_subject_edit.text().strip()
        self.ctx.save_settings()
        QMessageBox.information(self, "已儲存", "Gmail 設定已儲存")
