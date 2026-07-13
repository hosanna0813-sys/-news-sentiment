"""
NewsTableModel — QAbstractTableModel 實作

規格七要求：
    - 改勾選不得讓清單跳回第一列
    - 改勾選不得重新載入整張表格
    - 點選列後預覽必須穩定更新
    - 不可因同一新聞 ID 重複而回寫錯誤

實作重點：使用 QAbstractTableModel + dataChanged 局部更新，而非每次都
呼叫 beginResetModel/endResetModel 整表重載；勾選變更只針對該 cell
發出 dataChanged 訊號，Qt View 只會重繪該列，不會重置捲動位置或選取狀態。
"""
from __future__ import annotations

from typing import List, Optional, Callable
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from app.models.news import NewsItem

COLUMNS = [
    ("retained", "留用"),
    ("title", "標題"),
    ("source", "來源"),
    ("published_at", "時間"),
    ("url", "連結"),
    ("retention_status", "AI留用判斷"),
    ("body_fetch_status", "正文狀態"),
    ("priority_stars", "優先級"),
]


class NewsTableModel(QAbstractTableModel):
    retention_toggled = Signal(str, bool)   # row_id, new_value

    def __init__(self, items: Optional[List[NewsItem]] = None, parent=None):
        super().__init__(parent)
        self._items: List[NewsItem] = items or []
        self._row_id_index = {it.row_id: i for i, it in enumerate(self._items)}

    # ---------- Qt 必要介面 ----------
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section][1]
        return str(section + 1)

    def flags(self, index: QModelIndex):
        # 「留用」欄刻意不設 ItemIsUserCheckable：Qt 原生行為只有點中勾選框
        # 那十幾px 的小方塊才會切換，實際操作很難點（使用者回報）。改由
        # retention_page 監聽整格點擊呼叫 toggle_retained()——勾選框仍照常
        # 渲染（data() 有回 CheckStateRole 就會畫），但切換範圍是整個儲存格。
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        field, _ = COLUMNS[index.column()]

        if field == "retained":
            if role == Qt.CheckStateRole:
                return Qt.Checked if item.retained else Qt.Unchecked
            return None

        if field == "priority_stars" and role == Qt.DisplayRole:
            stars = getattr(item, field, 0) or 0
            return "★" * stars + "☆" * (5 - stars) if stars else "-"

        if role == Qt.DisplayRole:
            return getattr(item, field, "")
        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole):
        field, _ = COLUMNS[index.column()]
        if field == "retained" and role == Qt.CheckStateRole:
            item = self._items[index.row()]
            new_val = value == Qt.Checked
            item.retained = new_val
            item.retention_status = "留用" if new_val else "人工不留用"
            item.retention_judged_by = "human"
            # 只針對這個 cell 發出變更訊號，不整表重載（滿足「不得重新載入整張表格」）
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            status_index = self.index(index.row(), 5)
            self.dataChanged.emit(status_index, status_index, [Qt.DisplayRole])
            self.retention_toggled.emit(item.row_id, new_val)
            return True
        return False

    # ---------- 自訂輔助方法 ----------
    def toggle_retained(self, row: int) -> None:
        """切換某列留用狀態（整格點擊／空白鍵用），走與 setData 相同的路徑"""
        item = self.item_at(row)
        if item is None:
            return
        self.setData(self.index(row, 0),
                     Qt.Unchecked if item.retained else Qt.Checked, Qt.CheckStateRole)

    def set_retained(self, row: int, value: bool) -> None:
        """把某列留用狀態設為指定值；已是該值時不動作（避免重複寫庫與回饋紀錄）"""
        item = self.item_at(row)
        if item is None or bool(item.retained) == value:
            return
        self.setData(self.index(row, 0),
                     Qt.Checked if value else Qt.Unchecked, Qt.CheckStateRole)

    def item_at(self, row: int) -> Optional[NewsItem]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def set_items(self, items: List[NewsItem]) -> None:
        """僅在切換篩選條件／重新整理時才整表重載"""
        self.beginResetModel()
        self._items = items
        self._row_id_index = {it.row_id: i for i, it in enumerate(self._items)}
        self.endResetModel()

    def update_item_field(self, row_id: str, field: str, value) -> None:
        """局部更新（例如 AI 完成留用初判後回填結果），不整表重載"""
        row = self._row_id_index.get(row_id)
        if row is None:
            return
        setattr(self._items[row], field, value)
        col = next((i for i, (f, _) in enumerate(COLUMNS) if f == field), None)
        if col is not None:
            idx = self.index(row, col)
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.CheckStateRole])

    def refresh_row(self, row_id: str) -> None:
        row = self._row_id_index.get(row_id)
        if row is None:
            return
        left = self.index(row, 0)
        right = self.index(row, len(COLUMNS) - 1)
        self.dataChanged.emit(left, right)
