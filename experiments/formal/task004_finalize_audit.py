"""Rebuild task4 audit JSON after serial recovery attempts."""

from __future__ import annotations

import json

from experiments.formal.run_task004_local_group import (
    AUDIT_ROOT,
    CONDITIONS,
    EXCLUDED_DIAGNOSTIC_BATCHES,
    PROTOCOL,
    RESULT_ROOT,
    REPLICATES,
    SCHEDULE,
    RUN_PREFIX,
    current_opencode_config,
    db_usage,
    freeze_launcher,
    load_latest_result,
    scan_excluded_attempts,
    write_posthoc_audit,
    MAX_TURNS,
    MODEL_TURN_TIMEOUT_SECONDS,
    VERIFIER_TIMEOUT_SECONDS,
)


def main() -> int:
    config_path, requested_model, _ = current_opencode_config()
    launcher = freeze_launcher()
    results = []
    for replicate in REPLICATES:
        for condition in CONDITIONS:
            value = load_latest_result(condition, replicate)
            if value is not None:
                results.append(value)

    excluded = scan_excluded_attempts()
    reclassified = [x for x in excluded if x.get("classification") == "controller_false_positive_rate_limit"]
    rate_limits = [x for x in excluded if str(x.get("status", "")) == "quarantined_rate_limit" and x.get("classification") != "controller_false_positive_rate_limit"]
    condition_summaries = {}
    for condition in CONDITIONS:
        values = [x for x in results if x["condition"] == condition]
        condition_summaries[condition] = {
            "planned_trials": 3,
            "result_files": len(values),
            "formal_included_trials": sum(not str(x.get("status", "")).startswith("quarantined_") for x in values),
            "excluded_trials": sum(str(x.get("status", "")).startswith("quarantined_") for x in values),
            "verifier_passes": sum(bool(x.get("final_verifier_pass")) for x in values if not str(x.get("status", "")).startswith("quarantined_")),
            "usage": {key: sum(int(x.get("usage", {}).get(key) or 0) for x in values if not str(x.get("status", "")).startswith("quarantined_")) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")},
        }

    summary = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "protocol": PROTOCOL,
        "task_id": "task-004",
        "benchmark_task_id": "powerlifting-coef-calc",
        "conditions": list(CONDITIONS),
        "replicates_per_condition": 3,
        "planned_trials": 9,
        "completed_trials": len(results),
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
        "requested_provider_model": requested_model,
        "opencode": {"config_path": str(config_path), "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256},
        "timeouts": {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS},
        "condition_summaries": condition_summaries,
        "results": [{key: item.get(key) for key in ("condition", "replicate_id", "attempt", "status", "stop_reason", "final_verifier_pass", "session_id", "actual_model_ids", "actual_provider_ids", "usage", "initially_available_modules", "disclosed_modules", "accessed_modules_after_disclosure", "disclosure_events", "vista_metrics", "final_output_files")} for item in results],
        "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
        "excluded_rate_limit_attempts": rate_limits,
        "excluded_attempts": excluded,
        "controller_reclassified_attempts": reclassified,
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "completion_note": "No-Skill-03 attempt-11 原始控制器因费用数字 0.00342978 的裸 429 子串发生误判；人工最终 Verifier 11/11 后已重新归类为正式通过，原始隔离证据保留。",
    }
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = write_posthoc_audit(results, requested_model, summary["excluded_rate_limit_attempts"])
    audit["controller_reclassified_attempts"] = reclassified
    audit["completion_note"] = summary["completion_note"]
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": RUN_PREFIX, "completed_trials": len(results), "excluded_rate_limit_attempts": len(summary["excluded_rate_limit_attempts"]), "audit_status": audit["audit_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
