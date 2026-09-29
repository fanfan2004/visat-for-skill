"""Run the task-002 local OpenCode three-condition, three-replicate experiment.

This controller intentionally materializes only local task copies and invokes the
host OpenCode binary directly.  It does not call BenchFlow, SkillsBench, Docker,
or any container execution route.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from experiments.formal.run_travel_planning_vista_group import (
    current_opencode_config,
    event_summary,
    link_opencode_auth_store,
    parse_events,
)
from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.runtime import DisclosureRouter, SkillManifest
from vista.runtime.opencode_local_backend import command_for_turn, freeze_launcher


REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "enterprise-information-search"
ENVIRONMENT = TASK_SOURCE / "environment"
DATA_SOURCE = ENVIRONMENT / "DATA"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-002"
RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task002-local-3x3-minimaxm3-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
)
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task002-local-opencode-minimaxm3-public-diagnostics-resumable-timeout-noskill-native-pdvista-3x3",
)
LOCAL_ROOT = Path(
    os.environ.get("VISTA_EXPERIMENT_ROOT", Path.home() / "Library" / "Caches" / "VistaExperiments")
).expanduser().resolve()
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv" / "bin" / "python")
).expanduser().absolute()
MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
VERIFIER_TIMEOUT_SECONDS = 300
CONDITIONS = ("noskill", "native", "pd-vista")
REPLICATES = (1, 2, 3)
SCHEDULE = (
    (1, "noskill"), (1, "native"), (1, "pd-vista"),
    (2, "native"), (2, "pd-vista"), (2, "noskill"),
    (3, "pd-vista"), (3, "noskill"), (3, "native"),
)
# A narrowly scoped recovery run may repeat only trials quarantined for an
# external provider fault.  It keeps their original replicate identifiers and
# uses fresh workspace/state/session directories.  Example:
# VISTA_SELECTED_TRIALS="pd-vista:3,noskill:3,native:3".
_selected_trials = os.environ.get("VISTA_SELECTED_TRIALS", "").strip()
if _selected_trials:
    _requested = {tuple(item.strip().rsplit(":", 1)) for item in _selected_trials.split(",") if item.strip()}
    SCHEDULE = tuple((replicate, condition) for replicate, condition in SCHEDULE
                     if (condition, str(replicate)) in _requested)
    if not SCHEDULE or len(SCHEDULE) != len(_requested):
        raise RuntimeError("VISTA_SELECTED_TRIALS must name unique scheduled trials as condition:replicate")
MODULE_IDS = ("enterprise-artifact-search",)
# A controller-only, zero-model import-path check occurred before the formal batch.
# It has no task result, model request, or session and is explicitly excluded from analysis.
EXCLUDED_DIAGNOSTIC_BATCHES = [
    {"run_id": "formal-task002-local-3x3-minimaxm3-20260822-01", "completed_result_files": 0,
     "reason": "controller import-path diagnostic failed before any OpenCode request; excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-02", "completed_result_files": 0,
      "reason": "controller workspace location was denied by the local sandbox before any OpenCode request; excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-03", "completed_result_files": 0,
      "reason": "controller security gate rejected an in-repository workspace before any OpenCode request; excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-04", "completed_result_files": 9,
      "reason": "network-isolated infrastructure diagnostic: all nine trials ended in retryable API connection errors with zero requests, tokens, tool uses, and task outputs; excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-05", "completed_result_files": 0,
      "reason": "foreground-controller diagnostic: execution host ended the controller parent while an OpenCode child was still completing; incomplete controller evidence excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-06", "completed_result_files": 0,
      "reason": "background-controller diagnostic: background process did not persist in the execution host and made no model request; excluded before formal comparison"}
    ,{"run_id": "formal-task002-local-3x3-minimaxm3-20260822-07", "completed_result_files": 0,
      "reason": "foreground-controller retry diagnostic: execution host ended the controller before the first model turn could be reconciled; incomplete controller evidence excluded before formal comparison"}
]

COMMON_PROMPT = """Solve the isolated enterprise-information-search task in this directory. Work only inside this directory and do not access parent or external directories. First read task.md and question.txt. On this host, /root/DATA maps to ./DATA, /root/question.txt maps to ./question.txt, and /root/answer.json maps to ./answer.json. Search the local DATA artifacts, answer every question accurately, and write ./answer.json in the exact requested JSON structure. Include a positive numeric tokens field for every question. You may use ordinary local shell/Python tools to inspect only this workspace. The controller runs the complete acceptance check privately."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT + " No task Skill is available in this condition.",
    "native": COMMON_PROMPT + " The full original guidance module is available at ./modules/enterprise-artifact-search/SKILL.md; read and use it.",
    "pd-vista": COMMON_PROMPT + " Read VISTA.md. The original guidance module is initially hidden; after a public acceptance failure, VISTA may disclose one relevant module at ./modules/enterprise-artifact-search/SKILL.md.",
}


def dependency_readiness() -> dict[str, Any]:
    """Perform a zero-model readiness audit before the unique formal batch."""

    checks: dict[str, Any] = {
        "task_identity": (REPO / "tasks" / "pilot" / "task-002" / "task.yaml").is_file(),
        "task_instruction": (TASK_SOURCE / "task.md").is_file(),
        "data_directory": DATA_SOURCE.is_dir(),
        "question": (ENVIRONMENT / "question.txt").is_file(),
        "original_verifier": (TASK_SOURCE / "verifier" / "test_outputs.py").is_file(),
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "native_modules": {
            module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS
        },
        "vista_root": (VISTA_SOURCE / "SKILL.md").is_file(),
        "vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
        "vista_source": (VISTA_SOURCE / "source.yaml").is_file(),
        "public_compile_entry": "not_applicable_python_retrieval_task",
    }
    command = [
        str(VERIFIER_PYTHON), "-m", "pytest", "--collect-only", "-q",
        str(TASK_SOURCE / "verifier" / "test_outputs.py"),
        f"--confcutdir={TASK_SOURCE / 'verifier'}",
    ]
    try:
        completed = subprocess.run(command, cwd=TASK_SOURCE / "verifier", capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=120,
                                   check=False)
        checks["verifier_collect_returncode"] = completed.returncode
        checks["verifier_collect_output"] = (completed.stdout or "") + (completed.stderr or "")
        checks["verifier_collect"] = completed.returncode == 0 and "5 tests collected" in checks["verifier_collect_output"]
    except Exception as exc:
        checks["verifier_collect"] = False
        checks["verifier_collect_error"] = str(exc)
    checks["ready"] = all(
        item if isinstance(item, bool) else all(item.values()) if isinstance(item, dict) else True
        for key, item in checks.items()
        if key not in {"public_compile_entry", "verifier_collect_returncode", "verifier_collect_output", "verifier_collect_error"}
    )
    return checks


def prepare_trial(condition: str, replicate: int) -> tuple[Path, Path, Path, Path]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    base = LOCAL_ROOT / f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}"
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse task/model evidence: {base}")
    workspace, verifier, router_source = base / "workspace", base / "verifier", base / "router-source"
    workspace.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(exist_ok=True)
    router_source.mkdir(exist_ok=True)
    (workspace / "modules").mkdir(exist_ok=True)
    # Each trial gets a complete, independent data and task copy.  Only dependency caches may be shared.
    shutil.copytree(DATA_SOURCE, workspace / "DATA")
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    shutil.copy2(ENVIRONMENT / "question.txt", workspace / "question.txt")
    if condition == "native":
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, workspace / "modules" / module)
    elif condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, router_source / "modules" / module)
    verifier_text = (TASK_SOURCE / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    verifier_text = verifier_text.replace('ANSWER_PATH = "/root/answer.json"', f'ANSWER_PATH = r"{workspace / "answer.json"}"')
    verifier_text = verifier_text.replace('DATA_PATH = "/root/DATA"', f'DATA_PATH = r"{workspace / "DATA"}"')
    verifier_text = verifier_text.replace('QUESTION_PATH = "/root/question.txt"', f'QUESTION_PATH = r"{workspace / "question.txt"}"')
    (verifier / "test_outputs.py").write_text(verifier_text, encoding="utf-8")
    return base, workspace, verifier, router_source


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "no module named", "modulenotfounderror", "failed to import", "error while loading conftest",
        "unrecognized arguments", "unicodedecodeerror", "internalerror",
    ))


def run_verifier(verifier: Path, turn: int) -> tuple[bool, str, bool]:
    command = [str(VERIFIER_PYTHON), "-m", "pytest", "-q", str(verifier / "test_outputs.py"),
               f"--confcutdir={verifier}", f"--basetemp={verifier / f'pytest-temp-turn-{turn}'}"]
    completed = subprocess.run(command, cwd=verifier, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=VERIFIER_TIMEOUT_SECONDS,
                               check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = verifier_infrastructure_failure(output)
    return completed.returncode == 0 and not infra, output, infra


def public_feedback(workspace: Path, verifier_output: str, tool_errors: list[str]) -> tuple[list[str], str]:
    """Return a stable, safe diagnostic without leaking private expected values."""

    answer = workspace / "answer.json"
    kinds: list[str] = []
    notes: list[str] = []
    if not answer.is_file():
        kinds.append("incomplete_output")
        notes.append("answer.json is missing")
    else:
        try:
            value = json.loads(answer.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(key not in value for key in ("q1", "q2", "q3")):
                kinds.extend(["schema_error", "wrong_format"])
                notes.append("answer.json must be an object with q1, q2, and q3")
            elif any(not isinstance(value[key], dict) or not isinstance(value[key].get("answer"), list)
                     or not isinstance(value[key].get("tokens"), (int, float)) or value[key].get("tokens", 0) <= 0
                     for key in ("q1", "q2", "q3")):
                kinds.extend(["schema_error", "wrong_format"])
                notes.append("each answer must be a list and each tokens value must be positive numeric")
        except (OSError, json.JSONDecodeError):
            kinds.extend(["schema_error", "wrong_format"])
            notes.append("answer.json is not valid UTF-8 JSON")
    if tool_errors:
        kinds.append("runtime_error")
        notes.append("a local tool call failed; use only available workspace tools")
    if not kinds:
        kinds.append("test_failure")
        notes.append("the private acceptance check still fails; re-check all retrieval answers against local DATA")
    if "test_failure" not in kinds:
        kinds.append("test_failure")
    return list(dict.fromkeys(kinds)), "; ".join(dict.fromkeys(notes))


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(router_source / "modules" / module_id, destination)
    return destination / "SKILL.md"


def _timeout_completed(command: list[str], exc: subprocess.TimeoutExpired) -> subprocess.CompletedProcess[str]:
    def decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return subprocess.CompletedProcess(command, 124, stdout=decode(exc.stdout), stderr=decode(exc.stderr))


def run_replicate(condition: str, replicate: int, *, config_path: Path, requested_model: str) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate)
    parser = FeedbackParser() if condition == "pd-vista" else None
    router = DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml")) if condition == "pd-vista" else None
    disclosed: list[str] = []
    accessed_after_disclosure: list[str] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    final_pass, stop_reason = False, "max_turns"
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    process_env = os.environ.copy()
    link_opencode_auth_store(base)
    process_env["OPENCODE_CONFIG"] = str(config_path)
    process_env.pop("OPENCODE_CONFIG_DIR", None)
    process_env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    process_env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    process_env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")

    for turn in range(1, MAX_TURNS + 1):
        command = command_for_turn(workspace=workspace, prompt=prompt, session_id=session_id, model=requested_model)
        timed_out = False
        try:
            completed = subprocess.run(command, cwd=workspace, env=process_env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace",
                                       timeout=MODEL_TURN_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as exc:
            timed_out, completed = True, _timeout_completed(command, exc)
        (base / f"turn-{turn}.jsonl").write_text(completed.stdout or "", encoding="utf-8")
        (base / f"turn-{turn}.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
        summary = event_summary(parse_events(completed.stdout or ""), MODULE_IDS)
        if summary["session_ids"]:
            observed = summary["session_ids"][0]
            if session_id is not None and observed != session_id:
                stop_reason = "session_identity_changed"
                break
            session_id = observed
        accessed_now = [module for module in summary["accessed_modules"] if module in disclosed]
        if accessed_now:
            access_rounds += 1
            accessed_after_disclosure.extend(accessed_now)
        verifier_pass, verifier_output, verifier_infra = run_verifier(verifier, turn)
        turns.append({
            "turn": turn, "return_code": completed.returncode, "model_turn_timeout": timed_out,
            "event_summary": summary, "answer_exists": (workspace / "answer.json").is_file(),
            "verifier_pass": verifier_pass, "verifier_infrastructure_error": verifier_infra,
        })
        print(f"condition={condition} replicate={replicate} turn={turn} requests={summary['requests']} "
              f"tools={summary['tool_uses']} accessed={accessed_now} verifier_pass={verifier_pass} "
              f"model_timeout={timed_out}", flush=True)
        # The output is accepted even when OpenCode stalled after its tools wrote a valid file.
        if verifier_pass:
            final_pass = True
            stop_reason = "verifier_pass_after_model_timeout" if any(item["model_turn_timeout"] for item in turns) else "verifier_pass"
            break
        if verifier_infra:
            stop_reason = "verifier_infrastructure_error"
            break
        if summary["errors"]:
            stop_reason = summary["errors"][-1]["category"]
            break
        trigger_opportunities += 1
        feedback_types, diagnostic = public_feedback(workspace, verifier_output, summary["tool_errors"])
        newly_disclosed: list[str] = []
        if condition == "pd-vista":
            assert parser is not None and router is not None
            payload = json.dumps({"schema_version": 1, "kind": "vista_public_verifier_feedback",
                                  "outcome": "fail", "feedback_types": feedback_types, "summary": diagnostic})
            classification = parser.parse(FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                                          trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}")
            decision = router.route(classification, previous_modules=disclosed)
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(f"./modules/{module}/SKILL.md" for module in newly_disclosed) + ". Read it before repairing."
        elif condition == "native":
            instruction = "Use the original module already available under ./modules/."
        elif disclosed:
            instruction = "Use the already disclosed module: " + ", ".join(disclosed) + "."
        else:
            instruction = "No task Skill is available; repair from the public diagnostic and local DATA."
        prompt = (f"The public acceptance check failed with {', '.join(feedback_types)}. Public diagnostic: {diagnostic}. "
                  f"{instruction} Continue in the same session, work only inside this directory, and repair ./answer.json.")

    result_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    if (workspace / "answer.json").is_file():
        shutil.copy2(workspace / "answer.json", result_dir / "answer.json")
    actual_models = list(dict.fromkeys(model for item in turns for model in item["event_summary"]["actual_model_ids"]))
    actual_providers = list(dict.fromkeys(provider for item in turns for provider in item["event_summary"]["provider_ids"]))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model}:
        final_pass, stop_reason = False, "model_identity_changed"
    if actual_providers and set(actual_providers) != {expected_provider}:
        final_pass, stop_reason = False, "model_identity_changed"
    clean_closure = int(condition == "pd-vista" and final_pass and disclosure_count > 0 and access_rounds > 0)
    usage = {key: sum(int(item["event_summary"].get(key) or 0) for item in turns)
             for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
    quarantined = stop_reason in {"provider_policy_rejection", "api_error", "session_identity_changed", "verifier_infrastructure_error", "model_identity_changed"}
    status = ("passed_with_clean_vista_feedback_loop" if clean_closure else "passed" if final_pass and condition != "pd-vista"
              else "passed_without_clean_vista_feedback_loop" if final_pass else f"quarantined_{stop_reason}" if quarantined else "failed_max_turns")
    result = {
        "schema_version": 1, "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-002", "condition": condition, "replicate_id": replicate,
        "backend": "opencode-local-minimal-dependencies", "docker_used": False,
        "skillbench_execution_route_used": False, "requested_provider_model": requested_model,
        "expected_provider": expected_provider, "expected_actual_model": expected_model,
        "actual_provider_ids": actual_providers, "actual_model_ids": actual_models, "session_id": session_id,
        "turns": turns, "usage": usage, "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "disclosed_modules": disclosed, "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)),
        "vista_metrics": {"trigger_opportunities": trigger_opportunities, "routable_feedback": routable_feedback,
                          "successful_disclosures": disclosure_count, "next_round_module_access": access_rounds,
                          "clean_complete_closure": clean_closure},
        "final_verifier_pass": final_pass, "stop_reason": stop_reason, "status": status, "workspace": str(workspace),
    }
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def db_usage(workspace: str) -> dict[str, Any]:
    """Read isolated OpenCode records read-only for the post-hoc audit."""

    db = Path(workspace).parent / "opencode-state" / "data" / "opencode" / "opencode.db"
    evidence: dict[str, Any] = {"db_path": str(db), "exists": db.is_file(), "read_only": True,
                                "provider_ids": [], "model_ids": [], "recorded_cost": 0.0, "assistant_messages": 0}
    if not db.is_file():
        return evidence
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # The session row records the configured identity even when a provider failure occurs
        # before any assistant message is persisted.
        for (raw_model,) in conn.execute("select model from session"):
            try:
                model_value = json.loads(raw_model) if raw_model else {}
            except (TypeError, json.JSONDecodeError):
                model_value = {}
            if isinstance(model_value, dict):
                if model_value.get("providerID"):
                    evidence["provider_ids"].append(str(model_value["providerID"]))
                if model_value.get("id"):
                    evidence["model_ids"].append(str(model_value["id"]))
        for (raw,) in conn.execute("select data from message"):
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if value.get("role") != "assistant":
                continue
            evidence["assistant_messages"] += 1
            model = value.get("model") if isinstance(value.get("model"), dict) else {}
            provider_id = value.get("providerID") or model.get("providerID")
            model_id = value.get("modelID") or model.get("modelID") or model.get("id")
            if provider_id:
                evidence["provider_ids"].append(str(provider_id))
            if model_id:
                evidence["model_ids"].append(str(model_id))
            try:
                evidence["recorded_cost"] += float(value.get("cost") or 0)
            except (TypeError, ValueError):
                pass
    finally:
        conn.close()
    evidence["provider_ids"] = list(dict.fromkeys(evidence["provider_ids"]))
    evidence["model_ids"] = list(dict.fromkeys(evidence["model_ids"]))
    evidence["recorded_cost"] = round(evidence["recorded_cost"], 8)
    return evidence


def write_posthoc_audit(results: list[dict[str, Any]], requested_model: str) -> dict[str, Any]:
    per_trial: list[dict[str, Any]] = []
    for result in results:
        record = dict(result)
        record["opencode_db"] = db_usage(result["workspace"])
        per_trial.append(record)
    sessions = [item["session_id"] for item in per_trial]
    expected_provider, expected_model = requested_model.split("/", 1)
    conditions: dict[str, Any] = {}
    for condition in CONDITIONS:
        values = [item for item in per_trial if item["condition"] == condition]
        totals = {key: sum(int(item["usage"][key]) for item in values)
                  for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
        totals["input_plus_output_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        totals["recorded_cost"] = round(sum(float(item["opencode_db"]["recorded_cost"]) for item in values), 8)
        conditions[condition] = {"completed_trials": len(values), "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in values),
                                 "total_turns": sum(len(item["turns"]) for item in values),
                                 "model_turn_timeouts": sum(sum(bool(turn["model_turn_timeout"]) for turn in item["turns"]) for item in values),
                                 **totals}
        if condition == "pd-vista":
            conditions[condition].update({key: sum(int(item["vista_metrics"][key]) for item in values)
                                          for key in ("trigger_opportunities", "routable_feedback", "successful_disclosures", "next_round_module_access", "clean_complete_closure")})
            conditions[condition]["disclosed_modules"] = sorted({module for item in values for module in item["disclosed_modules"]})
    totals = {key: sum(int(item["usage"][key]) for item in per_trial)
              for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
    totals["input_plus_output_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["recorded_cost"] = round(sum(float(item["opencode_db"]["recorded_cost"]) for item in per_trial), 8)
    audit = {
        "schema_version": 1, "run_id": RUN_PREFIX, "audit_status": "passed" if all(item["final_verifier_pass"] for item in per_trial) else "completed_with_failures",
        "completed_trials": len(per_trial), "unique_sessions": len(set(sessions)), "session_ids_complete": all(sessions),
        "all_final_verifiers_passed": all(item["final_verifier_pass"] for item in per_trial),
        "docker_used": False, "skillbench_execution_route_used": False,
        "provider_policy_errors": sum(item["stop_reason"] == "provider_policy_rejection" for item in per_trial),
        "session_identity_errors": sum(item["stop_reason"] == "session_identity_changed" for item in per_trial),
        "model_identity": {"provider_id": expected_provider, "model_id": expected_model,
                             "consistent_across_all_trials": all(item["opencode_db"]["provider_ids"] == [expected_provider] and item["opencode_db"]["model_ids"] == [expected_model] for item in per_trial),
                             "evidence": "read-only query of each isolated OpenCode opencode.db message table"},
        "conditions": conditions, "totals": totals, "trials": per_trial,
        "excluded_runs": EXCLUDED_DIAGNOSTIC_BATCHES, "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
    }
    (RESULT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    launcher = freeze_launcher()
    readiness = dependency_readiness()
    if RESULT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse result root: {RESULT_ROOT}")
    RESULT_ROOT.mkdir(parents=True)
    (RESULT_ROOT / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not readiness["ready"]:
        return 2
    results = [run_replicate(condition, replicate, config_path=config_path, requested_model=requested_model)
               for replicate, condition in SCHEDULE]
    condition_summaries = {}
    for condition in CONDITIONS:
        values = [item for item in results if item["condition"] == condition]
        condition_summaries[condition] = {"planned_trials": sum(1 for _, cond in SCHEDULE if cond == condition), "completed_trials": len(values),
                                          "verifier_passes": sum(item["final_verifier_pass"] for item in values),
                                          "usage": {key: sum(item["usage"][key] for item in values)
                                                    for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}}
    summary = {"schema_version": 1, "protocol": PROTOCOL, "task_id": "task-002", "conditions": list(CONDITIONS),
               "replicates_per_condition": 3, "planned_trials": len(SCHEDULE), "completed_trials": len(results),
               "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
               "requested_provider_model": requested_model, "opencode": {"config_path": str(config_path), "launcher": launcher.launcher,
               "version": launcher.version, "binary_sha256": launcher.binary_sha256}, "condition_summaries": condition_summaries,
               "results": [{key: item[key] for key in ("condition", "replicate_id", "status", "final_verifier_pass", "session_id", "actual_model_ids", "actual_provider_ids", "usage", "initially_available_modules", "disclosed_modules", "accessed_modules_after_disclosure", "vista_metrics")} for item in results],
               "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES, "docker_used": False, "skillbench_execution_route_used": False}
    (RESULT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_posthoc_audit(results, requested_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
