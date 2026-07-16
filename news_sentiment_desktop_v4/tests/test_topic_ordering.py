"""測試：議題人工拖曳排序（V4.6.0）

使用者需求：議題調整頁可拖曳調整議題排列順序。順序存於 topics.display_order
（0=尚未手動排序，依 created_at 排最後），Word 匯出、綜整頁、議題清單
（都走 list_active()）自動跟隨。
"""
from __future__ import annotations

import pytest

from app.models.topic import Topic


def _topic(tid, name, created_at):
    return Topic(topic_id=tid, topic_name=name, created_at=created_at, updated_at=created_at)


def test_save_display_order_reorders_list_active(topic_repo):
    topic_repo.upsert_many([
        _topic("ta", "議題A", 100.0), _topic("tb", "議題B", 200.0), _topic("tc", "議題C", 300.0),
    ])
    # 尚未手動排序：依建立時間
    assert [t.topic_id for t in topic_repo.list_active()] == ["ta", "tb", "tc"]

    topic_repo.save_display_order(["tc", "ta", "tb"])
    assert [t.topic_id for t in topic_repo.list_active()] == ["tc", "ta", "tb"]


def test_new_topic_after_manual_sort_appends_at_end(topic_repo):
    topic_repo.upsert_many([_topic("ta", "議題A", 100.0), _topic("tb", "議題B", 200.0)])
    topic_repo.save_display_order(["tb", "ta"])
    # 排序後才新增的議題（display_order=0）附在最後，不插隊
    topic_repo.upsert_one(_topic("tnew", "後來新增的議題", 300.0))
    assert [t.topic_id for t in topic_repo.list_active()] == ["tb", "ta", "tnew"]


@pytest.fixture()
def adjustment_page(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("NEWS_SENTIMENT_DATA_DIR", str(tmp_path))
    from app.controllers.app_context import AppContext
    from app.ui.pages.topic_adjustment_page import TopicAdjustmentPage
    ctx = AppContext()
    ctx.topic_repo.upsert_many([
        _topic("t1", "議題一", 100.0), _topic("t2", "議題二", 200.0), _topic("t3", "議題三", 300.0),
    ])
    page = TopicAdjustmentPage(ctx)
    yield page, ctx


def test_drag_reorder_persists_to_db(adjustment_page):
    from app.ui.pages.topic_adjustment_page import TOPIC_ID_ROLE
    page, ctx = adjustment_page
    assert page.topic_list.count() == 3

    # 模擬拖曳：把第 3 列移到最上面（QListWidget InternalMove 的結果狀態），
    # 再呼叫 rowsMoved 對應的處理器
    item = page.topic_list.takeItem(2)
    page.topic_list.insertItem(0, item)
    page._on_topic_order_changed()

    assert [t.topic_id for t in ctx.topic_repo.list_active()] == ["t3", "t1", "t2"]
    # 重新整理後清單維持新順序（讀回 display_order）
    page.refresh_all()
    ids = [page.topic_list.item(r).data(TOPIC_ID_ROLE) for r in range(page.topic_list.count())]
    assert ids == ["t3", "t1", "t2"]


def test_topic_selection_helpers_follow_list(adjustment_page):
    page, ctx = adjustment_page
    page.topic_list.setCurrentRow(1)
    assert page._current_topic_id() == "t2"
    assert page._current_topic_name() == "議題二"
