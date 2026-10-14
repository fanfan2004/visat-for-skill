"""Recompute sj4 token metrics from raw trial evidence (raw result.json untouched).

背景：sj4 首批 runner 把「发起技能读取的那次请求的整段上下文输入」记成
skill_read_tokens（字段 request_input_tokens），同一请求读多个文件还重复累加。
这测的是读技能时上下文已有多长，不是技能本身的成本；vista 在后续回合才读技能，
因此被系统性高估。本脚本拆成两项：
  - skill_file_tokens：被读文件本身的估计 token（与 runner 新口径 estimate_tokens 一致）
  - 回合级上下文成本：每回合请求数 / 输入 token / 平均每请求输入（来自 turn-N.jsonl 回执）

输出（--tag 默认 x3）：
  data/derived/sj4_<tag>_token_trials.csv      每试验一行
  data/derived/sj4_<tag>_token_turns.csv       每回合一行
  data/derived/sj4_<tag>_token_summary.json    每任务×条件汇总

用法：
  python experiments/formal/recompute_sj4_tokens.py            # 默认 x3 批次
  python experiments/formal/recompute_sj4_tokens.py --tag x5       --group task-011=data/raw/formal-task-011-sj4-x5-glm53-<stamp>       --group task-012=data/raw/formal-task-012-sj4-x5-glm53-<stamp>
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROUPS = {
    "task-011": ROOT / "data/raw/formal-task-011-sj4-x3-glm53-20261013-21",
    "task-012": ROOT / "data/raw/formal-task-012-sj4-x3-glm53-20261013-21",
}
ARTIFACTS = ROOT / "experiments/artifacts/sj4"
DERIVED = ROOT / "data/derived"
CONDITIONS = ("noskill", "flat", "pd", "vista")


def estimate_tokens(text: str) -> int:
    # 与 run_sj4_zcode_group.estimate_tokens 相同：英文/代码约 4 字符/token，CJK 1 字符/token
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + (len(text) - cjk + 3) // 4


def artifact_for(task_id: str, workspace_file: str) -> Path:
    # workspace 里的 modules/<id>/SKILL.md 来自 flat/，skill/... 来自 pd/
    if workspace_file.startswith("modules/"):
        return ARTIFACTS / task_id / "flat" / workspace_file.removeprefix("modules/")
    return ARTIFACTS / task_id / "pd" / workspace_file.removeprefix("skill/")


def turn_receipt(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if not text:
        return {}
    return json.loads(text[text.find("{"):]).get("usage") or {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", default="x3", help="output file tag, e.g. x3 / x5")
    parser.add_argument("--group", action="append", default=[], metavar="TASK=DIR",
                        help="task group directory (repeatable); defaults to the x3 batch")
    args = parser.parse_args(argv)
    groups = dict(DEFAULT_GROUPS)
    if args.group:
        groups = {}
        for item in args.group:
            task_id, directory = item.split("=", 1)
            path = Path(directory)
            groups[task_id] = path if path.is_absolute() else ROOT / path

    trial_rows: list[dict] = []
    turn_rows: list[dict] = []
    for task_id, group in groups.items():
        for condition in CONDITIONS:
            for result_path in sorted((group / condition).glob("replicate-*/result.json")):
                result = json.loads(result_path.read_text(encoding="utf-8"))
                reads = result.get("skill_reads") or []
                file_tokens = [
                    estimate_tokens(artifact_for(task_id, r["file"]).read_text(encoding="utf-8"))
                    for r in reads
                ]
                unique_files = {r["file"]: t for r, t in zip(reads, file_tokens)}
                old_metric = sum(
                    r.get("request_input_tokens", r.get("context_input_tokens", 0)) for r in reads
                )
                turn_input_sum = turn_request_sum = 0
                for turn in result["turns"]:
                    receipt = turn_receipt(result_path.parent / f"turn-{turn['turn']}.jsonl")
                    requests = receipt.get("modelRequestCount") or 0
                    input_tokens = receipt.get("inputTokens") or 0
                    turn_input_sum += input_tokens
                    turn_request_sum += requests
                    turn_rows.append({
                        "task_id": task_id, "condition": condition,
                        "replicate": result["replicate_id"], "turn": turn["turn"],
                        "return_code": turn["return_code"],
                        "requests": requests, "input_tokens": input_tokens,
                        "output_tokens": receipt.get("outputTokens") or 0,
                        "cache_read_tokens": receipt.get("cacheReadTokens") or 0,
                        "input_per_request": round(input_tokens / requests) if requests else 0,
                        "skill_files_read": ";".join(r["file"] for r in turn.get("skill_reads") or []),
                        "verifier_pass": turn["verifier_pass"],
                    })
                usage = result["usage"]
                trial_rows.append({
                    "task_id": task_id, "condition": condition,
                    "replicate": result["replicate_id"],
                    "turns": len(result["turns"]), "stop_reason": result.get("stop_reason"),
                    "final_pass": result.get("final_verifier_pass"),
                    "requests": usage["requests"], "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "input_per_request": round(usage["input_tokens"] / usage["requests"]) if usage["requests"] else 0,
                    "skill_read_count": len(reads),
                    "skill_file_tokens": sum(file_tokens),
                    "skill_unique_file_tokens": sum(unique_files.values()),
                    "old_skill_read_tokens": old_metric,
                    # 回合回执与 session 总量对账：不等说明有回合（报错/超时）的用量没被归属
                    "turn_receipts_reconcile": (
                        turn_request_sum == usage["requests"] and turn_input_sum == usage["input_tokens"]
                    ),
                })

    DERIVED.mkdir(parents=True, exist_ok=True)
    for name, rows in ((f"sj4_{args.tag}_token_trials.csv", trial_rows), (f"sj4_{args.tag}_token_turns.csv", turn_rows)):
        with (DERIVED / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    summary: dict = {}
    for task_id in groups:
        for condition in CONDITIONS:
            rows = [r for r in trial_rows if r["task_id"] == task_id and r["condition"] == condition]
            inputs = [r["input_tokens"] for r in rows]
            summary.setdefault(task_id, {})[condition] = {
                "trials": len(rows),
                "final_passes": sum(bool(r["final_pass"]) for r in rows),
                "skill_file_tokens": sum(r["skill_file_tokens"] for r in rows),
                "old_skill_read_tokens": sum(r["old_skill_read_tokens"] for r in rows),
                "requests": sum(r["requests"] for r in rows),
                "input_tokens": sum(inputs),
                "input_tokens_median": statistics.median(inputs),
                "input_tokens_max_over_min": round(max(inputs) / min(inputs), 2),
                "input_per_request": round(sum(inputs) / max(1, sum(r["requests"] for r in rows))),
                "all_turn_receipts_reconcile": all(r["turn_receipts_reconcile"] for r in rows),
            }
    (DERIVED / f"sj4_{args.tag}_token_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for task_id, conditions in summary.items():
        for condition, values in conditions.items():
            print(task_id, condition, values)


if __name__ == "__main__":
    main()
