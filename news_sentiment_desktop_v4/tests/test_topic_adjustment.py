"""測試：議題移動／合併／拆分（規格十：拖曳後同步更新，不留重複歸屬或幽靈資料）"""
from __future__ import annotations

from app.models.news import NewsItem
from app.models.topic import Topic


def _seed_topic_with_members(news_repo, topic_repo, topic_id, topic_name, n=3):
    topic = Topic(topic_id=topic_id, topic_name=topic_name)
    topic_repo.upsert_one(topic)
    items = []
    for i in range(n):
        it = NewsItem(row_id=f"{topic_id}_r{i}", title=f"{topic_name}新聞{i}",
                       final_topic_id=topic_id, final_topic_name=topic_name)
        news_repo.upsert_one(it)
        items.append(it)
    return topic, items


def test_move_news_between_topics(news_repo, topic_repo):
    topic_a, items_a = _seed_topic_with_members(news_repo, topic_repo, "ta", "議題A", n=2)
    topic_b, items_b = _seed_topic_with_members(news_repo, topic_repo, "tb", "議題B", n=2)

    # 將議題A的一則新聞移到議題B
    moved_id = items_a[0].row_id
    news_repo.update_fields(moved_id, {"final_topic_id": "tb", "final_topic_name": "議題B"})

    a_members = news_repo.list_by_topic("ta")
    b_members = news_repo.list_by_topic("tb")
    assert len(a_members) == 1
    assert len(b_members) == 3
    assert moved_id in [m.row_id for m in b_members]
    assert moved_id not in [m.row_id for m in a_members]


def test_merge_topics_no_ghost_data(news_repo, topic_repo):
    topic_a, items_a = _seed_topic_with_members(news_repo, topic_repo, "ta", "議題A", n=2)
    topic_b, items_b = _seed_topic_with_members(news_repo, topic_repo, "tb", "議題B", n=3)

    # 合併議題A到議題B：所有議題A成員改為議題B，議題A標記為 merged（非刪除，可追溯）
    for it in items_a:
        news_repo.update_fields(it.row_id, {"final_topic_id": "tb", "final_topic_name": "議題B"})
    topic_repo.mark_merged("ta", "tb")

    merged_topic = topic_repo.get("ta")
    assert merged_topic.status == "merged"
    assert merged_topic.merged_into == "tb"

    b_members = news_repo.list_by_topic("tb")
    assert len(b_members) == 5  # 2 + 3，無重複、無遺失
    a_members = news_repo.list_by_topic("ta")
    assert len(a_members) == 0  # 不留幽靈成員

    active_topics = topic_repo.list_active()
    active_ids = {t.topic_id for t in active_topics}
    assert "ta" not in active_ids  # 已合併議題不再出現於 active 清單
    assert "tb" in active_ids


def test_split_topic_creates_new_topic_without_duplicating_membership(news_repo, topic_repo):
    topic_a, items_a = _seed_topic_with_members(news_repo, topic_repo, "ta", "議題A", n=4)

    # 拆分：選取其中 2 則建立新議題
    to_split = [items_a[0].row_id, items_a[1].row_id]
    new_topic = Topic(topic_id="ta_split", topic_name="議題A－子議題")
    topic_repo.upsert_one(new_topic)
    for rid in to_split:
        news_repo.update_fields(rid, {"final_topic_id": "ta_split", "final_topic_name": "議題A－子議題"})

    original_members = news_repo.list_by_topic("ta")
    split_members = news_repo.list_by_topic("ta_split")

    assert len(original_members) == 2
    assert len(split_members) == 2
    # 每則新聞只能歸屬一個議題，不可同時出現在兩邊（無重複歸屬）
    original_ids = {m.row_id for m in original_members}
    split_ids = {m.row_id for m in split_members}
    assert original_ids.isdisjoint(split_ids)


def test_delete_empty_topic(news_repo, topic_repo):
    topic = Topic(topic_id="empty1", topic_name="空議題")
    topic_repo.upsert_one(topic)
    assert news_repo.list_by_topic("empty1") == []

    topic_repo.delete("empty1")
    active_ids = {t.topic_id for t in topic_repo.list_active()}
    assert "empty1" not in active_ids
