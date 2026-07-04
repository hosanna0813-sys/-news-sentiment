"""
DropListWidget — 支援「拖放完成後回呼」的 QListWidget

修正議題調整頁的關鍵 bug：原本兩個 QListWidget 之間可以視覺上拖曳，
但 drop 完成後沒有任何程式碼把結果寫回資料庫，造成「假拖曳」。

本元件在 dropEvent 完成（項目實際移入本清單）後發出 items_dropped 訊號，
由頁面層負責把新歸屬寫回資料庫與 feedback log。
"""
from __future__ import annotations

from PySide6.QtWidgets import QListWidget, QAbstractItemView
from PySide6.QtCore import Qt, Signal


class DropListWidget(QListWidget):
    # 參數：被放入本清單的 row_id 清單
    items_dropped = Signal(list)

    def __init__(self, parent=None, row_id_role: int = Qt.UserRole + 1):
        super().__init__(parent)
        self._row_id_role = row_id_role
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    def dropEvent(self, event):
        # 記錄 drop 前本清單已有的 row_id
        before = {self.item(i).data(self._row_id_role) for i in range(self.count())}
        super().dropEvent(event)
        after = {self.item(i).data(self._row_id_role) for i in range(self.count())}
        newly_added = [rid for rid in after - before if rid]
        if newly_added:
            self.items_dropped.emit(newly_added)
