"""測試：分群粒度設定＋命名/合併回饋學習閉環（V4.4.0）

涵蓋：
    1. 粒度指示注入 cluster_batch / merge_candidate_topics 的 prompt
    2. 使用者自訂舊模板（沒有 {granularity_section} 佔位符）不炸、行為不變
    3. 共用 few-shot 範例組裝：搬移／議題合併（human_merge_topic 不再產生
       「新聞《ftopic_xxx》」雜訊）／改名（topic_naming 過去被整個丟掉）
    4. 網頁版 rename/merge 補記 feedback
    5. ApiSettings.clustering_granularity 預設值
"""
from __future__ import annotations

import pytest

from app.models.news import NewsItem
from app.prompts.clustering_prompt import (
    CLUSTERING_USER_TEMPLATE, MERGE_USER_TEMPLATE, GRANULARITY_INSTRUCTIONS,
)
from app.services.clustering.clustering_service import (
    cluster_batch, merge_candidate_topics, granularity_section,
    build_clustering_human_examples, build_naming_examples, build_combined_clustering_examples,
)
from app.services.feedback.feedback_service import log_feedback


class _CaptureGateway:
    """記下 call_with_tool 收到的 prompt，回傳最小合法分群/合併結果"""

    def __init__(self, data):
        self._data = data
        self.captured = {}

    def call_with_tool(self, task, system_prompt, user_content, tool_name, tool_schema):
        self.captured = {"task": task, "system_prompt": system_prompt,
                          "user_content": user_content}

        class _R:
            data = self._data
        return _R()


def _items():
    return [NewsItem(row_id="r1", title="測試新聞", source="測試媒體",
                      published_at="2026-07-10", body_text="正文" * 100)]


# ---------------------------------------------------------------------------
# 1/2. 粒度注入
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("granularity", ["fine", "standard", "coarse"])
def test_cluster_batch_injects_granularity(granularity):
    gw = _CaptureGateway({"topics": [{"topic_name": "議題", "member_row_ids": ["r1"],
                                       "reason": "", "confidence": 0.9}]})
    cluster_batch(gw, _items(), "sys", CLUSTERING_USER_TEMPLATE, "tool",
                   {"type": "object"}, granularity=granularity)
    assert GRANULARITY_INSTRUCTIONS[granularity] in gw.captured["user_content"]
    assert "{granularity_section}" not in gw.captured["user_content"]


def test_unknown_granularity_falls_back_to_standard():
    assert GRANULARITY_INSTRUCTIONS["standard"] in granularity_section("bogus-value")


def test_merge_injects_granularity_and_supports_legacy_template():
    candidates = [{"topic_id": "t1", "topic_name": "議題A", "member_row_ids": ["r1"],
                    "sample_titles": ["標題"]}]
    data = {"merged_groups": [{"final_topic_name": "議題A", "source_topic_ids": ["t1"],
                                "reason": ""}]}
    gw = _CaptureGateway(data)
    merge_candidate_topics(gw, candidates, "sys", MERGE_USER_TEMPLATE, "tool",
                            {"type": "object"}, granularity="fine")
    assert GRANULARITY_INSTRUCTIONS["fine"] in gw.captured["user_content"]

    # 使用者自訂過的舊模板（V4.3 以前、只有 candidate_topics_json 佔位符）：
    # 原本用 str.format 會 KeyError，改 safe_format 後正常運作、單純沒有粒度段
    legacy_template = "候選議題：{candidate_topics_json}\n請合併。"
    gw2 = _CaptureGateway(data)
    result = merge_candidate_topics(gw2, candidates, "sys", legacy_template, "tool",
                                     {"type": "object"}, granularity="fine")
    assert result[0]["final_topic_name"] == "議題A"
    assert "議題A" in gw2.captured["user_content"]
    assert "{granularity_section}" not in gw2.captured["user_content"]


def test_default_templates_contain_granularity_placeholder():
    assert "{granularity_section}" in CLUSTERING_USER_TEMPLATE
    assert "{granularity_section}" in MERGE_USER_TEMPLATE


# ---------------------------------------------------------------------------
# 3. 共用 few-shot 範例組裝
# ---------------------------------------------------------------------------
def test_examples_include_moves_and_topic_merges(feedback_repo, news_repo):
    news_repo.upsert_one(NewsItem(row_id="r1", title="戶政事務所服務爭議", source="來源",
                                    published_at="2026-07-10"))
    log_feedback(feedback_repo, batch_id="", entity_type="clustering", entity_id="r1",
                 ai_original_value="舊議題", human_final_value="新議題", action="human_move",
                 operator="web", reason="戶政事務所服務爭議")
    log_feedback(feedback_repo, batch_id="", entity_type="clustering", entity_id="ftopic_abc",
                 ai_original_value="巴威颱風動態", human_final_value="中颱巴威來襲防颱整備",
                 action="human_merge_topic", operator="user")

    text = build_clustering_human_examples(feedback_repo, news_repo)
    assert "戶政事務所服務爭議" in text
    assert "人工把議題「巴威颱風動態」整個併入「中颱巴威來襲防颱整備」" in text
    # 舊實作會拿議題 ID 查新聞標題、產生「新聞《ftopic_abc》」雜訊行
    assert "ftopic_abc" not in text


def test_naming_examples_from_topic_naming_feedback(feedback_repo):
    log_feedback(feedback_repo, batch_id="", entity_type="topic_naming", entity_id="t1",
                 ai_original_value="公投議題", human_final_value="藍白推動公投與政治動員爭議",
                 action="human_rename", operator="user")
    # 名稱沒變的紀錄不當範例
    log_feedback(feedback_repo, batch_id="", entity_type="topic_naming", entity_id="t2",
                 ai_original_value="相同名稱", human_final_value="相同名稱",
                 action="human_rename", operator="user")

    text = build_naming_examples(feedback_repo)
    assert "「公投議題」→ 人工改名為「藍白推動公投與政治動員爭議」" in text
    assert "相同名稱" not in text


def test_combined_examples_have_naming_section(feedback_repo, news_repo):
    log_feedback(feedback_repo, batch_id="", entity_type="topic_naming", entity_id="t1",
                 ai_original_value="舊名", human_final_value="具體的新議題名稱",
                 action="human_rename", operator="user")
    combined = build_combined_clustering_examples(feedback_repo, news_repo)
    assert "議題命名風格範例" in combined
    assert "具體的新議題名稱" in combined


def test_combined_examples_empty_when_no_feedback(feedback_repo, news_repo):
    assert build_combined_clustering_examples(feedback_repo, news_repo) == ""


# ---------------------------------------------------------------------------
# 5. 設定預設值
# ---------------------------------------------------------------------------
def test_api_settings_granularity_default():
    from app.models.settings import ApiSettings
    assert ApiSettings().clustering_granularity == "standard"
