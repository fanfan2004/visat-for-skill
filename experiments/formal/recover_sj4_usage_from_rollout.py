"""Recover zero-recorded trial usage from zcode-storage rollout files.

背景：sj4 六条件批次里部分 trial 的模型回合被超时杀掉（rc=124），runner 在回合
收尾前被中断，导致 result.json 的 usage / session_id / 回合明细解析失败（记 0）。
原始 IO 记录完整保存在 trial 缓存目录 zcode-storage/cli/rollout/model-io-*.jsonl，
每条记录带 usage（inputTokens/outputTokens/cacheReadTokens）、durationMs、turnId。

已验证口径（vista-replicate-03/04 的存量记录完全吻合）：
  - trial 级 usage = rollout 全部记录求和（attempt 字段是 CLI 内部请求重试计数，
    重试请求同样是真实消耗，全部计入）
  - agent_net_seconds = sum(durationMs) / 1000
  - 回合明细按 turnId 分组，与 result.json turns 里已有 zcode_turn_id 的回合对齐，
    剩余组按时间顺序填补 zcode_turn_id 为 null 的回合

补记字段：usage、session_id、agent_net_seconds、缺失回合的
zcode_turn_id / requests / turn_usage，并加 usage_recovery 注记。
随后从 trial result.json 重算 group-summary 的条件 usage / agent_net_seconds /
verifier_seconds 汇总与 results[] 对应条目。原始数据有 git 版本（cc8256c）可回滚。

用法：
  python experiments/formal/recover_sj4_usage_from_rollout.py --stamp 20261014-2150
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path.home() / "Library/Caches/VistaExperiments"
STAMP = "20261014-2150"
GROUP_PREFIX = f"formal-task-014-sj4ext-six-x5-glm53-{STAMP}"


def rollout_records(cache_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted((cache_dir / "zcode-storage/cli/rollout").glob("model-io-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def usage_totals(records: list[dict]) -> dict:
    usage = {"requests": len(records), "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0}
    for record in records:
        per = (record.get("response") or {}).get("usage") or {}
        usage["input_tokens"] += per.get("inputTokens") or 0
        usage["output_tokens"] += per.get("outputTokens") or 0
        usage["cache_read_tokens"] += per.get("cacheReadTokens") or 0
    usage["input_plus_output_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def turn_groups(records: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for record in records:
        turn_id = record.get("turnId")
        group = groups.setdefault(turn_id, {"turn_id": turn_id, "records": [],
                                            "started_at": record.get("startedAt")})
        group["records"].append(record)
    return sorted(groups.values(), key=lambda g: g["started_at"] or "")


def patch_result(result: dict, cache_dir: Path) -> dict | None:
    """Return a change note if the trial was patched, else None."""
    usage = result.get("usage") or {}
    if usage.get("requests"):
        # trial 级总量已在：只补回合明细（turn 记录丢失但总量来自同一 rollout）
        missing_turns = [t for t in result.get("turns", []) if t.get("zcode_turn_id") is None]
        if not missing_turns:
            return None
        groups = turn_groups(rollout_records(cache_dir))
        known = {t["zcode_turn_id"] for t in result["turns"] if t.get("zcode_turn_id")}
        unknown = [g for g in groups if g["turn_id"] not in known]
        if len(unknown) != len(missing_turns):
            return None
        for turn, group in zip(missing_turns, unknown):
            per = usage_totals(group["records"])
            turn["zcode_turn_id"] = group["turn_id"]
            turn["requests"] = per["requests"]
            turn["turn_usage"] = {"modelRequestCount": per["requests"],
                                  "inputTokens": per["input_tokens"],
                                  "outputTokens": per["output_tokens"],
                                  "cacheReadTokens": per["cache_read_tokens"]}
        return {"recovered": ["turn_breakdown"], "turns": len(missing_turns)}

    records = rollout_records(cache_dir)
    if not records:
        return None
    session_ids = {r.get("sessionId") for r in records}
    if len(session_ids) != 1:
        raise SystemExit(f"{result['run_id']}: rollout 含多个 session: {session_ids}")
    result["usage"] = usage_totals(records)
    result["session_id"] = session_ids.pop()
    result["agent_net_seconds"] = round(sum(r.get("durationMs") or 0 for r in records) / 1000, 3)
    groups = turn_groups(records)
    turns = result.get("turns", [])
    if len(groups) != len(turns):
        raise SystemExit(f"{result['run_id']}: rollout {len(groups)} 回合 vs result {len(turns)} 回合")
    for turn, group in zip(turns, groups):
        per = usage_totals(group["records"])
        if turn.get("zcode_turn_id") is None:
            turn["zcode_turn_id"] = group["turn_id"]
        turn["requests"] = per["requests"]
        turn["turn_usage"] = {"modelRequestCount": per["requests"],
                              "inputTokens": per["input_tokens"],
                              "outputTokens": per["output_tokens"],
                              "cacheReadTokens": per["cache_read_tokens"]}
    return {"recovered": ["usage", "session_id", "agent_net_seconds", "turn_breakdown"],
            "requests": result["usage"]["requests"],
            "input_tokens": result["usage"]["input_tokens"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group-dir", default=str(ROOT / f"data/raw/{GROUP_PREFIX}"))
    args = parser.parse_args()
    group = Path(args.group_dir)

    patched: list[str] = []
    for condition_dir in sorted(p for p in group.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for result_path in sorted(condition_dir.glob("replicate-*/result.json")):
            result = json.loads(result_path.read_text(encoding="utf-8"))
            cache_dir = CACHE / result["run_id"]
            note = patch_result(result, cache_dir)
            if note is None:
                continue
            result["usage_recovery"] = {
                "source": "zcode-storage/cli/rollout/model-io",
                "note": "usage 由 rollout 原始记录补记：回合被超时杀掉，runner 收尾解析失败",
                **note,
            }
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
            patched.append(f"{condition_dir.name}/{result_path.parent.name}")
            print(f"补记 {condition_dir.name}/{result_path.parent.name}: {note}")

    summary_path = group / "group-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    results = json.loads((group / "group-summary.json").read_text(encoding="utf-8"))["results"]
    result_by_key = {}
    for condition_dir in sorted(p for p in group.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for result_path in sorted(condition_dir.glob("replicate-*/result.json")):
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result_by_key[(result["condition"], result["replicate_id"])] = result

    results = summary["results"]
    for entry in results:
        trial = result_by_key.get((entry["condition"], entry["replicate_id"]))
        if trial is None:
            continue
        entry["usage"] = {**trial["usage"]}
        entry["session_id"] = trial.get("session_id")
        entry["agent_net_seconds"] = trial.get("agent_net_seconds", entry.get("agent_net_seconds"))
    for condition, values in summary["condition_summaries"].items():
        trials = [r for r in result_by_key.values() if r["condition"] == condition]
        usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        for trial in trials:
            for key in usage:
                usage[key] += trial["usage"][key]
        values["usage"] = usage
        values["agent_net_seconds"] = round(sum(t.get("agent_net_seconds") or 0 for t in trials), 3)
        values["verifier_seconds"] = round(sum(t.get("verifier_seconds") or 0 for t in trials), 3)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"\n共补记 {len(patched)} 个 trial，group-summary 已重算: {summary_path}")


if __name__ == "__main__":
    main()
