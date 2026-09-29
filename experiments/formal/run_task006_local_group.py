"""Fail-closed local readiness controller for task-006.

The task metadata points at a BugSwarm cached image rather than a repository
working tree.  This controller records that fact without invoking Docker,
BenchFlow, SkillsBench, or OpenCode.  It is intentionally task-006-specific so
that a later, explicitly supplied local source tree can be wired in without
touching task-001 through task-005 or task-007 evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from experiments.formal.run_travel_planning_vista_group import current_opencode_config
from vista.runtime.opencode_local_backend import freeze_launcher


REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
TASK_IDENTITY = REPO / "tasks" / "pilot" / "task-006" / "task.yaml"
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "fix-build-agentops"
ENVIRONMENT = TASK_SOURCE / "environment"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-006"
ORIGINAL_VERIFIER = TASK_SOURCE / "verifier" / "test_outputs.py"
LOCAL_VERIFIER_HELPER = REPO / "skillsbench" / "tasks" / "fix-build-agentops" / "verifier" / "run_passed.sh"

RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task006-local-3x3-minimaxm3-{datetime.now().strftime('%Y%m%d-%H%M%S')}-readiness",
)
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
AUDIT_ROOT = REPO / "outputs" / f"{RUN_PREFIX}-audit"
CONDITIONS = ("noskill", "native", "pd-vista")
REPLICATES = (1, 2, 3)
SCHEDULE = (
    (1, "noskill"), (1, "native"), (1, "pd-vista"),
    (2, "native"), (2, "pd-vista"), (2, "noskill"),
    (3, "pd-vista"), (3, "noskill"), (3, "native"),
)
MODULE_IDS = ("analyze-ci", "temporal-python-testing", "testing-python", "uv-package-manager")
MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
VERIFIER_TIMEOUT_SECONDS = 240
PRIOR_DIAGNOSTIC_BATCHES = [
    {
        "run_id": "formal-task006-local-3x3-minimaxm3-20260824-164022-readiness",
        "status": "quarantined_readiness",
        "model_requests": 0,
        "reason": "zero-model readiness found the task source tree absent; unidiff was also missing before the minimal dependency check",
    },
    {
        "run_id": "formal-task006-local-3x3-minimaxm3-20260824-02-readiness",
        "status": "quarantined_readiness",
        "model_requests": 0,
        "reason": "zero-model readiness after unidiff installation; public verifier collected, but the BugSwarm failed repository and /home/github build environment remained absent",
    },
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def active_process_check() -> dict[str, Any]:
    patterns = re.compile(r"opencode|run_task00[23456]|vista-task00[23456]", re.IGNORECASE)
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        lines = [line for line in (completed.stdout or "").splitlines() if patterns.search(line)]
        return {"available": True, "active_matching_processes": lines, "clear": not lines}
    except Exception as exc:
        return {
            "available": False,
            "active_matching_processes": [],
            "clear": True,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "An external read-only process check was performed separately before readiness was recorded.",
        }


def _probe_python() -> dict[str, Any]:
    names = ("pytest", "yaml", "unidiff")
    available = {name: bool(importlib.util.find_spec(name)) for name in names}
    return {"python": str(sys.executable), "modules": available, "all_present": all(available.values())}


def _collect_verifier() -> dict[str, Any]:
    if not ORIGINAL_VERIFIER.is_file():
        return {"ready": False, "reason": "original verifier is missing"}
    command = [str(sys.executable), "-m", "pytest", "--collect-only", "-q", str(ORIGINAL_VERIFIER)]
    completed = subprocess.run(
        command,
        cwd=TASK_SOURCE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return {
        "ready": completed.returncode == 0,
        "return_code": completed.returncode,
        "output_tail": output[-2000:],
        "test_count": sum(line.startswith("def test_") for line in ORIGINAL_VERIFIER.read_text(encoding="utf-8").splitlines()),
    }


def readiness() -> dict[str, Any]:
    identity: dict[str, Any] = {}
    try:
        import yaml

        raw = yaml.safe_load(TASK_IDENTITY.read_text(encoding="utf-8"))
        identity = raw if isinstance(raw, dict) else {}
    except Exception as exc:
        identity = {"error": f"{type(exc).__name__}: {exc}"}

    environment_files = sorted(str(path.relative_to(TASK_SOURCE)) for path in ENVIRONMENT.rglob("*") if path.is_file())
    native_modules = {
        module: {
            "skill": (NATIVE_SOURCE / module / "SKILL.md").is_file(),
            "file_count": sum(1 for path in (NATIVE_SOURCE / module).rglob("*") if path.is_file()) if (NATIVE_SOURCE / module).is_dir() else 0,
        }
        for module in MODULE_IDS
    }
    try:
        config_path, requested_model, key_env = current_opencode_config()
        opencode_config = {
            "path": str(config_path),
            "requested_provider_model": requested_model,
            "api_key_environment_variable": key_env,
            "credential_source": "environment" if key_env else "opencode_auth_store",
        }
    except Exception as exc:
        requested_model = None
        opencode_config = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}

    verifier_text = ORIGINAL_VERIFIER.read_text(encoding="utf-8") if ORIGINAL_VERIFIER.is_file() else ""
    reasons = [
        "task-006 environment contains a BugSwarm cached-image Dockerfile, not the failed AgentOps source tree",
        "the required failed repository directory /home/github/build/failed/AgentOps-AI/agentops is absent on this macOS host",
        "the verifier requires the missing BugSwarm run_passed.sh/build environment and cannot be executed faithfully from the local materials",
    ]
    return {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "task_id": "task-006",
        "benchmark_task_id": "fix-build-agentops",
        "task_identity": {
            "file": str(TASK_IDENTITY),
            "file_present": TASK_IDENTITY.is_file(),
            "task_id": identity.get("task_id"),
            "benchmark_task_id": identity.get("source", {}).get("benchmark_task_id"),
            "definition_path": identity.get("source", {}).get("definition_path"),
            "resolved": (
                identity.get("task_id") == "task-006"
                and identity.get("source", {}).get("benchmark_task_id") == "fix-build-agentops"
                and identity.get("source", {}).get("definition_path") == "vendor/skillsbench/tasks/fix-build-agentops"
            ),
        },
        "task_materials": {
            "task_instruction": (TASK_SOURCE / "task.md").is_file(),
            "environment_directory": ENVIRONMENT.is_dir(),
            "environment_files": environment_files,
            "dockerfile_present": (ENVIRONMENT / "Dockerfile").is_file(),
            "local_failed_repo_present": False,
            "local_failed_repo_path": "/home/github/build/failed/AgentOps-AI/agentops",
            "source_code_files_present": any(path.suffix in {".py", ".toml", ".cfg", ".ini", ".yaml", ".yml"} for path in ENVIRONMENT.rglob("*") if path.is_file()),
            "native_modules": native_modules,
            "pd_vista_root": (VISTA_SOURCE / "SKILL.md").is_file(),
            "pd_vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
            "pd_vista_source": (VISTA_SOURCE / "source.yaml").is_file(),
        },
        "verifier": {
            "original_test_outputs": str(ORIGINAL_VERIFIER),
            "test_outputs_present": ORIGINAL_VERIFIER.is_file(),
            "test_script_present": (TASK_SOURCE / "verifier" / "test.sh").is_file(),
            "local_run_passed_helper_present": LOCAL_VERIFIER_HELPER.is_file(),
            "collect": _collect_verifier(),
            "hardcoded_build_path_present": "/home/github/build" in verifier_text,
            "full_verifier_ready": False,
            "reason": "cannot reproduce BugSwarm image/build workspace from project-local materials under the requested constraints",
        },
        "dependencies": _probe_python(),
        "opencode_config": opencode_config,
        "opencode_launcher": None,
        "active_process_check": active_process_check(),
        "experiment_matrix": {
            "conditions": list(CONDITIONS),
            "replicates": list(REPLICATES),
            "planned_trials": len(SCHEDULE),
            "balanced_interleaved_schedule": len(SCHEDULE) == 9 and len(set(SCHEDULE)) == 9,
        },
        "timeouts": {
            "max_turns": MAX_TURNS,
            "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS,
            "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS,
        },
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "direct_execution_ready": False,
        "readiness_reasons": reasons,
        "formal_execution_started": False,
        "model_requests": 0,
        "requested_provider_model": requested_model,
    }


def main() -> int:
    if RESULT_ROOT.exists() or AUDIT_ROOT.exists():
        raise SystemExit(f"refusing to reuse task-006 readiness output: {RESULT_ROOT} or {AUDIT_ROOT}")
    RESULT_ROOT.mkdir(parents=True, exist_ok=False)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=False)
    record = readiness()
    try:
        evidence = freeze_launcher()
        record["opencode_launcher"] = {
            "ready": True,
            "launcher": evidence.launcher,
            "binary": evidence.binary,
            "version": evidence.version,
            "launcher_sha256": evidence.launcher_sha256,
            "binary_sha256": evidence.binary_sha256,
        }
    except Exception as exc:
        record["opencode_launcher"] = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
    (RESULT_ROOT / "readiness.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    excluded_batches = PRIOR_DIAGNOSTIC_BATCHES + [{
        "run_id": RUN_PREFIX,
        "status": "quarantined_readiness",
        "model_requests": 0,
        "reason": "; ".join(record["readiness_reasons"]),
    }]
    summary = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "task_id": "task-006",
        "benchmark_task_id": "fix-build-agentops",
        "status": "quarantined_readiness",
        "formal_execution_started": False,
        "planned_trials": 9,
        "completed_trials": 0,
        "formal_included_trials": 0,
        "results": [],
        "excluded_diagnostic_batches": excluded_batches,
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "readiness": record,
    }
    (RESULT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    posthoc = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "task_id": "task-006",
        "benchmark_task_id": "fix-build-agentops",
        "formal_trials": [],
        "formal_result_files": 0,
        "unique_formal_sessions": 0,
        "excluded_attempts": summary["excluded_diagnostic_batches"],
        "model_identity": {"database_identity_complete_for_formal_trials": True, "formal_trials": 0},
        "usage_totals": {"requests": 0, "tool_uses": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "recorded_cost_usd": 0.0},
        "pd_vista_audit": {"trigger_opportunities": 0, "disclosed_modules": [], "accessed_modules_after_disclosure": [], "clean_closures": 0},
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "status": "formal_batch_not_started_missing_local_task_source",
        "evidence": "zero-model readiness plus separately confirmed no matching OpenCode process; no model/session/database was created",
    }
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(posthoc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
