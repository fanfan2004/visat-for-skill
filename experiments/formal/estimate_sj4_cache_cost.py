"""Estimate sj4 per-condition dollar cost under market prompt-caching prices.

实测 sj4 全部 cacheReadTokens=0（glm53-flash 端点未命中缓存），这里模拟「若有前缀缓存」的价格。

逐请求重建（本地无逐请求 rollout）：
  同一 session 内上下文近似线性增长。第 1 回合首请求输入 = FIRST_REQUEST（系统提示+工具+任务），
  每回合已知请求数 N 与输入总和 I，线性段 start→end 满足 I = N*(start+end)/2，
  下一回合从上一回合 end 接续（resume 同 session，前缀保留）。
缓存模型：
  请求 i 的可缓存前缀 = 上一请求输入 x_{i-1}，命中比例 HIT（模拟 TTL/分块对齐损失）；
  未命中部分按原价，命中部分按缓存读价；Anthropic 式计费另对新写入部分收写入溢价。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data/derived"

# 美元 / 百万 token。来源为 2026-09/10 第三方汇总，非官方核实，按情景使用。
PRICE_TIERS = {
    "glm-5.3-flash": {"input": 0.15, "cache_read": 0.03, "cache_write": 0.15, "output": 0.50},
    "glm-5.3": {"input": 1.40, "cache_read": 0.26, "cache_write": 1.40, "output": 4.40},
    "claude-sonnet-class": {"input": 3.00, "cache_read": 0.30, "cache_write": 3.75, "output": 15.00},
}
FIRST_REQUEST_OPTIONS = (8_000, 12_000, 16_000)
HIT_OPTIONS = (1.0, 0.9, 0.7)
DEFAULT_FIRST, DEFAULT_HIT = 12_000, 0.9


def per_request_inputs(turns: list[dict], first: int) -> list[float]:
    xs: list[float] = []
    start = float(first)
    for turn in turns:
        n, total = int(turn["requests"]), float(turn["input_tokens"])
        if n == 0:
            continue
        if n == 1:
            xs.append(total)
            start = total
            continue
        end = max(start, 2 * total / n - start)
        segment = [start + (end - start) * i / (n - 1) for i in range(n)]
        # 线性段与实测总和对齐（end 被下限截断时按比例缩放）
        scale = total / sum(segment)
        segment = [x * scale for x in segment]
        xs.extend(segment)
        start = segment[-1]
    return xs


def trial_cost(xs: list[float], output_tokens: int, price: dict, hit: float, cached: bool) -> float:
    cost = output_tokens * price["output"]
    previous = 0.0
    for x in xs:
        if not cached:
            cost += x * price["input"]
            continue
        read = min(previous, x) * hit
        fresh = x - read
        cost += read * price["cache_read"] + fresh * price["cache_write"]
        previous = x
    return cost / 1e6


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="x3",
                        help="reads sj4_<tag>_token_turns.csv from recompute_sj4_tokens.py")
    args = parser.parse_args(argv)
    turns_csv = DERIVED / f"sj4_{args.tag}_token_turns.csv"
    out = DERIVED / f"sj4_{args.tag}_cache_cost_estimate.json"
    trials: dict[tuple, list[dict]] = defaultdict(list)
    with turns_csv.open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            trials[(row["task_id"], row["condition"], int(row["replicate"]))].append(row)

    def evaluate(first: int, hit: float) -> dict:
        table: dict = {}
        for (task, condition, _), turns in sorted(trials.items()):
            turns.sort(key=lambda r: int(r["turn"]))
            xs = per_request_inputs(turns, first)
            output_tokens = sum(int(r["output_tokens"]) for r in turns)
            passed = any(r["verifier_pass"] == "True" for r in turns)
            cell = table.setdefault(task, {}).setdefault(condition, {"passes": 0, "trials": 0})
            cell["passes"] += passed
            cell["trials"] += 1
            for tier, price in PRICE_TIERS.items():
                for mode, cached in (("no_cache", False), ("cached", True)):
                    key = f"{tier}:{mode}"
                    cell[key] = cell.get(key, 0.0) + trial_cost(xs, output_tokens, price, hit, cached)
        return table

    base = evaluate(DEFAULT_FIRST, DEFAULT_HIT)
    sensitivity = {
        f"first={first},hit={hit}": {
            task: {cond: round(cell["glm-5.3:cached"], 4) for cond, cell in conds.items()}
            for task, conds in evaluate(first, hit).items()
        }
        for first in FIRST_REQUEST_OPTIONS for hit in HIT_OPTIONS
    }
    out.write_text(json.dumps({"base": base, "sensitivity_glm53_cached": sensitivity},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    for task, conds in base.items():
        for cond, cell in conds.items():
            line = [task, cond, f"{cell['passes']}/{cell['trials']}"]
            for tier in PRICE_TIERS:
                nc, c = cell[f"{tier}:no_cache"], cell[f"{tier}:cached"]
                per_pass = f"{c / cell['passes']:.3f}" if cell["passes"] else "-"
                line.append(f"{tier}: {nc:.3f}->{c:.3f} (-{(1 - c / nc) * 100:.0f}%) /pass {per_pass}")
            print(" | ".join(line))
    print("\nsensitivity (glm-5.3 cached, 3-trial total $):")
    for key, value in sensitivity.items():
        print(" ", key, json.dumps(value))


if __name__ == "__main__":
    main()
