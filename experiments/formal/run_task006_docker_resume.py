"""Resume the unfinished task-006 Docker trials without rerunning passed groups.

This controller aggregates the five passed trials from the stopped 20260827-10
batch, then runs only the four remaining balanced-schedule trials.  A rate
limit attempt is retained as excluded evidence and stops this continuation
immediately; it is never retried by this controller.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("VISTA_RUN_PREFIX", "formal-task006-docker-local-3x3-minimaxm3-20260827-17")
os.environ.setdefault("VISTA_EXPERIMENT_ROOT", "/private/tmp/vista-task006-docker-experiments-20260827-17")

from experiments.formal import run_task006_docker_group as runner


SOURCE_BATCH = "formal-task006-docker-local-3x3-minimaxm3-20260827-15"
SOURCE_RESULT_ROOT = runner.REPO / "data" / "raw" / SOURCE_BATCH
SOURCE_AUDIT_PATH = runner.REPO / "outputs" / f"{SOURCE_BATCH}-audit" / "posthoc-audit.json"
REPLACED_SOURCE_BATCH = "formal-task006-docker-local-3x3-minimaxm3-20260827-13"
INTERRUPTED_BATCH = "formal-task006-docker-local-3x3-minimaxm3-20260827-11"
INTERRUPTED_ROOT = Path("/private/tmp/vista-task006-docker-experiments-20260827-11")
RATE_LIMIT_BATCH = "formal-task006-docker-local-3x3-minimaxm3-20260827-16"
RATE_LIMIT_ROOT = Path("/private/tmp/vista-task006-docker-experiments-20260827-16")
REMAINING_SCHEDULE = ((3, "pd-vista"),)
FULL_SCHEDULE = runner.SCHEDULE


def copy_previous_formal_results() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_paths = sorted(SOURCE_RESULT_ROOT.glob("*/replicate-*/result.json"))
    source_paths = [path for path in source_paths if not (path.parent.parent.name == "pd-vista" and path.parent.name == "replicate-03")]
    if len(source_paths) != 8:
        raise RuntimeError(f"expected 8 source results after excluding the contaminated PD-VISTA replicate-03, found {len(source_paths)} in {SOURCE_RESULT_ROOT}")
    formal: list[dict[str, Any]] = []
    for source_json in source_paths:
        source_dir = source_json.parent
        result = json.loads(source_json.read_text(encoding="utf-8"))
        if str(result.get("status", "")).startswith("quarantined_"):
            raise RuntimeError(f"source result is an excluded infrastructure/limit attempt: {source_json}")
        relative = source_dir.relative_to(SOURCE_RESULT_ROOT)
        target_dir = runner.RESULT_ROOT / relative
        target_dir.mkdir(parents=True, exist_ok=False)
        for artifact in source_dir.iterdir():
            if artifact.name == "result.json":
                continue
            destination = target_dir / artifact.name
            if artifact.is_dir():
                shutil.copytree(artifact, destination)
            else:
                shutil.copy2(artifact, destination)
        result["result_directory"] = str(target_dir)
        result["source_partial_batch"] = SOURCE_BATCH
        result["aggregated_into_run"] = runner.RUN_PREFIX
        (target_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        formal.append(result)

    excluded: list[dict[str, Any]] = []
    source_excluded = SOURCE_RESULT_ROOT / "excluded-attempts"
    if source_excluded.is_dir():
        target_excluded = runner.RESULT_ROOT / "excluded-attempts" / f"source-{SOURCE_BATCH}"
        shutil.copytree(source_excluded, target_excluded)
        for excluded_json in sorted(source_excluded.rglob("excluded-attempt.json")):
            excluded.append(json.loads(excluded_json.read_text(encoding="utf-8")))
    interrupted_attempt = next(INTERRUPTED_ROOT.glob("*/turn-1.jsonl"), None) if INTERRUPTED_ROOT.is_dir() else None
    if interrupted_attempt is not None:
        session_id = None
        try:
            events = runner.parse_events(interrupted_attempt.read_text(encoding="utf-8"))
            session_ids = runner.event_summary(events, runner.MODULE_IDS).get("session_ids", [])
            session_id = session_ids[0] if session_ids else None
        except Exception:
            pass
        excluded.append({
            "batch": INTERRUPTED_BATCH,
            "condition": "noskill",
            "replicate_id": 2,
            "attempt": 1,
            "status": "quarantined_verifier_infrastructure_error",
            "stop_reason": "disk_full_after_verifier_timeout",
            "session_id": session_id,
            "result_directory": str(interrupted_attempt.parent),
            "reason": "continuation interrupted by local disk exhaustion while writing verifier evidence; excluded from formal statistics",
        })
    # The previous PD-VISTA replacement stopped on HTTP 429 in turn 2.  Its
    # temporary workspace was cleaned after the stop, so retain a compact,
    # explicit exclusion record instead of pretending it was a task failure.
    if not any(item.get("batch") == RATE_LIMIT_BATCH for item in excluded):
        excluded.append({
            "batch": RATE_LIMIT_BATCH,
            "condition": "pd-vista",
            "replicate_id": 3,
            "attempt": 1,
            "status": "quarantined_rate_limit",
            "stop_reason": "http_429_rate_limit",
            "session_id": None,
            "result_directory": str(RATE_LIMIT_ROOT),
            "reason": "shared provider HTTP 429 observed in turn 2; continuation stopped immediately; temporary workspace was cleaned after the stop and this attempt is excluded from formal statistics",
            "raw_evidence_retained": False,
        })
    return formal, excluded


def cached_posthoc_audit(results: list[dict[str, Any]], excluded_attempts: list[dict[str, Any]], requested_model: str) -> dict[str, Any]:
    """Audit the eight retained groups from cached read-only DB results.

    The source temporary workspaces were removed to recover disk space.  The
    prior posthoc JSON is the immutable audit cache for those groups; only the
    fresh replacement group is queried from its still-present isolated DB.
    """
    if not SOURCE_AUDIT_PATH.is_file():
        raise RuntimeError(f"missing cached source audit: {SOURCE_AUDIT_PATH}")
    cached = json.loads(SOURCE_AUDIT_PATH.read_text(encoding="utf-8"))
    cached_by_key = {(item.get("condition"), int(item.get("replicate_id"))): item for item in cached.get("formal_results", [])}
    expected_provider, expected_model = requested_model.split("/", 1)
    for result in results:
        key = (result.get("condition"), int(result.get("replicate_id")))
        cached_result = cached_by_key.get(key)
        if cached_result is not None:
            for field in ("opencode_db_audit", "db_session_count", "db_recorded_cost_usd", "db_tokens"):
                if field in cached_result:
                    result[field] = cached_result[field]
            result["db_audit_source"] = str(SOURCE_AUDIT_PATH)
            continue
        base = Path(result["workspace"]).parent
        db_audits = [runner.audit_db(db, expected_model, expected_provider) for db in sorted(base.rglob("opencode.db"))]
        result["opencode_db_audit"] = db_audits
        result["db_session_count"] = sum(item.get("session_count", 0) for item in db_audits)
        result["db_recorded_cost_usd"] = sum(float(session.get("recorded_cost_usd") or 0) for item in db_audits for session in item.get("sessions", []))
        result["db_tokens"] = {key: sum(int(session.get(key) or 0) for item in db_audits for session in item.get("sessions", [])) for key in ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")}
        result["db_audit_source"] = "fresh read-only sqlite query"
    sessions = [session for result in results for audit in result.get("opencode_db_audit", []) for session in audit.get("sessions", [])]
    audit = {
        "schema_version": 1,
        "run_id": runner.RUN_PREFIX,
        "task_id": "task-006",
        "benchmark_task_id": "fix-build-agentops",
        "formal_results": results,
        "excluded_attempts": excluded_attempts,
        "formal_trial_count": len(results),
        "formal_result_files": len(results),
        "unique_formal_sessions": len({item["session_id"] for item in sessions}),
        "formal_sessions_with_expected_provider_model": bool(sessions) and all(item["model_matches"] and item["provider_matches"] for item in sessions),
        "docker_used": True,
        "docker_image": runner.IMAGE,
        "skillbench_execution_route_used": False,
        "oracle_used": False,
        "evidence": "Read-only sqlite queries against the fresh replacement group's isolated opencode.db, plus cached read-only sqlite audit JSON for the eight retained source groups; raw turn JSONL and final Docker Verifier logs were retained in the result roots.",
        "excluded_note": "Readiness, disk-full interruption, contaminated two-session replacement, and shared 429 attempts are listed explicitly outside formal statistics.",
        "cached_source_audit": str(SOURCE_AUDIT_PATH),
    }
    runner.AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (runner.AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def save_excluded(result: dict[str, Any], excluded_attempts: list[dict[str, Any]]) -> None:
    saved = runner.save_excluded_attempt(result)
    excluded_attempts.append({
        "condition": result["condition"],
        "replicate_id": result["replicate_id"],
        "attempt": result["attempt"],
        "status": result["status"],
        "stop_reason": result["stop_reason"],
        "session_id": result.get("session_id"),
        "result_directory": saved["result_directory"],
        "reason": "shared provider 429/zero-request limit; excluded and continuation stopped immediately" if result["status"] == "quarantined_rate_limit" else "isolated model/session/verifier infrastructure fault; excluded and retried serially",
    })


def condition_summaries(formal: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for condition in runner.CONDITIONS:
        values = [item for item in formal if item["condition"] == condition]
        excluded_values = [item for item in excluded if item.get("condition") == condition]
        summaries[condition] = {
            "planned_trials": 3,
            "formal_trials": len(values),
            "result_files": len(values),
            "quarantined_attempts": len(excluded_values),
            "verifier_passes": sum(bool(item.get("final_verifier_pass")) for item in values),
            "usage": {key: sum(int(item.get("usage", {}).get(key) or 0) for item in values) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")},
        }
    return summaries


def write_summary(formal: list[dict[str, Any]], excluded: list[dict[str, Any]], requested_model: str, launcher: Any, config_path: Path, readiness_run_id: str, stopped_on_rate_limit: bool) -> None:
    included = [item for item in formal if not str(item.get("status", "")).startswith("quarantined_")]
    summary = {
        "schema_version": 1,
        "run_id": runner.RUN_PREFIX,
        "status": "complete" if len(formal) == 9 and len(included) == 9 else "stopped_on_429" if stopped_on_rate_limit else "partial",
        "protocol": runner.PROTOCOL,
        "task_id": "task-006",
        "benchmark_task_id": "fix-build-agentops",
        "conditions": list(runner.CONDITIONS),
        "replicates_per_condition": 3,
        "planned_trials": 9,
        "completed_trials": len(formal),
        "formal_included_trials": len(included),
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in FULL_SCHEDULE],
        "source_partial_batch": SOURCE_BATCH,
        "excluded_contaminated_group": {"condition": "pd-vista", "replicate_id": 3, "source_batch": REPLACED_SOURCE_BATCH, "reason": "isolated opencode.db contained two session rows although event logs used one session; replaced by a fresh single-session rerun"},
        "remaining_schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in REMAINING_SCHEDULE],
        "requested_provider_model": requested_model,
        "opencode": {"config_path": str(config_path), "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256},
        "docker": {"used": True, "image": runner.IMAGE, "base_image": runner.BASE_IMAGE, "platform": "linux/amd64", "container_per_attempt": True, "reuse_within_attempt": True},
        "skillbench_execution_route_used": False,
        "oracle_used": False,
        "timeouts": {"max_turns": runner.MAX_TURNS, "model_turn_timeout_seconds": runner.MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": runner.VERIFIER_TIMEOUT_SECONDS},
        "excluded_diagnostic_batches": [runner.READINESS_RUN_ID, "formal-task006-docker-local-3x3-minimaxm3-20260827-15-00-readiness", "formal-task006-docker-local-3x3-minimaxm3-20260827-14-00-readiness", "formal-task006-docker-local-3x3-minimaxm3-20260827-12-00-readiness", "formal-task006-local-3x3-minimaxm3-20260824-164022-readiness", "formal-task006-local-3x3-minimaxm3-20260824-02-readiness", "formal-task006-local-3x3-minimaxm3-20260824-03-readiness-final", "formal-task006-docker-local-3x3-minimaxm3-20260826-01", "formal-task006-docker-local-3x3-minimaxm3-20260826-02", "formal-task006-docker-local-3x3-minimaxm3-20260826-03", "formal-task006-docker-local-3x3-minimaxm3-20260826-04", "formal-task006-docker-local-3x3-minimaxm3-20260826-05", "formal-task006-docker-local-3x3-minimaxm3-20260826-06", "formal-task006-docker-local-3x3-minimaxm3-20260826-07", "formal-task006-docker-local-3x3-minimaxm3-20260826-08", "formal-task006-docker-local-3x3-minimaxm3-20260827-09"],
        "excluded_interrupted_batches": [INTERRUPTED_BATCH, RATE_LIMIT_BATCH],
        "excluded_rate_limit_batches": [RATE_LIMIT_BATCH],
        "replaced_source_groups": [{"condition": "pd-vista", "replicate_id": 3, "source_batch": REPLACED_SOURCE_BATCH, "reason": "two opencode.db session rows"}],
        "excluded_attempts": excluded,
        "condition_summaries": condition_summaries(formal, excluded),
        "results": [{"condition": item["condition"], "replicate_id": item["replicate_id"], "attempt": item["attempt"], "status": item["status"], "final_verifier_pass": item["final_verifier_pass"], "session_id": item["session_id"], "actual_model_ids": item["actual_model_ids"], "actual_provider_ids": item["actual_provider_ids"], "usage": item["usage"], "initially_available_modules": item["initially_available_modules"], "disclosed_modules": item["disclosed_modules"], "accessed_modules_after_disclosure": item["accessed_modules_after_disclosure"], "disclosure_events": item["disclosure_events"], "vista_metrics": item["vista_metrics"]} for item in formal],
        "readiness_run_id": readiness_run_id,
        "stopped_on_rate_limit": stopped_on_rate_limit,
    }
    runner.RESULT_ROOT.joinpath("group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if runner.RESULT_ROOT.exists() or runner.AUDIT_ROOT.exists() or runner.READINESS_RESULT_ROOT.exists() or runner.READINESS_AUDIT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse task6 continuation output directory: {runner.RESULT_ROOT}")
    config_path, requested_model, key_env = runner.current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    launcher = runner.freeze_launcher()
    image = runner.ensure_image()
    concurrency = runner.wait_for_shared_opencode_clear()
    readiness = runner.readiness_report(config_path, requested_model, launcher, image, concurrency)
    runner.write_readiness(readiness)
    if not readiness["ready"]:
        print(f"task6 continuation readiness failed; diagnostic batch={runner.READINESS_RUN_ID}", flush=True)
        return 2
    if not concurrency.get("clear", False):
        raise RuntimeError("shared task OpenCode processes remained active before formal start")

    runner.RESULT_ROOT.mkdir(parents=True, exist_ok=False)
    runner.AUDIT_ROOT.mkdir(parents=True, exist_ok=False)
    formal_results, excluded_attempts = copy_previous_formal_results()
    stopped_on_rate_limit = False

    for replicate, condition in REMAINING_SCHEDULE:
        latest: dict[str, Any] | None = None
        for attempt in range(1, max(runner.MAX_RATE_LIMIT_RETRIES, runner.MAX_INFRA_RETRIES) + 2):
            latest = runner.run_attempt(condition, replicate, attempt, requested_model=requested_model)
            status = str(latest.get("status", ""))
            if status == "quarantined_rate_limit":
                save_excluded(latest, excluded_attempts)
                stopped_on_rate_limit = True
                break
            if status.startswith("quarantined_"):
                save_excluded(latest, excluded_attempts)
                retry_limit = runner.MAX_INFRA_RETRIES
                if attempt <= retry_limit:
                    backoff_seconds = min(300, 30 * (2 ** min(attempt - 1, 3)))
                    print(f"excluded {status} for {condition} replicate={replicate}; backing off {backoff_seconds}s before retry", flush=True)
                    for _ in range(backoff_seconds // 30):
                        time.sleep(30)
                    continue
            break
        if stopped_on_rate_limit:
            break
        if latest is None:
            raise RuntimeError(f"no result for {condition} replicate {replicate}")
        formal_results.append(runner.save_formal_result(latest))

    audit = cached_posthoc_audit(formal_results, excluded_attempts, requested_model)
    write_summary(formal_results, excluded_attempts, requested_model, launcher, config_path, runner.READINESS_RUN_ID, stopped_on_rate_limit)
    print(json.dumps({"run_id": runner.RUN_PREFIX, "completed_trials": len(formal_results), "formal_included_trials": len([item for item in formal_results if not str(item.get("status", "")).startswith("quarantined_")]), "stopped_on_rate_limit": stopped_on_rate_limit, "unique_formal_sessions": audit.get("unique_formal_sessions"), "audit_root": str(runner.AUDIT_ROOT)}, ensure_ascii=False), flush=True)
    return 0 if len(formal_results) == 9 and not stopped_on_rate_limit else 1


if __name__ == "__main__":
    raise SystemExit(main())
