"""測試：assign_news_to_topic()／unassign_news_from_topic()

這兩條規則原本寫死在 app/ui/pages/topic_adjustment_page.py 的 Qt slot 裡
（建立新議題／移入議題／拆分議題／合併議題各自呼叫一份幾乎相同的迴圈，
拖曳與按鈕移出議題也各自重複實作一次）——沒有 QApplication 就無法單獨測試，
且同一份邏輯在同一個檔案裡重複了好幾次。抽成 clustering_service 的純函式後，
這裡直接驗證資料庫寫入與 feedback log，不需要啟動任何 Qt 元件。
"""
from __future__ import annotations

from app.models.news import NewsItem
from app.services.clustering.clustering_service import (
    assign_news_to_topic, unassign_news_from_topic,
)


def _make_item(row_id: str, title: str = "測試新聞", final_topic_name: str = "") -> NewsItem:
    return NewsItem(row_id=row_id, title=title, source="測試媒體",
                     published_at="2026-07-03", final_topic_name=final_topic_name)


def test_assign_news_to_topic_writes_fields_and_clears_confidence(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1", final_topic_name=""))
    news_repo.update_fields("r1", {"clustering_confidence": 0.4})

    assign_news_to_topic(news_repo, feedback_repo, ["r1"], "t1", "議題A", action="human_create_topic")

    item = news_repo.get("r1")
    assert item.final_topic_id == "t1"
    assert item.final_topic_name == "議題A"
    assert item.clustering_confidence == 0

    entries = feedback_repo.list_all(entity_type="clustering")
    assert len(entries) == 1
    assert entries[0].action == "human_create_topic"
    assert entries[0].human_final_value == "議題A"


def test_assign_news_to_topic_handles_multiple_row_ids(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1"))
    news_repo.upsert_one(_make_item("r2"))

    assign_news_to_topic(news_repo, feedback_repo, ["r1", "r2"], "t1", "議題A", action="human_merge")

    assert news_repo.get("r1").final_topic_id == "t1"
    assert news_repo.get("r2").final_topic_id == "t1"
    assert len(feedback_repo.list_all(entity_type="clustering")) == 2


def test_unassign_news_from_topic_clears_topic_fields(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1", final_topic_name="議題A"))
    news_repo.update_fields("r1", {"final_topic_id": "t1"})

    unassign_news_from_topic(news_repo, feedback_repo, ["r1"], action="human_unassign")

    item = news_repo.get("r1")
    assert item.final_topic_id == ""
    assert item.final_topic_name == ""

    entries = feedback_repo.list_all(entity_type="clustering")
    assert entries[0].action == "human_unassign"
    assert entries[0].ai_original_value == "議題A"
    assert entries[0].human_final_value == "（不納入任何議題）"


def test_unassign_news_from_topic_records_drag_action_label(news_repo, feedback_repo):
    news_repo.upsert_one(_make_item("r1", final_topic_name="議題A"))

    unassign_news_from_topic(news_repo, feedback_repo, ["r1"], action="human_drag_unassign")

    assert feedback_repo.list_all(entity_type="clustering")[0].action == "human_drag_unassign"
