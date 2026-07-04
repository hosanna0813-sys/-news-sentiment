"""
人工議題調整工作區 — 對應規格書 十

實作說明（本版本的可視化調整方式）：
    左側「未分類／正文不足新聞」與中間「目前選定議題成員」皆為可拖曳的
    QListWidget，兩者間可直接互相拖曳新聞以完成「移動」；多選後可用
    右側按鈕「建立新議題」；「合併議題」「拆分議題」「改名」「刪除空議題」
    「標示不納入任何議題」則以明確按鈕操作，避免多欄位同時拖放造成誤觸，
    同時仍完整滿足規格十的 8 項操作能力。所有操作即時寫回資料庫與
    feedback log，不留下重複歸屬或幽靈資料。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QListWidgetItem,
    QComboBox, QAbstractItemView, QInputDialog, QMessageBox, QTextEdit, QSplitter,
)
from PySide6.QtCore import Qt

from app.controllers.app_context import AppContext
from app.models.topic import Topic
from app.utils.text_utils import new_id
from app.services.feedback.feedback_service import log_feedback
from app.ui.widgets.drop_list_widget import DropListWidget

ROW_ID_ROLE = Qt.UserRole + 1


class TopicAdjustmentPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("步驟 5：人工調整議題")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)

        # ---- 左：未分類 / 正文不足 ----
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.addWidget(QLabel("未分類 / 正文不足新聞"))
        self.unclassified_list = DropListWidget(row_id_role=ROW_ID_ROLE)
        self.unclassified_list.itemSelectionChanged.connect(self._on_item_selected_preview)
        self.unclassified_list.items_dropped.connect(self._on_dropped_to_unclassified)
        left_layout.addWidget(self.unclassified_list)
        splitter.addWidget(left_box)

        # ---- 中：選定議題成員 ----
        mid_box = QWidget()
        mid_layout = QVBoxLayout(mid_box)
        topic_row = QHBoxLayout()
        topic_row.addWidget(QLabel("目前議題："))
        self.topic_combo = QComboBox()
        self.topic_combo.currentIndexChanged.connect(self._on_topic_changed)
        topic_row.addWidget(self.topic_combo, 1)
        mid_layout.addLayout(topic_row)
        from PySide6.QtWidgets import QCheckBox
        self.chk_low_conf_only = QCheckBox("只顯示低信心新聞（AI 不確定歸屬，建議優先確認）")
        self.chk_low_conf_only.stateChanged.connect(lambda *_: self._refresh_members())
        mid_layout.addWidget(self.chk_low_conf_only)
        self.lbl_low_conf_count = QLabel("")
        mid_layout.addWidget(self.lbl_low_conf_count)
        self.member_list = DropListWidget(row_id_role=ROW_ID_ROLE)
        self.member_list.itemSelectionChanged.connect(self._on_item_selected_preview)
        self.member_list.items_dropped.connect(self._on_dropped_to_member)
        mid_layout.addWidget(self.member_list)
        splitter.addWidget(mid_box)

        # ---- 右：操作 + 預覽 ----
        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)

        btn_new_topic = QPushButton("將左側選取新聞建立新議題")
        btn_new_topic.clicked.connect(self._on_create_topic_from_left)
        right_layout.addWidget(btn_new_topic)

        btn_move_to_topic = QPushButton("將左側選取新聞加入目前議題")
        btn_move_to_topic.clicked.connect(self._on_move_left_to_topic)
        right_layout.addWidget(btn_move_to_topic)

        btn_unassign = QPushButton("將目前議題選取新聞標示為不納入任何議題")
        btn_unassign.clicked.connect(self._on_unassign_selected)
        right_layout.addWidget(btn_unassign)

        btn_split = QPushButton("將目前議題選取新聞拆分為新議題")
        btn_split.clicked.connect(self._on_split_topic)
        right_layout.addWidget(btn_split)

        btn_rename = QPushButton("修改目前議題名稱")
        btn_rename.clicked.connect(self._on_rename_topic)
        right_layout.addWidget(btn_rename)

        merge_row = QHBoxLayout()
        self.merge_target_combo = QComboBox()
        merge_row.addWidget(self.merge_target_combo, 1)
        btn_merge = QPushButton("合併目前議題到→")
        btn_merge.clicked.connect(self._on_merge_topic)
        merge_row.addWidget(btn_merge)
        right_layout.addLayout(merge_row)

        btn_delete_empty = QPushButton("刪除空議題")
        btn_delete_empty.clicked.connect(self._on_delete_empty_topics)
        right_layout.addWidget(btn_delete_empty)

        right_layout.addWidget(QLabel("新聞正文 / AI 分群理由預覽："))
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        right_layout.addWidget(self.preview_text, 1)

        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, 1)

    # ---------- 資料載入 ----------
    def refresh_all(self):
        self._refresh_topic_combo()
        self._refresh_unclassified()
        self._refresh_members()

    def _refresh_topic_combo(self):
        current = self.topic_combo.currentData()
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        self.merge_target_combo.clear()
        topics = self.ctx.topic_repo.list_active()
        for t in topics:
            self.topic_combo.addItem(t.topic_name, t.topic_id)
            self.merge_target_combo.addItem(t.topic_name, t.topic_id)
        if current:
            idx = self.topic_combo.findData(current)
            if idx >= 0:
                self.topic_combo.setCurrentIndex(idx)
        self.topic_combo.blockSignals(False)

    def _refresh_unclassified(self):
        self.unclassified_list.clear()
        items = [it for it in self.ctx.news_repo.list_all()
                 if it.retained and not it.final_topic_id]
        for it in items:
            li = QListWidgetItem(f"{it.title}（{it.source}）")
            li.setData(ROW_ID_ROLE, it.row_id)
            self.unclassified_list.addItem(li)

    LOW_CONFIDENCE_THRESHOLD = 0.7

    def _refresh_members(self):
        self.member_list.clear()
        topic_id = self.topic_combo.currentData()
        if not topic_id:
            self.lbl_low_conf_count.setText("")
            return
        from PySide6.QtGui import QColor, QBrush
        items = self.ctx.news_repo.list_by_topic(topic_id)
        low_conf_count = 0
        only_low = self.chk_low_conf_only.isChecked()
        for it in items:
            conf = it.clustering_confidence or 0.0
            # 低信心 = AI 分群信心低於門檻；conf==0 代表未經 AI 評分（人工建立/指定），不標黃
            is_low = 0 < conf < self.LOW_CONFIDENCE_THRESHOLD
            if is_low:
                low_conf_count += 1
            if only_low and not is_low:
                continue
            label = f"{it.title}（{it.source}）"
            if is_low:
                label = f"⚠ [信心 {conf:.2f}] " + label
            li = QListWidgetItem(label)
            li.setData(ROW_ID_ROLE, it.row_id)
            if is_low:
                li.setBackground(QBrush(QColor(255, 249, 196)))  # 淡黃底：優先人工確認
            self.member_list.addItem(li)
        self.lbl_low_conf_count.setText(
            f"本議題共 {len(items)} 則，其中 {low_conf_count} 則為低信心（標黃）"
            if items else "")

    def _on_topic_changed(self):
        self._refresh_members()

    # ---------- 拖曳持久化（修正原「假拖曳」bug）----------
    def _on_dropped_to_member(self, row_ids: List[str]):
        """新聞被拖入「目前議題成員」清單：寫回 final_topic_id 與 feedback log"""
        topic_id = self.topic_combo.currentData()
        if not topic_id:
            QMessageBox.warning(self, "提示", "目前未選定議題，拖曳無法生效，已還原畫面")
            self.refresh_all()
            return
        topic_name = self.topic_combo.currentText()
        self._assign_news_to_topic(row_ids, topic_id, topic_name, action="human_drag_assign")

    def _on_dropped_to_unclassified(self, row_ids: List[str]):
        """新聞被拖回「未分類」清單：清除議題歸屬並記錄回饋"""
        for rid in row_ids:
            it = self.ctx.news_repo.get(rid)
            old_topic = it.final_topic_name if it else ""
            self.ctx.news_repo.update_fields(rid, {"final_topic_id": "", "final_topic_name": ""})
            log_feedback(self.ctx.feedback_repo, batch_id="", entity_type="clustering", entity_id=rid,
                         ai_original_value=old_topic, human_final_value="（不納入任何議題）",
                         action="human_drag_unassign", operator="user")

    # ---------- 預覽 ----------
    def _on_item_selected_preview(self):
        sender = self.sender()
        items = sender.selectedItems() if sender else []
        if not items:
            return
        row_id = items[0].data(ROW_ID_ROLE)
        it = self.ctx.news_repo.get(row_id)
        if not it:
            return
        text = f"【{it.title}】\n分群理由：{it.clustering_reason or '（無）'}\n\n正文：\n{it.body_text or '（無正文）'}"
        self.preview_text.setPlainText(text)

    # ---------- 操作 ----------
    def _selected_row_ids(self, list_widget: QListWidget) -> List[str]:
        return [item.data(ROW_ID_ROLE) for item in list_widget.selectedItems()]

    def _on_create_topic_from_left(self):
        row_ids = self._selected_row_ids(self.unclassified_list)
        if not row_ids:
            QMessageBox.information(self, "提示", "請先在左側選取新聞")
            return
        name, ok = QInputDialog.getText(self, "建立新議題", "議題名稱（建議格式：主體＋行動／事件＋核心爭點）：")
        if not ok or not name.strip():
            return
        topic = Topic(topic_id=new_id("ftopic_"), topic_name=name.strip())
        self.ctx.topic_repo.upsert_one(topic)
        self._assign_news_to_topic(row_ids, topic.topic_id, topic.topic_name, action="human_create_topic")
        self.refresh_all()

    def _on_move_left_to_topic(self):
        topic_id = self.topic_combo.currentData()
        if not topic_id:
            QMessageBox.information(self, "提示", "請先選擇目前議題")
            return
        row_ids = self._selected_row_ids(self.unclassified_list)
        if not row_ids:
            QMessageBox.information(self, "提示", "請先在左側選取新聞")
            return
        topic_name = self.topic_combo.currentText()
        self._assign_news_to_topic(row_ids, topic_id, topic_name, action="human_drag_assign")
        self.refresh_all()

    def _on_unassign_selected(self):
        row_ids = self._selected_row_ids(self.member_list)
        if not row_ids:
            return
        for rid in row_ids:
            it = self.ctx.news_repo.get(rid)
            old_topic = it.final_topic_name if it else ""
            self.ctx.news_repo.update_fields(rid, {"final_topic_id": "", "final_topic_name": ""})
            log_feedback(self.ctx.feedback_repo, batch_id="", entity_type="clustering", entity_id=rid,
                         ai_original_value=old_topic, human_final_value="（不納入任何議題）",
                         action="human_unassign", operator="user")
        self.refresh_all()

    def _on_split_topic(self):
        row_ids = self._selected_row_ids(self.member_list)
        if not row_ids:
            QMessageBox.information(self, "提示", "請先在中間選取要拆分的新聞")
            return
        name, ok = QInputDialog.getText(self, "拆分為新議題", "新議題名稱：")
        if not ok or not name.strip():
            return
        new_topic = Topic(topic_id=new_id("ftopic_"), topic_name=name.strip())
        self.ctx.topic_repo.upsert_one(new_topic)
        self._assign_news_to_topic(row_ids, new_topic.topic_id, new_topic.topic_name, action="human_split")
        self.refresh_all()

    def _on_rename_topic(self):
        topic_id = self.topic_combo.currentData()
        if not topic_id:
            return
        old_name = self.topic_combo.currentText()
        name, ok = QInputDialog.getText(self, "修改議題名稱", "新名稱：", text=old_name)
        if not ok or not name.strip():
            return
        self.ctx.topic_repo.update_fields(topic_id, {"topic_name": name.strip()})
        for it in self.ctx.news_repo.list_by_topic(topic_id):
            self.ctx.news_repo.update_fields(it.row_id, {"final_topic_name": name.strip()})
        log_feedback(self.ctx.feedback_repo, batch_id="", entity_type="topic_naming", entity_id=topic_id,
                     ai_original_value=old_name, human_final_value=name.strip(),
                     action="human_rename", operator="user")
        self.refresh_all()

    def _on_merge_topic(self):
        source_id = self.topic_combo.currentData()
        target_id = self.merge_target_combo.currentData()
        if not source_id or not target_id or source_id == target_id:
            QMessageBox.information(self, "提示", "請選擇兩個不同的議題進行合併")
            return
        target_name = self.merge_target_combo.currentText()
        source_name = self.topic_combo.currentText()
        members = self.ctx.news_repo.list_by_topic(source_id)
        self._assign_news_to_topic([it.row_id for it in members], target_id, target_name,
                                     action="human_merge")
        self.ctx.topic_repo.mark_merged(source_id, target_id)
        log_feedback(self.ctx.feedback_repo, batch_id="", entity_type="clustering", entity_id=source_id,
                     ai_original_value=source_name, human_final_value=target_name,
                     action="human_merge_topic", operator="user")
        self.refresh_all()

    def _on_delete_empty_topics(self):
        deleted = 0
        for t in self.ctx.topic_repo.list_active():
            members = self.ctx.news_repo.list_by_topic(t.topic_id)
            if not members:
                self.ctx.topic_repo.delete(t.topic_id)
                deleted += 1
        QMessageBox.information(self, "完成", f"已刪除 {deleted} 個空議題")
        self.refresh_all()

    def _assign_news_to_topic(self, row_ids: List[str], topic_id: str, topic_name: str, action: str):
        for rid in row_ids:
            it = self.ctx.news_repo.get(rid)
            old_topic = it.final_topic_name if it else ""
            self.ctx.news_repo.update_fields(rid, {
                "final_topic_id": topic_id, "final_topic_name": topic_name,
                "clustering_confidence": 0,  # 人工確認過的歸屬，清除低信心標記
            })
            log_feedback(self.ctx.feedback_repo, batch_id="", entity_type="clustering", entity_id=rid,
                         ai_original_value=old_topic, human_final_value=topic_name,
                         action=action, operator="user")
