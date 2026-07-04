"""
留用初判基準測試 — 第 1 階段：從正式資料庫匯出「黃金標準」測試資料集

黃金標準 = 人工覆核過的留用決定。分層抽樣策略：
    - 修正樣本（correction）：AI 判過、後來被人工修改留用狀態的新聞
      （feedback_log 中 entity_type='retention' 且 action 以 human_ 開頭）。
      全數保留不抽樣——這是鑑別兩家模型優劣的「難題」。
    - 對照樣本（control）：AI 判過、未被人工修改的新聞（視為人工默認接受）。
      依留用/不留用分層，各隨機抽 N 則（預設 60），避免全是簡單題。

用法（在您的電腦上，於專案資料夾執行）：
    .venv\\Scripts\\python benchmark\\export_benchmark_dataset.py
    （預設讀 %APPDATA%\\NewsSentimentDesktopV4\\news_sentiment.db，
      輸出 benchmark\\benchmark_dataset.json）

選項：
    --db PATH            指定資料庫路徑
    --out PATH           指定輸出檔路徑
    --control-per-class N  對照樣本每類（留用/不留用）抽幾則，預設 60
    --seed N             抽樣亂數種子，預設 42（固定種子讓結果可重現）

僅使用 Python 標準函式庫，不需安裝任何套件。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path


def default_db_path() -> str:
    appdata = os.environ.get("APPDATA", "")
    return str(Path(appdata) / "NewsSentimentDesktopV4" / "news_sentiment.db")


def _parse_retained_from_text(text: str):
    """從 feedback 的狀態文字推斷留用與否：'人工不留用'/'AI建議不留用' → False，'留用' → True"""
    if not text:
        return None
    if "不留" in text:
        return False
    if "留用" in text:
        return True
    return None


def _news_row_to_item(row: sqlite3.Row) -> dict:
    """轉成測試項目：模型輸入欄位 = 正式流程 judge_batch 送給模型的相同欄位"""
    return {
        "row_id": row["row_id"],
        "title": row["title"] or "",
        "summary": row["summary"] or "",
        "source": row["source"] or "",
        "published_at": row["published_at"] or "",
        "channel": row["channel"] or "",
        "is_duplicate": bool(row["duplicate_group_id"]),
        "ground_truth": {
            "retained": bool(row["retained"]),
            "retention_status": row["retention_status"] or "",
        },
        "ai_original": {
            "priority_stars": row["priority_stars"] or 0,
            "should_respond": bool(row["should_respond"]),
            "is_moi_core_business": bool(row["is_moi_core_business"]),
            "score_final": row["score_final"] or 0,
        },
    }


def collect_correction_items(conn: sqlite3.Connection) -> list:
    """修正樣本：有 human_ 開頭留用回饋紀錄的新聞（全數保留），並標記修正方向"""
    # 每則新聞取最新一筆人工留用修正，用於推斷方向
    fb_rows = conn.execute(
        "SELECT entity_id, ai_original_value, human_final_value, MAX(created_at) "
        "FROM feedback_log WHERE entity_type='retention' AND action LIKE 'human_%' "
        "GROUP BY entity_id").fetchall()
    items = []
    for fb in fb_rows:
        row = conn.execute("SELECT * FROM news WHERE row_id=?", (fb["entity_id"],)).fetchone()
        if row is None:
            continue  # 新聞已被清除
        item = _news_row_to_item(row)
        item["group"] = "correction"
        ai_side = _parse_retained_from_text(fb["ai_original_value"])
        human_side = _parse_retained_from_text(fb["human_final_value"])
        if human_side is None:
            human_side = bool(row["retained"])
        if ai_side is not None and ai_side != human_side:
            item["direction"] = "ai_too_strict" if human_side else "ai_too_lenient"
        else:
            item["direction"] = "unknown"
        items.append(item)
    return items


def collect_control_items(conn: sqlite3.Connection, exclude_ids: set,
                           per_class: int, rng: random.Random) -> list:
    """對照樣本：AI 判過、無人工修正的新聞，依留用/不留用分層各抽 per_class 則"""
    out = []
    for retained_value in (1, 0):
        rows = conn.execute(
            "SELECT * FROM news WHERE retention_judged_at IS NOT NULL "
            "AND retention_status != '待確認' AND retained=?", (retained_value,)).fetchall()
        rows = [r for r in rows if r["row_id"] not in exclude_ids]
        if len(rows) > per_class:
            rows = rng.sample(rows, per_class)
        for row in rows:
            item = _news_row_to_item(row)
            item["group"] = "control"
            item["direction"] = ""
            out.append(item)
    return out


def build_dataset(db_path: str, control_per_class: int, seed: int) -> dict:
    if not Path(db_path).exists():
        raise SystemExit(f"找不到資料庫：{db_path}\n請用 --db 指定 news_sentiment.db 的路徑")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rng = random.Random(seed)
        corrections = collect_correction_items(conn)
        controls = collect_control_items(conn, {i["row_id"] for i in corrections},
                                          control_per_class, rng)
        items = corrections + controls
        counts = {
            "correction_total": len(corrections),
            "correction_ai_too_strict": sum(1 for i in corrections
                                             if i["direction"] == "ai_too_strict"),
            "correction_ai_too_lenient": sum(1 for i in corrections
                                              if i["direction"] == "ai_too_lenient"),
            "control_retained": sum(1 for i in controls if i["ground_truth"]["retained"]),
            "control_not_retained": sum(1 for i in controls
                                         if not i["ground_truth"]["retained"]),
            "total": len(items),
        }
        return {
            "meta": {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "db_path": db_path,
                "seed": seed,
                "control_per_class": control_per_class,
                "counts": counts,
                "note": ("ground_truth=人工覆核後的最終留用狀態；"
                          "control 樣本為 AI 判斷後未被人工修改者（視為默認接受）"),
            },
            "items": items,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="匯出留用初判基準測試資料集（分層抽樣）")
    parser.add_argument("--db", default=default_db_path())
    parser.add_argument("--out", default=str(Path(__file__).parent / "benchmark_dataset.json"))
    parser.add_argument("--control-per-class", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    dataset = build_dataset(args.db, args.control_per_class, args.seed)
    Path(args.out).write_text(json.dumps(dataset, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    c = dataset["meta"]["counts"]
    print("=== 資料集匯出完成 ===")
    print(f"修正樣本（難題）：{c['correction_total']} 則"
          f"（AI過嚴 {c['correction_ai_too_strict']}、AI過寬 {c['correction_ai_too_lenient']}、"
          f"其他 {c['correction_total'] - c['correction_ai_too_strict'] - c['correction_ai_too_lenient']}）")
    print(f"對照樣本：留用 {c['control_retained']} 則、不留用 {c['control_not_retained']} 則")
    print(f"總計：{c['total']} 則 → {args.out}")
    if c["correction_total"] < 30:
        print("⚠ 修正樣本偏少（<30），比較結果的可信度會下降，建議累積更多人工覆核後再測")


if __name__ == "__main__":
    main()
