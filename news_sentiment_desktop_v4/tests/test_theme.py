"""測試：全域主題（V4.5.0）與分組導覽欄

重點是導覽對應：側欄插入「分組標題列」後，list row 不再等於 stack index，
改以 item 的 Qt.UserRole data 對應頁面——這是本次美化唯一有行為風險的點，
用 MainWindow 冒煙測試逐項驗證。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from app.ui.theme import build_stylesheet, apply_theme, mark_primary, mark_danger


def test_stylesheet_nonempty_and_braces_balanced():
    qss = build_stylesheet()
    assert len(qss) > 1000
    assert qss.count("{") == qss.count("}")
    # 語意選擇器都有定義（頁面端只做 objectName 標記，樣式集中在 QSS）
    for selector in ("#navList", "#pageTitle", "#alertLabel", "#sidebar",
                      'QPushButton[primary="true"]', 'QPushButton[danger="true"]'):
        assert selector in qss


def test_apply_theme_and_button_markers(qapp):
    apply_theme(qapp)
    assert qapp.styleSheet()

    from PySide6.QtWidgets import QPushButton
    btn = QPushButton("主要")
    mark_primary(btn)
    assert btn.property("primary") == "true"
    btn2 = QPushButton("危險")
    mark_danger(btn2)
    assert btn2.property("danger") == "true"


@pytest.fixture()
def main_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_SENTIMENT_DATA_DIR", str(tmp_path))
    from app.controllers.app_context import AppContext
    from app.ui.main_window import MainWindow
    ctx = AppContext()
    win = MainWindow(ctx)
    yield win
    win.close()


def test_nav_items_map_to_correct_stack_pages(main_window):
    """逐一點選每個可選導覽項，stack 都要切到對應頁（分組列造成 row 位移不可影響）"""
    from app.ui.main_window import NAV_ITEMS
    nav = main_window.nav_list
    seen_pages = []
    for row in range(nav.count()):
        item = nav.item(row)
        page_index = item.data(Qt.UserRole)
        if page_index is None:
            continue  # 分組標題列
        nav.setCurrentItem(item)
        assert main_window.stack.currentIndex() == page_index, \
            f"導覽項「{item.text()}」應切到第 {page_index} 頁"
        seen_pages.append(page_index)
    assert seen_pages == list(range(len(NAV_ITEMS)))   # 10 頁都可到達、順序正確


def test_nav_group_headers_are_not_selectable(main_window):
    from app.ui.main_window import NAV_GROUPS
    nav = main_window.nav_list
    headers = [nav.item(r) for r in range(nav.count())
               if nav.item(r).data(Qt.UserRole) is None]
    assert len(headers) == len(NAV_GROUPS)
    for h in headers:
        assert h.flags() == Qt.NoItemFlags


def test_initial_selection_is_first_page(main_window):
    assert main_window.stack.currentIndex() == 0
    current = main_window.nav_list.currentItem()
    assert current is not None and current.data(Qt.UserRole) == 0
