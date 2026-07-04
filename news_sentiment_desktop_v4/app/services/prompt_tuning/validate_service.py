"""Prompt 調校建議 — 第二步：驗證指標計算

樣本選取（修正樣本 / 對照樣本）比照人工手動驗證（本次工作階段）的作法：
    - 修正樣本：retention_judged_by='human' 的新聞（人工實際覆核過的結果）
    - 對照樣本：目前卡在留用門檻邊界、AI 自行判斷、視為原本正確排除的新聞

不做除法時的零除防呆；成本估算採本次工作階段實測費率換算。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List
import time

from app.models.news import NewsItem

MAX_CORRECTION_SAMPLE = 60   # 比照本次工作階段手動驗證用的 60 筆已知修正
MAX_CONTROL_SAMPLE = 76      # 比照本次工作階段手動驗證用的 76 筆邊界雜訊

# 本次工作階段實測費率：136 筆（人工修正+對照樣本）跑一趟細評花費 $0.31
COST_PER_ITEM_ONE_PASS_USD = 0.31 / 136


def estimate_validation_cost(correction_count: int, control_count: int) -> float:
    """驗證需對同一批樣本各跑「目前 Prompt」與「建議 Prompt」兩趟，故乘以 2。"""
    total_items = correction_count + control_count
    return round(total_items * COST_PER_ITEM_ONE_PASS_USD * 2, 2)


@dataclass
class ValidationMetrics:
    correction_sample_size: int
    control_sample_size: int
    recovery_count: int                        # 修正樣本中，目前prompt判錯、建議prompt判對的筆數
    recovery_rate: float
    false_positive_count: int                  # 對照樣本中，目前prompt正確排除、建議prompt誤判留用的筆數
    false_positive_rate: float
    current_accuracy_on_corrections: float
    proposed_accuracy_on_corrections: float
    current_accuracy_on_control: float
    proposed_accuracy_on_control: float
    estimated_cost_usd: float
    validated_at: float
    error_note: str = ""


def _safe_div(numer: int, denom: int) -> float:
    return round(numer / denom, 4) if denom else 0.0


def compute_validation_metrics(correction_items: List[NewsItem], control_items: List[NewsItem],
                                current_results: Dict[str, Dict[str, Any]],
                                proposed_results: Dict[str, Dict[str, Any]],
                                retain_fn: Callable[[Dict[str, Any], int], bool],
                                priority_threshold: int,
                                estimated_cost_usd: float = 0.0,
                                error_note: str = "") -> ValidationMetrics:
    """
    修正樣本：human 最終 retained 是「正確答案」。
        - current_correct：current_results 判斷的 retain 是否等於 human 最終 retained
        - recovered：current 判錯 且 proposed 判對
    對照樣本：目前已知「不留用」是正確答案（人工尚未覆核，視為 AI 原判正確）。
        - false positive：current 判 False（正確）但 proposed 判 True（誤判留用）
    """
    correction_total = len(correction_items)
    control_total = len(control_items)

    current_correction_correct = 0
    proposed_correction_correct = 0
    recovered = 0
    for it in correction_items:
        human_retain = bool(it.retained)
        cur = current_results.get(it.row_id)
        prop = proposed_results.get(it.row_id)
        if cur is None or prop is None:
            continue
        cur_retain = retain_fn(cur, priority_threshold)
        prop_retain = retain_fn(prop, priority_threshold)
        if cur_retain == human_retain:
            current_correction_correct += 1
        if prop_retain == human_retain:
            proposed_correction_correct += 1
        if cur_retain != human_retain and prop_retain == human_retain:
            recovered += 1

    current_control_correct = 0
    proposed_control_correct = 0
    false_positives = 0
    for it in control_items:
        cur = current_results.get(it.row_id)
        prop = proposed_results.get(it.row_id)
        if cur is None or prop is None:
            continue
        cur_retain = retain_fn(cur, priority_threshold)
        prop_retain = retain_fn(prop, priority_threshold)
        if not cur_retain:
            current_control_correct += 1
        if not prop_retain:
            proposed_control_correct += 1
        if not cur_retain and prop_retain:
            false_positives += 1

    return ValidationMetrics(
        correction_sample_size=correction_total,
        control_sample_size=control_total,
        recovery_count=recovered,
        recovery_rate=_safe_div(recovered, correction_total),
        false_positive_count=false_positives,
        false_positive_rate=_safe_div(false_positives, control_total),
        current_accuracy_on_corrections=_safe_div(current_correction_correct, correction_total),
        proposed_accuracy_on_corrections=_safe_div(proposed_correction_correct, correction_total),
        current_accuracy_on_control=_safe_div(current_control_correct, control_total),
        proposed_accuracy_on_control=_safe_div(proposed_control_correct, control_total),
        estimated_cost_usd=estimated_cost_usd,
        validated_at=time.time(),
        error_note=error_note,
    )
