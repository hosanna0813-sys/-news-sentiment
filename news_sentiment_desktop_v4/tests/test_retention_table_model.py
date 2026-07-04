"""
測試：留用勾選不跳回首列、不重新載入整張表格（規格七）

使用 QAbstractTableModel 的 dataChanged 訊號驗證：切換某一列的勾選狀態時，
只有該列（該 cell）觸發 dataChanged，而不是整表 modelReset。
"""
from __future__ import annotations

import pytest


def _make_items(n=5):
    from app.models.news import NewsItem
    return [NewsItem(row_id=f"r{i}", title=f"新聞{i}", retained=True) for i in range(n)]


def test_toggle_retention_does_not_reset_model(qapp):
    from app.ui.widgets.news_table_model import NewsTableModel
    from PySide6.QtCore import Qt

    items = _make_items(5)
    model = NewsTableModel(items)

    reset_calls = {"count": 0}
    data_changed_calls = []

    model.modelAboutToBeReset.connect(lambda: reset_calls.__setitem__("count", reset_calls["count"] + 1))
    model.dataChanged.connect(lambda tl, br, roles: data_changed_calls.append((tl.row(), br.row())))

    # 切換第 3 列（非第一列）的留用勾選
    target_row = 3
    index = model.index(target_row, 0)
    model.setData(index, Qt.Unchecked, Qt.CheckStateRole)

    assert reset_calls["count"] == 0, "勾選變更不應觸發整表 modelReset"
    assert data_changed_calls, "應觸發 dataChanged 訊號"
    for top_row, bottom_row in data_changed_calls:
        assert top_row == target_row == bottom_row, "dataChanged 應只涵蓋被勾選的那一列，不影響其他列"

    # 驗證資料確實已更新，且其他列未受影響
    assert model.item_at(target_row).retained is False
    assert model.item_at(0).retained is True


def test_retention_toggled_signal_emits_correct_row_id(qapp):
    from app.ui.widgets.news_table_model import NewsTableModel
    from PySide6.QtCore import Qt

    items = _make_items(3)
    model = NewsTableModel(items)

    emitted = []
    model.retention_toggled.connect(lambda row_id, val: emitted.append((row_id, val)))

    index = model.index(1, 0)
    model.setData(index, Qt.Unchecked, Qt.CheckStateRole)

    assert emitted == [("r1", False)]
