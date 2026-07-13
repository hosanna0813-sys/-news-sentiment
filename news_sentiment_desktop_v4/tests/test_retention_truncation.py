"""測試：留用判斷「整批默默不留用」防護（V4.5.2）

使用者回報 AI 留用判斷全部判為不留用。根因鏈：推理型模型輸出容易達到
max_tokens 上限被截斷 → 部分 row_id 不在模型輸出中 → 舊行為默默套用保守
後備值（低優先級 → 不留用），且完全沒有 log。防護（本檔涵蓋 2、3）：
    1. OpenAIGateway 偵測 finish_reason=length，加大額度重試（見 test_openai_gateway）
    2. judge_batch 漏判超過半批 → 拋錯讓批次標記失敗可重試，不再默默全標不留用
    3. 少量漏判仍用後備值，但記警告 log
"""
from __future__ import annotations

import pytest

from app.models.news import NewsItem
from app.services.ai.model_gateway import GatewayError, GatewayErrorType
from app.services.retention.retention_service import judge_batch, prefilter_batch


class _FakeGateway:
    def __init__(self, data):
        self._data = data

    def call_with_tool(self, task, system_prompt, user_content, tool_name, tool_schema):
        class _R:
            data = self._data
        return _R()


def _items(n):
    return [NewsItem(row_id=f"r{i}", title=f"新聞{i}", source="來源",
                      published_at="2026-07-13") for i in range(n)]


def _judgement(rid, stars=4):
    return {"row_id": rid, "business_relevance": 30, "response_requirement": 10,
            "political_sensitivity": 5, "media_attention": 5, "public_impact": 5,
            "executive_bonus": 0, "final_score": 55, "priority_stars": stars,
            "should_respond": False, "is_moi_core_business": False}


def test_judge_batch_raises_when_more_than_half_missing():
    """模型只回傳少數幾則（輸出被截斷的典型特徵）→ 整批拋錯標失敗，
    不可默默把其餘新聞全標成不留用"""
    gw = _FakeGateway({"judgements": [_judgement("r0")]})
    with pytest.raises(GatewayError) as ei:
        judge_batch(gw, _items(10), "sys", "{news_batch_json}{human_examples_section}",
                     "tool", {"type": "object"})
    assert ei.value.error_type == GatewayErrorType.PARSE_ERROR
    assert "1/10" in ei.value.message


def test_judge_batch_minor_missing_uses_fallback():
    """只漏一兩則時維持舊行為：套用保守後備值（低優先級），不整批失敗"""
    gw = _FakeGateway({"judgements": [_judgement(f"r{i}") for i in range(4)]})
    out = judge_batch(gw, _items(5), "sys", "{news_batch_json}{human_examples_section}",
                       "tool", {"type": "object"})
    assert len(out) == 5
    assert out["r0"]["priority_stars"] == 4        # 有判斷的正常吃進來
    assert out["r4"]["priority_stars"] == 1        # 漏判的套保守後備值
    assert out["r4"]["score_final"] == 0.0


def test_prefilter_missing_rows_default_to_relevant():
    """粗篩漏判 fallback 為放行（寧可交給細評，也不在粗篩武斷排除）"""
    gw = _FakeGateway({"judgements": [{"row_id": "r0", "is_relevant": False}]})
    out = prefilter_batch(gw, _items(3), "sys", "{news_batch_json}", "tool", {"type": "object"})
    assert out["r0"] is False
    assert out["r1"] is True and out["r2"] is True
