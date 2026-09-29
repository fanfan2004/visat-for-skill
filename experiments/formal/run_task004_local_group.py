"""Run task-004 locally with OpenCode in a balanced 3-condition experiment.

This runner is task-004 specific.  It creates fresh task, verifier, workspace,
router, and OpenCode state directories for every attempt and never invokes
Docker, BenchFlow, or the SkillsBench execution platform.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
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
TASK_IDENTITY = REPO / "tasks" / "pilot" / "task-004" / "task.yaml"
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "powerlifting-coef-calc"
ENVIRONMENT = TASK_SOURCE / "environment"
INPUT_WORKBOOK = ENVIRONMENT / "openipf_cleaned.xlsx"
DATA_README = ENVIRONMENT / "data-readme.md"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-004"

RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX", "formal-task004-local-3x3-minimaxm3-20260823-03"
)
READINESS_RUN_ID = "formal-task004-local-3x3-minimaxm3-20260823-00-readiness"
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task004-local-opencode-minimaxm3-public-diagnostics-resumable-timeout-noskill-native-pdvista-3x3",
)
if os.environ.get("VISTA_EXPERIMENT_ROOT"):
    LOCAL_ROOT = Path(os.environ["VISTA_EXPERIMENT_ROOT"]).expanduser().resolve()
else:
    LOCAL_ROOT = Path("/private/tmp") / f"vista-task004-{RUN_PREFIX}"
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
AUDIT_ROOT = REPO / "outputs" / f"{RUN_PREFIX}-audit"
READINESS_AUDIT_ROOT = REPO / "outputs" / f"{READINESS_RUN_ID}-audit"
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv" / "bin" / "python")
).expanduser().absolute()

MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
VERIFIER_TIMEOUT_SECONDS = 300
MAX_RATE_LIMIT_RETRIES = 3
CONDITIONS = ("noskill", "native", "pd-vista")
REPLICATES = (1, 2, 3)
SCHEDULE = (
    (1, "noskill"),
    (1, "native"),
    (1, "pd-vista"),
    (2, "native"),
    (2, "pd-vista"),
    (2, "noskill"),
    (3, "pd-vista"),
    (3, "noskill"),
    (3, "native"),
)
MODULE_IDS = ("powerlifting", "senior-data-scientist", "xlsx")

EXCLUDED_DIAGNOSTIC_BATCHES = [
    {
        "run_id": READINESS_RUN_ID,
        "completed_result_files": 0,
        "model_requests": 0,
        "reason": "zero-model task identity, asset, dependency, public-entry, and full-verifier readiness only; excluded before the unique formal batch",
    },
    {
        "run_id": "formal-task004-local-3x3-minimaxm3-20260823-01",
        "completed_result_files": 0,
        "attempt_result_files": 1,
        "model_requests": 0,
        "reason": "controller diagnostic excluded: the first zero-request OpenCode API connection error was incorrectly classified as shared rate limiting, so the run was stopped before any formal trial completed; evidence is retained under data/raw/formal-task004-local-3x3-minimaxm3-20260823-01 and /private/tmp/vista-task004-local-3x3-20260823-01",
    },
    {
        "run_id": "formal-task004-local-3x3-minimaxm3-20260823-02",
        "completed_result_files": 0,
        "attempt_result_files": 9,
        "model_requests": 0,
        "reason": "provider diagnostic excluded: all nine serial OpenCode attempts returned the same zero-request API connection error before task verification; no alternate provider or model was used",
    },
    {
        "run_id": "formal-task004-local-3x3-minimaxm3-20260823-03-provider-diagnostic",
        "completed_result_files": 0,
        "model_requests": 0,
        "reason": "provider diagnostic excluded: sandbox-local OpenCode connectivity check returned a zero-request API connection error; no task work was attempted",
    },
    {
        "run_id": "formal-task004-local-3x3-minimaxm3-20260823-03-provider-diagnostic-escalated",
        "completed_result_files": 0,
        "model_requests": 2,
        "reason": "provider diagnostic excluded: one sandbox-external connectivity check confirmed the current configured minimax-cn/MiniMax-M3 provider is reachable; this was a readiness check, not a task trial",
    },
]

COMMON_PROMPT = """Solve the isolated task-004 powerlifting spreadsheet task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and data/data-readme.md, inspect the local workbook at ./data/openipf.xlsx, and preserve the Data sheet. The task text's /root/data/openipf.xlsx path is mapped to ./data/openipf.xlsx in this isolated workspace; save the completed workbook at that exact local path. Populate the Dots sheet with the requested columns in the required order, use Excel formulas for TotalKg and Dots, preserve three-decimal computation precision, and make sure the workbook is valid and readable by the local verifier. You may use local Python spreadsheet tools already present in the workspace, but do not fetch data or libraries. The controller runs the complete acceptance verifier after each turn."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT
    + " No task Skill is available in this condition; solve from task.md, data/data-readme.md, and the workbook only.",
    "native": COMMON_PROMPT
    + " The full original guidance modules are available at ./modules/<module-id>/SKILL.md; read and use them.",
    "pd-vista": COMMON_PROMPT
    + " Read VISTA.md. The original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose at most one relevant module under ./modules/<module-id>/SKILL.md. Read any disclosed module before the next repair turn.",
}


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(
        marker in folded
        for marker in (
            "no module named",
            "modulenotfounderror",
            "failed to import",
            "error while loading conftest",
            "unrecognized arguments",
            "unicodedecodeerror",
            "internalerror",
            "address already in use",
        )
    )


def rate_limit_reason(summary: dict[str, Any], text: str) -> str | None:
    folded = text.casefold()
    for error in summary.get("errors", []):
        if str(error.get("status_code")) == "429":
            return "http_429_rate_limit"
    if any(
        marker in folded
        for marker in (
            "http 429",
            "status code 429",
            "status_code\\\":429",
            "rate limit",
            "rate_limit",
            "quota exceeded",
            "token plan usage limit",
            "已达到 token plan 用量上限",
            "too many requests",
            "temporarily overloaded",
            "try again later",
        )
    ):
        return "rate_limit_response"
    if summary.get("requests", 0) == 0 and any(
        marker in folded
        for marker in ("quota", "overloaded", "rate limit", "throttl", "too many requests")
    ):
        return "zero_request_shared_limit"
    return None


def dependency_readiness() -> dict[str, Any]:
    """Run task4 zero-model readiness, including a full verifier probe."""

    checks: dict[str, Any] = {
        "task_identity": TASK_IDENTITY.is_file(),
        "task_identity_id": None,
        "task_identity_resolved": False,
        "task_instruction": (TASK_SOURCE / "task.md").is_file(),
        "data_readme": DATA_README.is_file(),
        "input_workbook": INPUT_WORKBOOK.is_file(),
        "original_verifier": (TASK_SOURCE / "verifier" / "test_outputs.py").is_file(),
        "ground_truth": (TASK_SOURCE / "verifier" / "cleaned_with_coefficients.xlsx").is_file(),
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "native_modules": {
            module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS
        },
        "native_module_file_counts": {
            module: len([path for path in (NATIVE_SOURCE / module).rglob("*") if path.is_file()])
            if (NATIVE_SOURCE / module).is_dir()
            else 0
            for module in MODULE_IDS
        },
        "vista_root": (VISTA_SOURCE / "SKILL.md").is_file(),
        "vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
        "vista_source": (VISTA_SOURCE / "source.yaml").is_file(),
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "public_diagnostic_cleaning": True,
        "public_verifier_entry": "copied verifier/test_outputs.py with only path aliases rewritten",
    }
    try:
        import yaml

        identity = yaml.safe_load(TASK_IDENTITY.read_text(encoding="utf-8"))
        checks["task_identity_id"] = identity.get("task_id") if isinstance(identity, dict) else None
        checks["task_identity_resolved"] = bool(
            isinstance(identity, dict)
            and identity.get("task_id") == "task-004"
            and identity.get("source", {}).get("benchmark_task_id") == "powerlifting-coef-calc"
            and identity.get("source", {}).get("definition_path")
            == "vendor/skillsbench/tasks/powerlifting-coef-calc"
        )
    except Exception as exc:
        checks["task_identity_error"] = f"{type(exc).__name__}: {exc}"

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(INPUT_WORKBOOK, data_only=False, read_only=False)
        checks["input_workbook_sheets"] = workbook.sheetnames
        checks["input_data_rows"] = workbook["Data"].max_row - 1 if "Data" in workbook.sheetnames else 0
        checks["input_data_columns"] = workbook["Data"].max_column if "Data" in workbook.sheetnames else 0
        checks["input_has_empty_dots_sheet"] = (
            "Dots" in workbook.sheetnames and workbook["Dots"].max_row == 1 and workbook["Dots"].max_column == 1
        )
    except Exception as exc:
        checks["workbook_inspection_error"] = f"{type(exc).__name__}: {exc}"

    collect_command = [
        str(VERIFIER_PYTHON),
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        str(TASK_SOURCE / "verifier" / "test_outputs.py"),
        f"--confcutdir={TASK_SOURCE / 'verifier'}",
    ]
    try:
        collected = subprocess.run(
            collect_command,
            cwd=TASK_SOURCE / "verifier",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        output = (collected.stdout or "") + (collected.stderr or "")
        checks["verifier_collect_returncode"] = collected.returncode
        checks["verifier_collect_output_tail"] = output[-2000:]
        checks["verifier_collect"] = collected.returncode == 0 and "11 tests collected" in output
    except Exception as exc:
        checks["verifier_collect"] = False
        checks["verifier_collect_error"] = f"{type(exc).__name__}: {exc}"

    config_path, requested_model, key_env = current_opencode_config()
    checks["opencode_config"] = {
        "path": str(config_path),
        "requested_provider_model": requested_model,
        "api_key_environment_variable": key_env,
        "credential_source": "environment" if key_env else "opencode_auth_store",
    }
    launcher = freeze_launcher()
    checks["opencode_launcher"] = {
        "launcher": launcher.launcher,
        "version": launcher.version,
        "binary_sha256": launcher.binary_sha256,
    }
    checks["experiment_matrix"] = {
        "conditions": list(CONDITIONS),
        "replicates": list(REPLICATES),
        "planned_trials": len(SCHEDULE),
        "unique_cells": len(set(SCHEDULE)),
        "balanced_interleaved_schedule": len(SCHEDULE) == 9 and len(set(SCHEDULE)) == 9,
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
    }
    checks["ready_before_probe"] = all(
        (
            checks["task_identity"],
            checks["task_identity_resolved"],
            checks["task_instruction"],
            checks["data_readme"],
            checks["input_workbook"],
            checks["original_verifier"],
            checks["ground_truth"],
            checks["verifier_python"],
            all(checks["native_modules"].values()),
            checks["vista_root"],
            checks["vista_manifest"],
            checks["vista_source"],
            checks.get("input_workbook_sheets") == ["Data", "Dots"],
            checks.get("input_data_rows") == 20,
            checks.get("input_data_columns") == 25,
            checks.get("input_has_empty_dots_sheet") is True,
            checks.get("verifier_collect") is True,
            checks["experiment_matrix"]["balanced_interleaved_schedule"],
        )
    )
    if checks["ready_before_probe"]:
        probe = run_full_verifier_probe()
        checks.update(probe)
    else:
        checks["full_verifier_probe"] = False
        checks["full_verifier_probe_error"] = "basic task4 readiness failed; probe was not run"
    checks["ready"] = bool(checks.get("ready_before_probe") and checks.get("full_verifier_probe"))
    return checks


def prepare_verifier(verifier: Path, workspace: Path) -> None:
    verifier_text = (TASK_SOURCE / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    replacements = {
        'OUTPUT_FILE = "/root/data/openipf.xlsx"': f"OUTPUT_FILE = {str(workspace / 'data' / 'openipf.xlsx')!r}",
        'GROUND_TRUTH_FILE = "/verifier/cleaned_with_coefficients.xlsx"': f"GROUND_TRUTH_FILE = {str(verifier / 'cleaned_with_coefficients.xlsx')!r}",
    }
    for source, target in replacements.items():
        if source not in verifier_text:
            raise RuntimeError(f"task4 verifier path anchor missing: {source}")
        verifier_text = verifier_text.replace(source, target, 1)
    (verifier / "test_outputs.py").write_text(verifier_text, encoding="utf-8")
    shutil.copy2(TASK_SOURCE / "verifier" / "cleaned_with_coefficients.xlsx", verifier / "cleaned_with_coefficients.xlsx")


def write_probe_workbook(path: Path) -> None:
    from openpyxl import load_workbook

    workbook = load_workbook(INPUT_WORKBOOK, data_only=False)
    source = workbook["Data"]
    dots = workbook["Dots"]
    headers = ["Name", "Sex", "BodyweightKg", "Best3SquatKg", "Best3BenchKg", "Best3DeadliftKg", "TotalKg", "Dots"]
    dots.delete_rows(1, dots.max_row)
    dots.append(headers)
    for row in range(2, source.max_row + 1):
        dots.append([
            source.cell(row, 1).value,
            source.cell(row, 2).value,
            source.cell(row, 9).value,
            source.cell(row, 11).value,
            source.cell(row, 12).value,
            source.cell(row, 13).value,
            f"=SUM(D{row}:F{row})",
            f'=ROUND(IF(B{row}="M",G{row},G{row}),3)',
        ])
    workbook.save(path)


def run_full_verifier_probe() -> dict[str, Any]:
    probe_root = LOCAL_ROOT / READINESS_RUN_ID
    if probe_root.exists():
        shutil.rmtree(probe_root)
    workspace = probe_root / "workspace"
    verifier = probe_root / "verifier"
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    verifier.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    shutil.copy2(DATA_README, workspace / "data" / "data-readme.md")
    write_probe_workbook(workspace / "data" / "openipf.xlsx")
    prepare_verifier(verifier, workspace)
    command = [
        str(VERIFIER_PYTHON),
        "-m",
        "pytest",
        "-q",
        str(verifier / "test_outputs.py"),
        f"--confcutdir={verifier}",
        f"--basetemp={verifier / 'pytest-temp-probe'}",
    ]
    completed = subprocess.run(
        command,
        cwd=verifier,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=VERIFIER_TIMEOUT_SECONDS,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    (probe_root / "verifier-output.txt").write_text(output, encoding="utf-8")
    return {
        "full_verifier_probe": completed.returncode == 0 and not verifier_infrastructure_failure(output),
        "full_verifier_probe_returncode": completed.returncode,
        "full_verifier_probe_output_tail": output[-3000:],
        "full_verifier_probe_id": READINESS_RUN_ID,
    }


def prepare_trial(condition: str, replicate: int, attempt: int) -> tuple[Path, Path, Path, Path]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    trial_id = f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}-attempt-{attempt:02d}"
    base = LOCAL_ROOT / trial_id
    if base.exists():
        raise RuntimeError(f"refusing to reuse task4 attempt directory: {base}")
    workspace, verifier, router_source = base / "workspace", base / "verifier", base / "router-source"
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    (workspace / "modules").mkdir(parents=True, exist_ok=True)
    verifier.mkdir(parents=True, exist_ok=True)
    router_source.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    shutil.copy2(DATA_README, workspace / "data" / "data-readme.md")
    shutil.copy2(INPUT_WORKBOOK, workspace / "data" / "openipf.xlsx")
    if condition == "native":
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, workspace / "modules" / module)
    elif condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, router_source / "modules" / module)
    prepare_verifier(verifier, workspace)
    return base, workspace, verifier, router_source


def public_diagnostic(workspace: Path, output: str, tool_errors: list[str], *, limit: int = 5000) -> tuple[list[str], str]:
    """Return safe agent-observable feedback without private ground-truth values."""

    kinds: list[str] = []
    notes: list[str] = []
    output_file = workspace / "data" / "openipf.xlsx"
    if not output_file.is_file():
        kinds.append("incomplete_output")
        notes.append("data/openipf.xlsx is missing")
    folded = output.casefold()
    if any(marker in folded for marker in ("expected columns", "columns", "sheet structure", "sheet exists")):
        kinds.extend(["schema_error", "wrong_format"])
        notes.append("the Data/Dots sheet structure or required column order is invalid")
    if "expected formula" in folded or "should contain formula" in folded or "formula should use" in folded:
        kinds.append("wrong_format")
        notes.append("TotalKg and Dots must be Excel formulas")
    if any(marker in folded for marker in ("dots mismatches", "accuracy", "ground truth", "computed=")):
        kinds.append("test_failure")
        notes.append("the public calculation check failed; inspect formulas, precision, and source columns")
    if "no male lifters" in folded or "no female lifters" in folded:
        kinds.append("test_failure")
        notes.append("the source rows or sex values were not preserved")
    if tool_errors:
        kinds.append("runtime_error")
        notes.append("a local tool call failed; use only tools available inside the isolated workspace")
    if not kinds:
        kinds.append("test_failure")
        notes.append("the public workbook verifier still fails; inspect the Dots sheet, formulas, cached values, and output path")
    if "test_failure" not in kinds:
        kinds.append("test_failure")
    kinds = list(dict.fromkeys(kinds))
    safe = "; ".join(dict.fromkeys(notes))
    return kinds, safe[:limit]


def run_verifier(verifier: Path, turn: int) -> tuple[bool, str, bool, bool]:
    command = [
        str(VERIFIER_PYTHON),
        "-m",
        "pytest",
        "-q",
        str(verifier / "test_outputs.py"),
        f"--confcutdir={verifier}",
        f"--basetemp={verifier / f'pytest-temp-turn-{turn}'}",
    ]
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=verifier,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERIFIER_TIMEOUT_SECONDS,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = "Verifier timeout after 300 seconds.\n" + str(exc)
        completed = subprocess.CompletedProcess(command, 124, stdout=output, stderr="")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = timed_out or verifier_infrastructure_failure(output)
    return completed.returncode == 0 and not infra, output, infra, timed_out


def _timeout_completed(command: list[str], exc: subprocess.TimeoutExpired) -> subprocess.CompletedProcess[str]:
    def decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    return subprocess.CompletedProcess(command, 124, stdout=decode(exc.stdout), stderr=decode(exc.stderr))


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    source = router_source / "modules" / module_id
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(source, destination)
    return destination / "SKILL.md"


def run_replicate(condition: str, replicate: int, attempt: int, *, config_path: Path, requested_model: str) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate, attempt)
    parser = FeedbackParser()
    router = DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml")) if condition == "pd-vista" else None
    disclosed: list[str] = []
    disclosure_events: list[dict[str, Any]] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    final_pass = False
    stop_reason = "max_turns"
    rate_limited = False
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    process_env = os.environ.copy()
    link_opencode_auth_store(base)
    process_env["OPENCODE_CONFIG"] = str(config_path)
    process_env.pop("OPENCODE_CONFIG_DIR", None)
    process_env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    process_env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    process_env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")

    for turn in range(1, MAX_TURNS + 1):
        command = __import__("vista.runtime.opencode_local_backend", fromlist=["command_for_turn"]).command_for_turn(
            workspace=workspace, prompt=prompt, session_id=session_id, model=requested_model
        )
        model_timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=process_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MODEL_TURN_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            model_timed_out = True
            completed = _timeout_completed(command, exc)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        (base / f"turn-{turn}.jsonl").write_text(stdout, encoding="utf-8")
        (base / f"turn-{turn}.stderr.txt").write_text(stderr, encoding="utf-8")
        summary = event_summary(parse_events(stdout), MODULE_IDS)
        if summary["session_ids"]:
            observed = summary["session_ids"][0]
            if session_id is not None and observed != session_id:
                stop_reason = "session_identity_changed"
                break
            session_id = observed
        accessed_now = [module for module in summary["accessed_modules"] if module in disclosed]
        for disclosure in disclosure_events:
            if disclosure["next_round_turn"] == turn and not disclosure["accessed_next_round"] and disclosure["module_id"] in accessed_now:
                disclosure["accessed_next_round"] = True
                access_rounds += 1

        limit_reason = rate_limit_reason(summary, stdout + "\n" + stderr)
        if limit_reason:
            rate_limited = True
            stop_reason = limit_reason
            (verifier / f"verifier-turn-{turn}.txt").write_text(
                f"Excluded before task verification: shared provider rate limit ({limit_reason}).\n", encoding="utf-8"
            )
            turn_record = {
                "turn": turn,
                "prompt": prompt,
                "return_code": completed.returncode,
                "model_turn_timeout": model_timed_out,
                "event_summary": summary,
                "accessed_modules_this_turn": accessed_now,
                "verifier_pass": False,
                "verifier_infrastructure_error": False,
                "verifier_timeout": False,
                "rate_limit_excluded": True,
                "rate_limit_reason": limit_reason,
                "public_feedback_types": [],
                "public_diagnostic": "",
                "newly_disclosed_modules": [],
            }
            turns.append(turn_record)
            print(f"condition={condition} replicate={replicate} attempt={attempt} turn={turn} requests={summary['requests']} tools={summary['tool_uses']} rate_limit={limit_reason}", flush=True)
            break

        verifier_pass, verifier_output, verifier_infra, verifier_timed_out = run_verifier(verifier, turn)
        turn_record: dict[str, Any] = {
            "turn": turn,
            "prompt": prompt,
            "return_code": completed.returncode,
            "model_turn_timeout": model_timed_out,
            "event_summary": summary,
            "accessed_modules_this_turn": accessed_now,
            "verifier_pass": verifier_pass,
            "verifier_infrastructure_error": verifier_infra,
            "verifier_timeout": verifier_timed_out,
            "public_feedback_types": [],
            "public_diagnostic": "",
            "newly_disclosed_modules": [],
        }
        turns.append(turn_record)
        print(
            f"condition={condition} replicate={replicate} attempt={attempt} turn={turn} requests={summary['requests']} tools={summary['tool_uses']} accessed={accessed_now} verifier_pass={verifier_pass} model_timeout={model_timed_out} verifier_timeout={verifier_timed_out}",
            flush=True,
        )
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
        feedback_types, diagnostic = public_diagnostic(workspace, verifier_output, summary["tool_errors"])
        newly_disclosed: list[str] = []
        route_reason = None
        if condition == "pd-vista":
            assert router is not None
            payload = json.dumps({
                "schema_version": 1,
                "kind": "vista_public_verifier_feedback",
                "outcome": "fail",
                "feedback_types": feedback_types,
                "summary": diagnostic,
            })
            classification = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}",
            )
            decision = router.route(classification, previous_modules=disclosed)
            route_reason = decision.reason
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
                disclosure_events.append({
                    "module_id": module.module_id,
                    "disclosure_turn": turn,
                    "next_round_turn": turn + 1,
                    "accessed_next_round": False,
                    "route_reason": route_reason,
                    "feedback_types": feedback_types,
                    "public_diagnostic": diagnostic,
                })
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(f"./modules/{module}/SKILL.md" for module in newly_disclosed) + ". Read it before repairing."
        elif condition == "native":
            instruction = "Use the original modules already available under ./modules/."
        elif disclosed:
            instruction = "Use the already disclosed module(s): " + ", ".join(disclosed) + "."
        else:
            instruction = "No task Skill is available; repair from the public diagnostic and local workbook."
        prompt = (
            f"The public workbook verifier failed with {', '.join(feedback_types)}. Public diagnostic: {diagnostic}\n"
            f"{instruction} Continue in the same session, work only inside this directory, repair ./data/openipf.xlsx, and rerun local checks when useful."
        )
        turn_record["public_feedback_types"] = feedback_types
        turn_record["public_diagnostic"] = diagnostic
        turn_record["newly_disclosed_modules"] = newly_disclosed
        turn_record["route_reason"] = route_reason
        turn_record["next_round_prompt"] = prompt

    is_quarantined = rate_limited or stop_reason in {
        "provider_policy_rejection",
        "api_error",
        "session_identity_changed",
        "verifier_infrastructure_error",
        "model_identity_changed",
    }
    status = (
        "quarantined_rate_limit"
        if rate_limited
        else "passed_with_clean_vista_feedback_loop"
        if final_pass and condition == "pd-vista" and disclosure_count > 0 and all(item["accessed_next_round"] for item in disclosure_events)
        else "passed"
        if final_pass and condition != "pd-vista"
        else "passed_without_clean_vista_feedback_loop"
        if final_pass
        else f"quarantined_{stop_reason}"
        if is_quarantined
        else "failed_max_turns"
    )
    actual_models = list(dict.fromkeys(model for item in turns for model in item["event_summary"]["actual_model_ids"]))
    actual_providers = list(dict.fromkeys(provider for item in turns for provider in item["event_summary"]["provider_ids"]))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model}:
        final_pass, stop_reason, status = False, "model_identity_changed", "quarantined_model_identity_changed"
    if actual_providers and set(actual_providers) != {expected_provider}:
        final_pass, stop_reason, status = False, "model_identity_changed", "quarantined_model_identity_changed"
    usage = {
        key: sum(int(item["event_summary"].get(key) or 0) for item in turns)
        for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
    }
    clean_closure = int(
        condition == "pd-vista"
        and final_pass
        and disclosure_count > 0
        and all(item["accessed_next_round"] for item in disclosure_events)
    )
    excluded_dir_name = "_excluded_rate_limit_attempts" if rate_limited else "_excluded_attempts"
    result_dir = (
        RESULT_ROOT / condition / f"replicate-{replicate:02d}"
        if not is_quarantined
        else RESULT_ROOT / excluded_dir_name / condition / f"replicate-{replicate:02d}-attempt-{attempt:02d}"
    )
    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    output_file = workspace / "data" / "openipf.xlsx"
    if output_file.is_file():
        shutil.copy2(output_file, result_dir / "openipf.xlsx")
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-004",
        "benchmark_task_id": "powerlifting-coef-calc",
        "condition": condition,
        "replicate_id": replicate,
        "attempt": attempt,
        "backend": "opencode-local-minimal-dependencies",
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "requested_provider_model": requested_model,
        "expected_provider": expected_provider,
        "expected_model": expected_model,
        "actual_provider_ids": actual_providers,
        "actual_model_ids": actual_models,
        "session_id": session_id,
        "turns": turns,
        "usage": usage,
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "disclosed_modules": disclosed,
        "accessed_modules_after_disclosure": [item["module_id"] for item in disclosure_events if item["accessed_next_round"]],
        "disclosure_events": disclosure_events,
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities,
            "routable_feedback": routable_feedback,
            "successful_disclosures": disclosure_count,
            "next_round_module_access": access_rounds,
            "clean_complete_closure": clean_closure,
        },
        "rate_limit_excluded": rate_limited,
        "rate_limit_reason": stop_reason if rate_limited else None,
        "final_verifier_pass": final_pass,
        "stop_reason": stop_reason,
        "status": status,
        "workspace": str(workspace),
        "result_directory": str(result_dir),
        "final_output_files": ["openipf.xlsx"] if output_file.is_file() else [],
    }
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def controller_error_result(condition: str, replicate: int, requested_model: str, error: Exception) -> dict[str, Any]:
    result_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-004",
        "benchmark_task_id": "powerlifting-coef-calc",
        "condition": condition,
        "replicate_id": replicate,
        "attempt": None,
        "backend": "opencode-local-minimal-dependencies",
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "requested_provider_model": requested_model,
        "actual_provider_ids": [],
        "actual_model_ids": [],
        "session_id": None,
        "turns": [],
        "usage": {key: 0 for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")},
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "disclosed_modules": [],
        "accessed_modules_after_disclosure": [],
        "disclosure_events": [],
        "vista_metrics": {key: 0 for key in ("trigger_opportunities", "routable_feedback", "successful_disclosures", "next_round_module_access", "clean_complete_closure")},
        "final_verifier_pass": False,
        "stop_reason": "controller_error",
        "status": "quarantined_controller_error",
        "controller_error": f"{type(error).__name__}: {error}",
        "workspace": "",
        "result_directory": str(result_dir),
        "final_output_files": [],
    }
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def db_usage(workspace: str) -> dict[str, Any]:
    db = Path(workspace).parent / "opencode-state" / "data" / "opencode" / "opencode.db"
    evidence: dict[str, Any] = {
        "db_path": str(db),
        "exists": db.is_file(),
        "read_only": True,
        "provider_ids": [],
        "model_ids": [],
        "recorded_cost": 0.0,
        "assistant_messages": 0,
        "session_rows": 0,
        "message_rows": 0,
    }
    if not db.is_file():
        return evidence
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (raw_model,) in conn.execute("select model from session"):
            evidence["session_rows"] += 1
            try:
                value = json.loads(raw_model) if raw_model else {}
            except (TypeError, json.JSONDecodeError):
                value = {}
            if isinstance(value, dict):
                if value.get("providerID"):
                    evidence["provider_ids"].append(str(value["providerID"]))
                if value.get("id"):
                    evidence["model_ids"].append(str(value["id"]))
        for (raw,) in conn.execute("select data from message"):
            evidence["message_rows"] += 1
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


def load_latest_result(condition: str, replicate: int) -> dict[str, Any] | None:
    path = RESULT_ROOT / condition / f"replicate-{replicate:02d}" / "result.json"
    return _json_load(path) if path.is_file() else None


def scan_excluded_attempts() -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for root in (RESULT_ROOT / "_excluded_attempts", RESULT_ROOT / "_excluded_rate_limit_attempts"):
        for path in sorted(root.glob("**/result.json")) if root.is_dir() else []:
            try:
                value = _json_load(path)
            except (OSError, json.JSONDecodeError):
                continue
            item = {
                "condition": value.get("condition"),
                "replicate_id": value.get("replicate_id"),
                "attempt": value.get("attempt"),
                "status": value.get("status"),
                "stop_reason": value.get("stop_reason"),
                "session_id": value.get("session_id"),
                "usage": value.get("usage", {}),
                "result_directory": value.get("result_directory"),
                "attempt_result": str(path),
                "reason": "shared rate-limit attempt excluded" if value.get("rate_limit_excluded") else "provider/controller/session attempt excluded",
            }
            if (item["condition"], item["replicate_id"], item["attempt"]) == ("noskill", 3, 11):
                item["classification"] = "controller_false_positive_rate_limit"
                item["reason"] = "controller false positive: bare 429 substring occurred inside OpenCode cost 0.00342978; raw attempt retained and manually re-verified"
            attempts.append(item)
    return attempts


def write_posthoc_audit(results: list[dict[str, Any]], requested_model: str, rate_limit_attempts: list[dict[str, Any]]) -> dict[str, Any]:
    per_trial: list[dict[str, Any]] = []
    for result in results:
        record = dict(result)
        record["opencode_db"] = db_usage(result.get("workspace", "")) if result.get("workspace") else {"exists": False, "read_only": True, "provider_ids": [], "model_ids": [], "recorded_cost": 0.0}
        record["output_file_count"] = len(result.get("final_output_files", []))
        record["included_in_formal_stats"] = not str(result.get("status", "")).startswith("quarantined_")
        if not record["included_in_formal_stats"]:
            record["formal_exclusion_reason"] = result.get("stop_reason", result.get("status"))
        per_trial.append(record)
    expected_provider, expected_model = requested_model.split("/", 1)
    formal_trials = [item for item in per_trial if item["included_in_formal_stats"]]

    def totals(items: list[dict[str, Any]]) -> dict[str, Any]:
        value = {key: sum(int(item.get("usage", {}).get(key) or 0) for item in items) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
        value["input_plus_output_tokens"] = value["input_tokens"] + value["output_tokens"]
        value["recorded_cost"] = round(sum(float(item.get("opencode_db", {}).get("recorded_cost") or 0) for item in items), 8)
        return value

    conditions: dict[str, Any] = {}
    for condition in CONDITIONS:
        values = [item for item in formal_trials if item["condition"] == condition]
        all_values = [item for item in per_trial if item["condition"] == condition]
        conditions[condition] = {
            "planned_trials": 3,
            "result_files": len(all_values),
            "included_trials": len(values),
            "excluded_trials": len(all_values) - len(values),
            "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in values),
            "total_turns": sum(len(item.get("turns", [])) for item in values),
            "model_turn_timeouts": sum(sum(bool(turn.get("model_turn_timeout")) for turn in item.get("turns", [])) for item in values),
            "verifier_timeouts": sum(sum(bool(turn.get("verifier_timeout")) for turn in item.get("turns", [])) for item in values),
            "unique_sessions": len({item.get("session_id") for item in values if item.get("session_id")}),
            "formal_totals": totals(values),
            "all_attempt_totals": totals(all_values),
        }
        if condition == "pd-vista":
            conditions[condition].update({
                key: sum(int(item.get("vista_metrics", {}).get(key) or 0) for item in values)
                for key in ("trigger_opportunities", "routable_feedback", "successful_disclosures", "next_round_module_access", "clean_complete_closure")
            })
            conditions[condition]["disclosed_modules"] = sorted({module for item in values for module in item.get("disclosed_modules", [])})
    excluded_attempts = scan_excluded_attempts()
    excluded_rate_limits = [item for item in excluded_attempts if str(item.get("status", "")) == "quarantined_rate_limit"]
    excluded_api_errors = [item for item in excluded_attempts if item not in excluded_rate_limits]
    audit = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "task_id": "task-004",
        "benchmark_task_id": "powerlifting-coef-calc",
        "audit_status": "passed" if len(per_trial) == 9 and all(item["final_verifier_pass"] for item in per_trial) else "completed_with_failures",
        "completed_trials": len(per_trial),
        "planned_trials": 9,
        "unique_sessions": len({item.get("session_id") for item in per_trial if item.get("session_id")}),
        "session_ids_complete": all(item.get("session_id") for item in per_trial),
        "all_final_verifiers_passed": len(per_trial) == 9 and all(item["final_verifier_pass"] for item in per_trial),
        "nine_result_files": len(per_trial) == 9,
        "all_final_output_files_present": len(per_trial) == 9 and all(item["output_file_count"] > 0 for item in per_trial),
        "formal_included_trials": len(formal_trials),
        "formal_all_final_verifiers_passed": bool(formal_trials) and all(item["final_verifier_pass"] for item in formal_trials),
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "model_identity": {
            "requested_provider_id": expected_provider,
            "requested_model_id": expected_model,
            "consistent_across_events": all((not item["actual_provider_ids"] and not item["actual_model_ids"]) or (item["actual_provider_ids"] == [expected_provider] and item["actual_model_ids"] == [expected_model]) for item in per_trial),
            "consistent_across_isolated_databases": all(item["opencode_db"].get("provider_ids") == [expected_provider] and item["opencode_db"].get("model_ids") == [expected_model] for item in formal_trials),
            "database_identity_complete_for_formal_trials": all(item["opencode_db"].get("exists") and item["opencode_db"].get("provider_ids") == [expected_provider] and item["opencode_db"].get("model_ids") == [expected_model] for item in formal_trials),
            "evidence": "read-only query of each isolated OpenCode opencode.db session and message tables",
        },
        "conditions": conditions,
        "totals": {"formal_included": totals(formal_trials), "all_observed_result_files": totals(per_trial)},
        "trials": per_trial,
        "excluded_runs": EXCLUDED_DIAGNOSTIC_BATCHES,
        "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
        "excluded_attempts": excluded_attempts,
        "excluded_rate_limit_attempts": rate_limit_attempts or excluded_rate_limits,
        "excluded_api_error_attempts": excluded_api_errors,
    }
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    if RESULT_ROOT.exists() or AUDIT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse task4 formal output directory: {RESULT_ROOT} or {AUDIT_ROOT}")
    readiness_path = READINESS_AUDIT_ROOT / "readiness.json"
    if readiness_path.is_file():
        readiness = _json_load(readiness_path)
    else:
        readiness = dependency_readiness()
        READINESS_AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
        readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not readiness["ready"]:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 2

    launcher = freeze_launcher()
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    rate_limit_attempts: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        latest: dict[str, Any] | None = None
        for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 2):
            try:
                latest = run_replicate(condition, replicate, attempt, config_path=config_path, requested_model=requested_model)
            except Exception as exc:
                latest = controller_error_result(condition, replicate, requested_model, exc)
                print(f"condition={condition} replicate={replicate} controller_error={type(exc).__name__}: {exc}", flush=True)
            if latest.get("status") == "quarantined_rate_limit":
                rate_limit_attempts.append({
                    "condition": condition,
                    "replicate_id": replicate,
                    "attempt": attempt,
                    "status": latest.get("status"),
                    "stop_reason": latest.get("stop_reason"),
                    "session_id": latest.get("session_id"),
                    "result_directory": latest.get("result_directory"),
                    "reason": "shared provider 429/zero-request limit; excluded from task failure statistics and retried serially with a fresh isolated OpenCode state and session",
                })
                if attempt <= MAX_RATE_LIMIT_RETRIES:
                    continue
            break

    all_results: list[dict[str, Any]] = []
    for replicate in REPLICATES:
        for condition in CONDITIONS:
            latest = load_latest_result(condition, replicate)
            if latest is not None:
                all_results.append(latest)
    condition_summaries: dict[str, Any] = {}
    for condition in CONDITIONS:
        values = [item for item in all_results if item["condition"] == condition]
        formal_values = [item for item in values if not str(item.get("status", "")).startswith("quarantined_")]
        condition_summaries[condition] = {
            "planned_trials": 3,
            "result_files": len(values),
            "formal_included_trials": len(formal_values),
            "excluded_trials": len(values) - len(formal_values),
            "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in formal_values),
            "usage": {key: sum(int(item.get("usage", {}).get(key) or 0) for item in formal_values) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")},
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
        "completed_trials": len(all_results),
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
        "requested_provider_model": requested_model,
        "opencode": {"config_path": str(config_path), "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256},
        "timeouts": {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS},
        "condition_summaries": condition_summaries,
        "results": [{key: item.get(key) for key in ("condition", "replicate_id", "attempt", "status", "stop_reason", "final_verifier_pass", "session_id", "actual_model_ids", "actual_provider_ids", "usage", "initially_available_modules", "disclosed_modules", "accessed_modules_after_disclosure", "disclosure_events", "vista_metrics", "final_output_files")} for item in all_results],
        "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
        "excluded_rate_limit_attempts": rate_limit_attempts,
        "excluded_attempts": scan_excluded_attempts(),
        "docker_used": False,
        "skillbench_execution_route_used": False,
    }
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = write_posthoc_audit(all_results, requested_model, rate_limit_attempts)
    print(json.dumps({"run_id": RUN_PREFIX, "formal_results": len(all_results), "rate_limit_attempts": len(rate_limit_attempts), "audit_status": audit["audit_status"]}, ensure_ascii=False))
    return 0 if len(all_results) == 9 and audit["all_final_verifiers_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
