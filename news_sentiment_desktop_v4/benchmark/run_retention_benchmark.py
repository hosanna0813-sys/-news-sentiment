"""
留用初判基準測試 — 第 2/3 階段：Claude API vs OpenAI API 盲測與評分

對 export_benchmark_dataset.py 匯出的資料集，用「與正式流程完全相同的
MOI 政策關注度 prompt 與 schema」讓多個模型盲測，與人工覆核結果比對評分。

用法（在您的電腦上，於專案資料夾執行；OpenAI 需先 pip install openai）：
    set OPENAI_API_KEY=sk-...        （Anthropic 金鑰自動從系統的認證管理員讀取）
    .venv\\Scripts\\python benchmark\\run_retention_benchmark.py ^
        --model anthropic:claude-sonnet-5 ^
        --model openai:<OpenAI模型ID，請查 platform.openai.com 當日最新型號>

選項：
    --dataset PATH     資料集路徑（預設 benchmark/benchmark_dataset.json）
    --model P:M        受測模型，可重複（anthropic:xxx 或 openai:xxx）
    --runs N           每個模型重跑幾輪（預設 3，測穩定性）
    --batch-size N     每批幾則（預設 10，與正式流程相同）
    --threshold N      留用星等門檻（預設 3，與系統設定相同）
    --price M=IN,OUT   模型每百萬 tokens 價格（美元），用於成本估算，可重複
    --limit N          只取資料集前 N 則（小額試跑用）
    --out PREFIX       輸出檔前綴（預設 benchmark/benchmark_result）

輸出：<prefix>_report.md（評分報告）與 <prefix>_raw.json（原始判斷紀錄）
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 專案根目錄

from app.prompts.retention_prompt import (  # noqa: E402
    SYSTEM_PROMPT, USER_TEMPLATE, TOOL_NAME, TOOL_SCHEMA,
)
from app.services.retention.retention_service import decide_retain  # noqa: E402
from app.utils.text_utils import coerce_model_list, safe_format  # noqa: E402

# Claude 定價（美元/百萬 tokens，2026-07 牌價；OpenAI 請用 --price 提供）
DEFAULT_PRICES = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
RETRYABLE_HINTS = ("rate limit", "429", "overloaded", "529", "500", "502", "503", "timeout")


def _normalize_judgement(j: dict) -> dict:
    """與 retention_service 相同的防禦性正規化"""
    return {
        "priority_stars": max(1, min(5, int(j.get("priority_stars", 1) or 1))),
        "should_respond": bool(j.get("should_respond", False)),
        "is_moi_core_business": bool(j.get("is_moi_core_business", False)),
        "final_score": float(j.get("final_score", 0.0) or 0.0),
    }


class AnthropicRunner:
    def __init__(self, model_id: str):
        import anthropic
        self._anthropic = anthropic
        self.model_id = model_id
        api_key = None
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            try:
                from app.utils.secure_key_store import load_api_key
                api_key = load_api_key()
            except Exception:
                api_key = None
        if not api_key:
            raise SystemExit("找不到 Anthropic API Key（環境變數 ANTHROPIC_API_KEY 或系統認證管理員）")
        self.client = anthropic.Anthropic(api_key=api_key, timeout=180)

    def judge(self, system_prompt: str, user_content: str, max_tokens: int) -> tuple:
        resp = self.client.messages.create(
            model=self.model_id, max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            tools=[{"name": TOOL_NAME, "description": "回傳每則新聞的留用評分",
                     "input_schema": TOOL_SCHEMA}],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        data = block.input if block is not None else {}
        usage = (resp.usage.input_tokens, resp.usage.output_tokens)
        return data, usage


class OpenAIRunner:
    def __init__(self, model_id: str):
        try:
            import openai
        except ImportError:
            raise SystemExit("尚未安裝 openai 套件，請執行：.venv\\Scripts\\pip install openai")
        import os
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("請先設定環境變數 OPENAI_API_KEY（set OPENAI_API_KEY=sk-...）")
        self.model_id = model_id
        self.client = openai.OpenAI(api_key=api_key, timeout=180)
        self._token_param = "max_completion_tokens"  # 新款模型用這個，舊款自動退回 max_tokens

    def judge(self, system_prompt: str, user_content: str, max_tokens: int) -> tuple:
        kwargs = {self._token_param: max_tokens}
        try:
            resp = self._create(system_prompt, user_content, kwargs)
        except Exception as e:
            msg = str(e)
            if "max_completion_tokens" in msg or "max_tokens" in msg:
                # 參數名稱不合此模型：換另一個名稱重送一次
                self._token_param = ("max_tokens" if self._token_param == "max_completion_tokens"
                                      else "max_completion_tokens")
                resp = self._create(system_prompt, user_content,
                                     {self._token_param: max_tokens})
            else:
                raise
        msg_obj = resp.choices[0].message
        data = {}
        if getattr(msg_obj, "tool_calls", None):
            data = json.loads(msg_obj.tool_calls[0].function.arguments)
        elif msg_obj.content:
            from app.utils.text_utils import safe_json_loads
            data = safe_json_loads(msg_obj.content) or {}
        usage = (resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return data, usage

    def _create(self, system_prompt: str, user_content: str, extra: dict):
        return self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_content}],
            tools=[{"type": "function", "function": {
                "name": TOOL_NAME, "description": "回傳每則新聞的留用評分",
                "parameters": TOOL_SCHEMA}}],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            **extra,
        )


def make_runner(spec: str):
    provider, _, model_id = spec.partition(":")
    if not model_id:
        raise SystemExit(f"--model 格式錯誤：{spec}（應為 anthropic:模型ID 或 openai:模型ID）")
    if provider == "anthropic":
        return AnthropicRunner(model_id)
    if provider == "openai":
        return OpenAIRunner(model_id)
    raise SystemExit(f"不支援的供應商：{provider}")


def call_with_retry(fn, max_retries: int = 4):
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:  # 分類重試：限流/過載/逾時才重試
            last = e
            msg = str(e).lower()
            if not any(h in msg for h in RETRYABLE_HINTS) or attempt == max_retries:
                raise
            wait = min(2 ** attempt, 30)
            print(f"    重試 {attempt}/{max_retries}（{wait}s 後）: {str(e)[:80]}")
            time.sleep(wait)
    raise last


def run_model_once(runner, items: list, batch_size: int, run_seed: int) -> dict:
    """跑一輪：回傳 {row_id: judgement}、缺漏清單、tokens、耗時"""
    order = list(items)
    random.Random(run_seed).shuffle(order)  # 每輪順序不同，避免順序效應
    judgements, missing = {}, []
    tokens_in = tokens_out = 0
    started = time.time()
    batches = [order[i:i + batch_size] for i in range(0, len(order), batch_size)]
    for bi, batch in enumerate(batches, start=1):
        payload = [{k: it[k] for k in ("row_id", "title", "summary", "source",
                                         "published_at", "channel", "is_duplicate")}
                   for it in batch]
        user_content = safe_format(
            USER_TEMPLATE, news_batch_json=json.dumps(payload, ensure_ascii=False),
            human_examples_section="")
        # 動態 max_tokens：避免「模型未回傳判斷」的截斷問題（依批次大小計算）
        max_tokens = 600 + 260 * len(batch)
        data, usage = call_with_retry(lambda: runner.judge(SYSTEM_PROMPT, user_content, max_tokens))
        tokens_in += usage[0]
        tokens_out += usage[1]
        for j in coerce_model_list(data, "judgements"):
            if isinstance(j, dict) and j.get("row_id"):
                judgements[j["row_id"]] = _normalize_judgement(j)
        print(f"    批次 {bi}/{len(batches)} 完成")
    for it in items:
        if it["row_id"] not in judgements:
            missing.append(it["row_id"])
    return {"judgements": judgements, "missing": missing,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "elapsed_sec": round(time.time() - started, 1)}


def compute_metrics(items: list, runs: list, threshold: int) -> dict:
    """與人工覆核（ground truth）比對，彙整多輪結果"""
    def decision(run, rid):
        j = run["judgements"].get(rid)
        return None if j is None else decide_retain(
            {"priority_stars": j["priority_stars"], "should_respond": j["should_respond"],
             "is_moi_core_business": j["is_moi_core_business"]}, threshold)

    per_run = []
    for run in runs:
        m = {"too_strict": 0, "too_lenient": 0, "correct": 0, "judged": 0,
             "correction_recovered": 0, "correction_total": 0,
             "control_kept": 0, "control_total": 0}
        for it in items:
            d = decision(run, it["row_id"])
            if d is None:
                continue
            gt = it["ground_truth"]["retained"]
            m["judged"] += 1
            if d == gt:
                m["correct"] += 1
            elif gt and not d:
                m["too_strict"] += 1
            else:
                m["too_lenient"] += 1
            if it["group"] == "correction":
                m["correction_total"] += 1
                m["correction_recovered"] += int(d == gt)
            else:
                m["control_total"] += 1
                m["control_kept"] += int(d == gt)
        per_run.append(m)

    def avg_rate(key, denom_key):
        vals = [m[key] / m[denom_key] for m in per_run if m[denom_key]]
        return statistics.mean(vals) if vals else 0.0

    # 穩定性：同一則新聞在不同輪的留用決定是否翻盤
    flip = total_multi = 0
    for it in items:
        ds = [d for d in (decision(r, it["row_id"]) for r in runs) if d is not None]
        if len(ds) >= 2:
            total_multi += 1
            flip += int(len(set(ds)) > 1)

    return {
        "accuracy": avg_rate("correct", "judged"),
        "too_strict_rate": avg_rate("too_strict", "judged"),
        "too_lenient_rate": avg_rate("too_lenient", "judged"),
        "correction_recovery": avg_rate("correction_recovered", "correction_total"),
        "control_preservation": avg_rate("control_kept", "control_total"),
        "missing_rate": statistics.mean(len(r["missing"]) / len(items) for r in runs),
        "flip_rate": (flip / total_multi) if total_multi else 0.0,
        "tokens_in": sum(r["tokens_in"] for r in runs),
        "tokens_out": sum(r["tokens_out"] for r in runs),
        "elapsed_sec": sum(r["elapsed_sec"] for r in runs),
        "per_run": per_run,
    }


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int, prices: dict):
    price = prices.get(model_id)
    if not price:
        return None
    return tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1]


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_report(out_prefix: str, dataset_meta: dict, results: dict, prices: dict,
                  runs_count: int, threshold: int) -> str:
    lines = [
        "# 留用初判基準測試報告：Claude API vs OpenAI API",
        "",
        f"- 測試時間：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 資料集：{json.dumps(dataset_meta.get('counts', {}), ensure_ascii=False)}",
        f"- 每模型輪數：{runs_count}　留用星等門檻：{threshold}",
        "",
        "| 指標 | " + " | ".join(results.keys()) + " |",
        "|---|" + "---|" * len(results),
    ]
    rows = [
        ("整體正確率（vs 人工覆核）", lambda m: pct(m["accuracy"])),
        ("過嚴率（該留卻沒留）⬇", lambda m: pct(m["too_strict_rate"])),
        ("過寬率（不該留卻留）⬇", lambda m: pct(m["too_lenient_rate"])),
        ("難題救回率（修正樣本答對）⬆", lambda m: pct(m["correction_recovery"])),
        ("對照保持率（沒把對的改壞）⬆", lambda m: pct(m["control_preservation"])),
        ("缺漏率（未回傳判斷）⬇", lambda m: pct(m["missing_rate"])),
        ("判斷翻盤率（多輪不一致）⬇", lambda m: pct(m["flip_rate"])),
        ("總 tokens（輸入/輸出）", lambda m: f"{m['tokens_in']:,}/{m['tokens_out']:,}"),
        ("總耗時（秒）", lambda m: f"{m['elapsed_sec']:.0f}"),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(m) for m in results.values()) + " |")
    costs = []
    for model_id, m in results.items():
        c = estimate_cost(model_id, m["tokens_in"], m["tokens_out"], prices)
        costs.append(f"${c:.2f}" if c is not None else "（未提供價格）")
    lines.append("| 估算費用（美元） | " + " | ".join(costs) + " |")
    lines += [
        "",
        "## 判讀指引",
        "- **過嚴率權重最高**：漏掉重要輿情的代價 > 多看幾則雜訊。",
        "- 難題救回率高但對照保持率低 = 模型整體偏寬，只是碰巧救回難題，要小心。",
        "- 缺漏率與翻盤率反映工程可靠度，長期使用體感影響大。",
        "- 對照樣本的 ground truth 為「AI 判斷後人工未修改」，可能包含未經人工細看的項目，",
        "  解讀對照保持率時保留一點誤差空間。",
    ]
    report_path = f"{out_prefix}_report.md"
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="留用初判 Claude vs OpenAI 基準測試")
    parser.add_argument("--dataset", default=str(Path(__file__).parent / "benchmark_dataset.json"))
    parser.add_argument("--model", action="append", required=True,
                        help="anthropic:模型ID 或 openai:模型ID，可重複")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--threshold", type=int, default=3)
    parser.add_argument("--price", action="append", default=[],
                        help="模型每百萬tokens價格：模型ID=輸入價,輸出價（美元）")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 則（試跑用）")
    parser.add_argument("--out", default=str(Path(__file__).parent / "benchmark_result"))
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    items = dataset["items"]
    if args.limit:
        items = items[:args.limit]
    print(f"資料集：{len(items)} 則（修正 {sum(1 for i in items if i['group'] == 'correction')}"
          f" / 對照 {sum(1 for i in items if i['group'] == 'control')}）")

    prices = dict(DEFAULT_PRICES)
    for spec in args.price:
        model_id, _, pair = spec.partition("=")
        p_in, _, p_out = pair.partition(",")
        prices[model_id] = (float(p_in), float(p_out))

    results, raw = {}, {}
    for spec in args.model:
        runner = make_runner(spec)
        print(f"\n=== 測試 {spec}（{args.runs} 輪 × {len(items)} 則）===")
        runs = []
        for r in range(1, args.runs + 1):
            print(f"  第 {r}/{args.runs} 輪：")
            run = run_model_once(runner, items, args.batch_size, run_seed=1000 + r)
            print(f"  → 缺漏 {len(run['missing'])} 則，"
                  f"tokens {run['tokens_in']:,}/{run['tokens_out']:,}，{run['elapsed_sec']}s")
            runs.append(run)
        results[runner.model_id] = compute_metrics(items, runs, args.threshold)
        raw[runner.model_id] = runs

    report_path = write_report(args.out, dataset.get("meta", {}), results, prices,
                                args.runs, args.threshold)
    Path(f"{args.out}_raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(f"\n=== 完成 ===\n報告：{report_path}\n原始紀錄：{args.out}_raw.json")
    for model_id, m in results.items():
        print(f"\n{model_id}: 正確率 {pct(m['accuracy'])}　過嚴 {pct(m['too_strict_rate'])}"
              f"　過寬 {pct(m['too_lenient_rate'])}　難題救回 {pct(m['correction_recovery'])}"
              f"　缺漏 {pct(m['missing_rate'])}")


if __name__ == "__main__":
    main()
