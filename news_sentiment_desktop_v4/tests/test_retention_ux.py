"""測試：留用切換操作性改善（V4.5.1）

使用者回報「留用與否的按鈕很難用」——Qt 原生 ItemIsUserCheckable 只有點中
勾選框那十幾 px 的小方塊才會切換。改為：點「留用」欄整格即切換、可多選列
批次設定（按鈕／空白鍵）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from app.models.news import NewsItem


@pytest.fixture()
def retention_page(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_SENTIMENT_DATA_DIR", str(tmp_path))
    from app.controllers.app_context import AppContext
    from app.ui.pages.retention_page import RetentionPage
    ctx = AppContext()
    ctx.news_repo.upsert_many([
        NewsItem(row_id=f"r{i}", title=f"新聞{i}", source="來源",
                  published_at=f"2026-07-1{i}", retained=True, retention_status="留用")
        for i in range(4)
    ])
    page = RetentionPage(ctx)
    page.reload_data()
    yield page, ctx


def test_click_anywhere_on_retained_cell_toggles(retention_page):
    page, ctx = retention_page
    row = 1
    item = page.model.item_at(row)
    assert item.retained

    page._on_table_clicked(page.model.index(row, 0))   # 模擬點擊整格（非勾選框）
    assert not page.model.item_at(row).retained
    assert ctx.news_repo.get(item.row_id).retained == 0   # 已寫回資料庫

    page._on_table_clicked(page.model.index(row, 0))   # 再點一次切回來
    assert page.model.item_at(row).retained


def test_click_on_other_columns_does_not_toggle(retention_page):
    page, _ = retention_page
    before = page.model.item_at(0).retained
    page._on_table_clicked(page.model.index(0, 1))   # 點標題欄只選取，不切換
    assert page.model.item_at(0).retained == before


def test_bulk_buttons_apply_to_selected_rows(retention_page):
    page, ctx = retention_page
    sel = page.table_view.selectionModel()
    from PySide6.QtCore import QItemSelectionModel
    for row in (0, 2, 3):
        sel.select(page.model.index(row, 0),
                   QItemSelectionModel.Select | QItemSelectionModel.Rows)

    page._set_retained_for_selection(False)
    assert [bool(page.model.item_at(r).retained) for r in range(4)] == [False, True, False, False]
    # 資料庫也要同步（表格預設依時間排序，row 與匯入順序不同，用列上的 row_id 對）
    assert ctx.news_repo.get(page.model.item_at(0).row_id).retained == 0
    assert ctx.news_repo.get(page.model.item_at(1).row_id).retained == 1

    page._set_retained_for_selection(True)   # 同一批選取設回留用
    assert all(page.model.item_at(r).retained for r in (0, 2, 3))


def test_bulk_with_no_selection_shows_hint_not_crash(retention_page):
    page, _ = retention_page
    page.table_view.selectionModel().clearSelection()
    page._set_retained_for_selection(False)
    assert "選取" in page.progress_label.text()


def test_space_toggle_applies_focus_rows_new_value_to_selection(retention_page):
    page, _ = retention_page
    from PySide6.QtCore import QItemSelectionModel
    sel = page.table_view.selectionModel()
    for row in (1, 2):
        sel.select(page.model.index(row, 0),
                   QItemSelectionModel.Select | QItemSelectionModel.Rows)
    page.table_view.setCurrentIndex(page.model.index(1, 1))
    # setCurrentIndex 會清掉多選，重新選回
    for row in (1, 2):
        sel.select(page.model.index(row, 0),
                   QItemSelectionModel.Select | QItemSelectionModel.Rows)

    page._on_space_toggle()   # 焦點列原本留用 → 兩列一起變不留用
    assert not page.model.item_at(1).retained
    assert not page.model.item_at(2).retained


def test_checkbox_still_rendered_without_usercheckable_flag(retention_page):
    """flags 拿掉 ItemIsUserCheckable 後，CheckStateRole 仍要有值（勾選框照常渲染），
    且整格點擊路徑（toggle_retained）取代原生小方塊互動"""
    page, _ = retention_page
    idx = page.model.index(0, 0)
    assert page.model.data(idx, Qt.CheckStateRole) in (Qt.Checked, Qt.Unchecked)
    assert not (page.model.flags(idx) & Qt.ItemIsUserCheckable)
