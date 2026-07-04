"""測試：留用初判基準測試工具（benchmark/）

驗證資料集分層抽樣的分類邏輯與評分指標計算，不需要呼叫任何 API。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark"))

from export_benchmark_dataset import build_dataset  # noqa: E402
from run_retention_benchmark import compute_metrics, _normalize_judgement  # noqa: E402


def _seed_news(conn, row_id, retained, status, judged_by="ai"):
    conn.execute(
        "INSERT INTO news (row_id, title, summary, source, retained, retention_status, "
        "retention_judged_at, retention_judged_by, priority_stars) VALUES (?,?,?,?,?,?,?,?,?)",
        (row_id, f"標題{row_id}", f"摘要{row_id}", "中央社", int(retained), status,
         time.time(), judged_by, 3))


def _seed_feedback(conn, entity_id, ai_value, human_value):
    conn.execute(
        "INSERT INTO feedback_log (feedback_id, entity_type, entity_id, ai_original_value, "
        "human_final_value, action, created_at) VALUES (?,?,?,?,?,?,?)",
        (f"fb_{entity_id}", "retention", entity_id, ai_value, human_value,
         "human_override", time.time()))


def test_build_dataset_stratifies_corrections_and_controls(tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    with conn:
        # 修正樣本：AI 過嚴（AI 判不留用、人工改留用）與 AI 過寬
        _seed_news(conn, "c1", retained=True, status="留用", judged_by="human")
        _seed_feedback(conn, "c1", "AI建議不留用", "留用")
        _seed_news(conn, "c2", retained=False, status="人工不留用", judged_by="human")
        _seed_feedback(conn, "c2", "留用", "人工不留用")
        # 對照樣本：AI 判過、無人工修正
        for i in range(5):
            _seed_news(conn, f"k{i}", retained=True, status="留用")
        for i in range(5):
            _seed_news(conn, f"n{i}", retained=False, status="AI建議不留用")
        # 待確認（未判）不應入選
        _seed_news(conn, "pending", retained=True, status="待確認")
        conn.execute("UPDATE news SET retention_judged_at=NULL WHERE row_id='pending'")
    conn.close()

    ds = build_dataset(str(tmp_db_path), control_per_class=3, seed=1)
    counts = ds["meta"]["counts"]
    assert counts["correction_total"] == 2
    assert counts["correction_ai_too_strict"] == 1
    assert counts["correction_ai_too_lenient"] == 1
    assert counts["control_retained"] == 3       # 分層各抽 3
    assert counts["control_not_retained"] == 3
    ids = {i["row_id"] for i in ds["items"]}
    assert "pending" not in ids
    # 修正樣本不得同時出現在對照樣本
    assert sum(1 for i in ds["items"] if i["row_id"] in ("c1", "c2")) == 2
    corr = next(i for i in ds["items"] if i["row_id"] == "c1")
    assert corr["group"] == "correction" and corr["direction"] == "ai_too_strict"
    assert corr["ground_truth"]["retained"] is True


def _item(rid, retained, group):
    return {"row_id": rid, "group": group,
            "ground_truth": {"retained": retained, "retention_status": ""}}


def _judgement(stars=1, respond=False, core=False):
    return _normalize_judgement({"priority_stars": stars, "should_respond": respond,
                                  "is_moi_core_business": core})


def test_compute_metrics_rates_and_stability():
    items = [
        _item("a", True, "correction"),   # 該留
        _item("b", False, "control"),     # 不該留
        _item("c", True, "control"),      # 該留
    ]
    run1 = {"judgements": {
        "a": _judgement(stars=4),              # 答對（救回難題）
        "b": _judgement(stars=1),              # 答對
        "c": _judgement(stars=1),              # 過嚴
    }, "missing": [], "tokens_in": 100, "tokens_out": 50, "elapsed_sec": 1.0}
    run2 = {"judgements": {
        "a": _judgement(stars=1),              # 翻盤：過嚴
        "b": _judgement(stars=5),              # 翻盤：過寬
        # c 缺漏
    }, "missing": ["c"], "tokens_in": 100, "tokens_out": 50, "elapsed_sec": 1.0}

    m = compute_metrics(items, [run1, run2], threshold=3)
    assert abs(m["accuracy"] - (2 / 3 + 0 / 2) / 2) < 1e-9   # run1 對2/3、run2 全錯
    assert m["too_strict_rate"] > 0 and m["too_lenient_rate"] > 0
    assert m["correction_recovery"] == 0.5     # a：run1 對、run2 錯
    assert abs(m["missing_rate"] - (0 / 3 + 1 / 3) / 2) < 1e-9
    assert m["flip_rate"] == 1.0               # a、b 兩則皆翻盤（c 只有一輪不計）
    assert m["tokens_in"] == 200 and m["tokens_out"] == 100


def test_should_respond_and_core_business_override_threshold():
    """留用公式與正式流程一致：星等不足但 should_respond / 核心業務旗標成立仍留用"""
    items = [_item("a", True, "control")]
    run = {"judgements": {"a": _judgement(stars=1, core=True)}, "missing": [],
           "tokens_in": 0, "tokens_out": 0, "elapsed_sec": 0.0}
    m = compute_metrics(items, [run], threshold=3)
    assert m["accuracy"] == 1.0
