"""Run task-003 (data-to-d3) locally with OpenCode in three conditions.

This controller is deliberately local-only. It materializes independent task,
verifier, workspace, and OpenCode state directories for every trial. It does
not invoke Docker, BenchFlow, or the SkillsBench execution platform.
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
TASK_IDENTITY = REPO / "tasks" / "pilot" / "task-003" / "task.yaml"
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "data-to-d3"
ENVIRONMENT = TASK_SOURCE / "environment"
DATA_SOURCE = ENVIRONMENT / "data"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-003"
RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX", "formal-task003-local-3x3-minimaxm3-20260823-02"
)
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task003-local-opencode-minimaxm3-public-diagnostics-resumable-timeout-noskill-native-pdvista-3x3",
)
if os.environ.get("VISTA_EXPERIMENT_ROOT"):
    LOCAL_ROOT = Path(os.environ["VISTA_EXPERIMENT_ROOT"]).expanduser().resolve()
else:
    LOCAL_ROOT = Path.home() / "Library" / "Caches" / "VistaExperiments"
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
AUDIT_ROOT = REPO / "outputs" / f"{RUN_PREFIX}-audit"
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv" / "bin" / "python")
).expanduser().absolute()
MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
VERIFIER_TIMEOUT_SECONDS = 300
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
FULL_SCHEDULE = SCHEDULE
_selected_trials = os.environ.get("VISTA_SELECTED_TRIALS", "").strip()
RESUME_MODE = bool(_selected_trials)
if _selected_trials:
    _requested_trials = {
        tuple(item.strip().rsplit(":", 1))
        for item in _selected_trials.split(",")
        if item.strip()
    }
    if not _requested_trials:
        raise RuntimeError("VISTA_SELECTED_TRIALS must not be empty")
    SCHEDULE = tuple(
        (replicate, condition)
        for replicate, condition in SCHEDULE
        if (condition, str(replicate)) in _requested_trials
    )
    if len(SCHEDULE) != len(_requested_trials):
        raise RuntimeError("VISTA_SELECTED_TRIALS must name unique condition:replicate cells")
ATTEMPT_ID = os.environ.get("VISTA_ATTEMPT_ID", "02" if RESUME_MODE else "01")
TRIAL_PREFIX = (
    f"{RUN_PREFIX}-resume-{ATTEMPT_ID}" if RESUME_MODE else RUN_PREFIX
)
MODULE_IDS = ("d3-visualization",)

# This is a zero-model readiness batch. It is kept in every audit and excluded
# from all formal comparison totals.
EXCLUDED_DIAGNOSTIC_BATCHES = [
    {
        "run_id": "formal-task003-local-3x3-minimaxm3-20260823-00-readiness",
        "completed_result_files": 0,
        "model_requests": 0,
        "reason": "zero-model task/material/verifier/browser readiness only; excluded before the unique formal batch",
    },
    {
        "run_id": "formal-task003-local-3x3-minimaxm3-20260823-01",
        "completed_result_files": 0,
        "model_requests": 193,
        "reason": "controller diagnostic excluded: copied verifier used a fixed port 8765 and returned Address already in use; batch stopped before completing 9 result files",
    },
    {
        "run_id": "formal-task003-local-3x3-minimaxm3-20260823-02-resume-readiness-check-01",
        "completed_result_files": 0,
        "model_requests": 0,
        "reason": "controller diagnostic excluded: an early resume-only readiness check rejected the filtered schedule before any OpenCode/API request; no formal trial was started",
    },
]

COMMON_PROMPT = """Solve the isolated data-to-D3 task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inspect the local data under ./data. Produce the requested browser-openable single-page web app under ./output/index.html with the exact supporting paths requested by task.md. Use only local inputs and deterministic D3.js v6 assets; do not fetch external data or libraries. You may run local browser checks when useful. The controller runs the complete acceptance verifier after each turn."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT
    + " No task Skill is available in this condition; solve from task.md and the local data only.",
    "native": COMMON_PROMPT
    + " The full original guidance module is available at ./modules/d3-visualization/SKILL.md; read and use it.",
    "pd-vista": COMMON_PROMPT
    + " Read VISTA.md. The original guidance module is initially hidden; after a public verifier or browser-output failure, VISTA may disclose one relevant module at ./modules/d3-visualization/SKILL.md.",
}


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    # Match only verifier/runner infrastructure signatures. Ordinary test
    # failures commonly contain words such as "error_msg" or "Page errors";
    # those must remain public diagnostics so the same session can repair.
    markers = (
        "no module named",
        "modulenotfounderror",
        "failed to import",
        "error while loading conftest",
        "unrecognized arguments",
        "unicodedecodeerror",
        "internalerror",
        "targetclosederror",
        "browsertype.launch",
        "playwright._impl",
        "address already in use",
        "traceback (most recent call last)",
    )
    return any(marker in folded for marker in markers)


def dependency_readiness() -> dict[str, Any]:
    """Zero-model readiness for task identity, assets, browser and verifier."""

    checks: dict[str, Any] = {
        "task_identity": TASK_IDENTITY.is_file(),
        "task_identity_id": None,
        "task_instruction": (TASK_SOURCE / "task.md").is_file(),
        "data_directory": DATA_SOURCE.is_dir(),
        "stock_description": (DATA_SOURCE / "stock-descriptions.csv").is_file(),
        "stock_file_count": len(list((DATA_SOURCE / "indiv-stock").glob("*.csv"))),
        "original_verifier": (TASK_SOURCE / "verifier" / "test_outputs.py").is_file(),
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "native_modules": {
            module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS
        },
        "vista_root": (VISTA_SOURCE / "SKILL.md").is_file(),
        "vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
        "vista_source": (VISTA_SOURCE / "source.yaml").is_file(),
        "public_diagnostic_cleaning": True,
        "public_browser_entry": "local HTTP server in copied verifier fixture",
        "docker_used": False,
        "skillbench_execution_route_used": False,
    }
    try:
        task_raw = json.loads("{}")
        import yaml

        task_raw = yaml.safe_load(TASK_IDENTITY.read_text(encoding="utf-8"))
        checks["task_identity_id"] = task_raw.get("task_id") if isinstance(task_raw, dict) else None
        checks["task_identity_resolved"] = (
            checks["task_identity_id"] == "task-003"
            and isinstance(task_raw, dict)
            and task_raw.get("source", {}).get("benchmark_task_id") == "data-to-d3"
        )
    except Exception as exc:
        checks["task_identity_resolved"] = False
        checks["task_identity_error"] = str(exc)

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
        collect_output = (collected.stdout or "") + (collected.stderr or "")
        checks["verifier_collect_returncode"] = collected.returncode
        checks["verifier_collect_output_tail"] = collect_output[-2000:]
        checks["verifier_collect"] = (
            collected.returncode == 0
            and "15 tests collected" in collect_output
            and sum(line.startswith("def test_") for line in (TASK_SOURCE / "verifier" / "test_outputs.py").read_text(encoding="utf-8").splitlines()) == 10
        )
    except Exception as exc:
        checks["verifier_collect"] = False
        checks["verifier_collect_error"] = str(exc)

    browser_probe = [
        str(VERIFIER_PYTHON),
        "-c",
        "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); page=b.new_page(); page.set_content('<h1>ready</h1>'); assert page.inner_text('h1') == 'ready'; b.close(); p.stop()",
    ]
    try:
        browser = subprocess.run(
            browser_probe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        checks["browser_smoke"] = browser.returncode == 0
        checks["browser_smoke_output"] = ((browser.stdout or "") + (browser.stderr or ""))[-1000:]
    except Exception as exc:
        checks["browser_smoke"] = False
        checks["browser_smoke_error"] = str(exc)

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
        "planned_trials": len(FULL_SCHEDULE),
        "unique_cells": len(set(FULL_SCHEDULE)),
        "balanced_interleaved_schedule": len(FULL_SCHEDULE) == 9 and len(set(FULL_SCHEDULE)) == 9,
        "selected_resume_trials": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
    }
    checks["ready"] = all(
        (
            checks.get("task_identity") is True,
            checks.get("task_identity_resolved") is True,
            checks.get("task_instruction") is True,
            checks.get("data_directory") is True,
            checks.get("stock_description") is True,
            checks.get("stock_file_count") == 50,
            checks.get("original_verifier") is True,
            checks.get("verifier_python") is True,
            all(checks.get("native_modules", {}).values()),
            checks.get("vista_root") is True,
            checks.get("vista_manifest") is True,
            checks.get("vista_source") is True,
            checks.get("verifier_collect") is True,
            checks.get("browser_smoke") is True,
            checks.get("experiment_matrix", {}).get("balanced_interleaved_schedule") is True,
        )
    )
    return checks


def prepare_trial(condition: str, replicate: int) -> tuple[Path, Path, Path, Path]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    base = LOCAL_ROOT / f"{TRIAL_PREFIX}-{condition}-replicate-{replicate:02d}"
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace, verifier, router_source = base / "workspace", base / "verifier", base / "router-source"
    workspace.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(exist_ok=True)
    router_source.mkdir(exist_ok=True)
    (workspace / "modules").mkdir(exist_ok=True)
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    shutil.copytree(DATA_SOURCE, workspace / "data")
    (workspace / "output").mkdir(exist_ok=True)

    if condition == "native":
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, workspace / "modules" / module)
    elif condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module, router_source / "modules" / module)

    verifier_text = (TASK_SOURCE / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    verifier_text = verifier_text.replace(
        'OUTPUT_DIR = "/root/output"', f"OUTPUT_DIR = {str(workspace / 'output')!r}"
    )
    verifier_text = verifier_text.replace("/logs/verifier", str(verifier / "logs"))
    # The benchmark fixture hard-codes 8765. Local trials run sequentially but
    # macOS can retain the old socket briefly, so each verifier process gets a
    # fresh ephemeral port without changing the verifier assertions.
    verifier_text = verifier_text.replace("PORT = 8765", "PORT = 0")
    verifier_text = verifier_text.replace(
        'httpd = socketserver.TCPServer(("", PORT), Handler)',
        'httpd = socketserver.TCPServer(("", PORT), Handler)\n    PORT = httpd.server_address[1]',
    )
    (verifier / "test_outputs.py").write_text(verifier_text, encoding="utf-8")
    return base, workspace, verifier, router_source


def public_diagnostic(output: str, *, limit: int = 6000) -> str:
    """Return only agent-observable, stable failure lines with local paths cleaned."""

    selected: list[str] = []
    markers = (
        "failed",
        "failure",
        "error",
        "assert",
        "missing",
        "not found",
        "expected",
        "must",
        "tooltip",
        "bubble",
        "table",
        "legend",
        "sector",
        "cluster",
        "ticker",
        "market cap",
        "data",
        "passed",
        "timeout",
    )
    for raw in output.splitlines():
        line = raw.strip()
        if not line or not any(marker in line.casefold() for marker in markers):
            continue
        line = re.sub(r"/(?:Users|private|var|tmp)/[^\s:]+", "<local-path>", line)
        if line not in selected:
            selected.append(line)
    return ("\n".join(selected) or "The local browser verifier failed; inspect the requested output structure and rendering.")[:limit]


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


def run_replicate(
    condition: str, replicate: int, *, config_path: Path, requested_model: str
) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate)
    parser = FeedbackParser()
    router = DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml")) if condition == "pd-vista" else None
    disclosed: list[str] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    disclosure_events: list[dict[str, Any]] = []
    final_pass = False
    stop_reason = "max_turns"
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    process_env = os.environ.copy()
    link_opencode_auth_store(base)
    process_env["OPENCODE_CONFIG"] = str(config_path)
    process_env.pop("OPENCODE_CONFIG_DIR", None)
    process_env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    process_env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    process_env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")

    for turn in range(1, MAX_TURNS + 1):
        command = command_for_turn(
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
        (base / f"turn-{turn}.jsonl").write_text(completed.stdout or "", encoding="utf-8")
        (base / f"turn-{turn}.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
        summary = event_summary(parse_events(completed.stdout or ""), MODULE_IDS)
        observed_sessions = summary["session_ids"]
        if observed_sessions:
            observed = observed_sessions[0]
            if session_id is not None and observed != session_id:
                stop_reason = "session_identity_changed"
                break
            session_id = observed
        accessed_now = [module for module in summary["accessed_modules"] if module in disclosed]
        for disclosure in disclosure_events:
            if disclosure["next_round_turn"] == turn and not disclosure["accessed_next_round"]:
                if disclosure["module_id"] in accessed_now:
                    disclosure["accessed_next_round"] = True
                    access_rounds += 1

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
            f"condition={condition} replicate={replicate} turn={turn} requests={summary['requests']} "
            f"tools={summary['tool_uses']} accessed={accessed_now} verifier_pass={verifier_pass} "
            f"model_timeout={model_timed_out} verifier_timeout={verifier_timed_out}",
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
        diagnostic = public_diagnostic(verifier_output)
        classification = parser.parse(
            FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=diagnostic, exit_code=1),
            trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}-public",
        )
        feedback_types = list(classification.trigger_names)
        if not feedback_types or feedback_types == ["unknown"]:
            feedback_types = ["command_failure"]
        newly_disclosed: list[str] = []
        route_reason = None
        if condition == "pd-vista":
            assert router is not None
            payload = json.dumps(
                {
                    "schema_version": 1,
                    "kind": "vista_public_verifier_feedback",
                    "outcome": "fail",
                    "feedback_types": feedback_types,
                    "summary": diagnostic,
                }
            )
            routed = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}",
            )
            decision = router.route(routed, previous_modules=disclosed)
            route_reason = decision.reason
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
                disclosure_events.append(
                    {
                        "module_id": module.module_id,
                        "disclosure_turn": turn,
                        "next_round_turn": turn + 1,
                        "accessed_next_round": False,
                        "route_reason": route_reason,
                        "feedback_types": feedback_types,
                        "public_diagnostic": diagnostic,
                    }
                )
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(
                f"./modules/{module}/SKILL.md" for module in newly_disclosed
            ) + ". Read it before repairing."
        elif condition == "native":
            instruction = "Use the original module already available under ./modules/."
        elif disclosed:
            instruction = "Use the already disclosed module: " + ", ".join(disclosed) + "."
        else:
            instruction = "No task Skill is available; repair from the public diagnostic and local data."
        prompt = (
            f"The public browser verifier failed with {', '.join(feedback_types)}. Public diagnostic:\n"
            f"{diagnostic}\n{instruction} Continue in the same session, work only inside this directory, "
            "repair the output under ./output, and use local browser checks when useful."
        )
        turn_record["public_feedback_types"] = feedback_types
        turn_record["public_diagnostic"] = diagnostic
        turn_record["newly_disclosed_modules"] = newly_disclosed
        turn_record["route_reason"] = route_reason
        turn_record["next_round_prompt"] = prompt

    logical_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir = (
        logical_dir / "attempts" / f"attempt-{ATTEMPT_ID}"
        if RESUME_MODE
        else logical_dir
    )
    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    if (verifier / "logs").is_dir():
        shutil.copytree(verifier / "logs", result_dir / "verifier-logs", dirs_exist_ok=True)
    if (workspace / "output").is_dir():
        shutil.copytree(workspace / "output", result_dir / "output", dirs_exist_ok=True)

    actual_models = list(dict.fromkeys(model for item in turns for model in item["event_summary"]["actual_model_ids"]))
    actual_providers = list(dict.fromkeys(provider for item in turns for provider in item["event_summary"]["provider_ids"]))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model}:
        final_pass, stop_reason = False, "model_identity_changed"
    if actual_providers and set(actual_providers) != {expected_provider}:
        final_pass, stop_reason = False, "model_identity_changed"
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
    quarantined = stop_reason in {
        "provider_policy_rejection",
        "api_error",
        "session_identity_changed",
        "verifier_infrastructure_error",
        "model_identity_changed",
    }
    status = (
        "passed_with_clean_vista_feedback_loop"
        if clean_closure
        else "passed"
        if final_pass and condition != "pd-vista"
        else "passed_without_clean_vista_feedback_loop"
        if final_pass
        else f"quarantined_{stop_reason}"
        if quarantined
        else "failed_max_turns"
    )
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-003",
        "benchmark_task_id": "data-to-d3",
        "condition": condition,
        "replicate_id": replicate,
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
        "accessed_modules_after_disclosure": [
            item["module_id"] for item in disclosure_events if item["accessed_next_round"]
        ],
        "disclosure_events": disclosure_events,
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities,
            "routable_feedback": routable_feedback,
            "successful_disclosures": disclosure_count,
            "next_round_module_access": access_rounds,
            "clean_complete_closure": clean_closure,
        },
        "final_verifier_pass": final_pass,
        "stop_reason": stop_reason,
        "status": status,
        "workspace": str(workspace),
        "result_directory": str(result_dir),
        "final_output_files": sorted(
            str(path.relative_to(workspace / "output"))
            for path in (workspace / "output").rglob("*")
            if path.is_file()
        ),
    }
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if RESUME_MODE:
        logical_dir.mkdir(parents=True, exist_ok=True)
        previous = logical_dir / "result.json"
        try:
            previous_attempt = max(1, int(ATTEMPT_ID) - 1)
        except ValueError:
            previous_attempt = 1
        previous_archive = logical_dir / "attempts" / f"attempt-{previous_attempt:02d}" / "result.json"
        if previous.is_file() and not previous_archive.is_file():
            previous_archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, previous_archive)
        shutil.copy2(result_dir / "result.json", previous)
    return result


def load_latest_result(condition: str, replicate: int) -> dict[str, Any] | None:
    logical_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_path = logical_dir / "result.json"
    if not result_path.is_file():
        return None
    return json.loads(result_path.read_text(encoding="utf-8"))


def db_usage(workspace: str) -> dict[str, Any]:
    """Read each isolated OpenCode database read-only for post-hoc identity/cost audit."""

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


def write_posthoc_audit(results: list[dict[str, Any]], requested_model: str) -> dict[str, Any]:
    per_trial: list[dict[str, Any]] = []
    for result in results:
        record = dict(result)
        record["opencode_db"] = db_usage(result["workspace"])
        record["output_file_count"] = len(result.get("final_output_files", []))
        record["included_in_formal_stats"] = not str(result.get("status", "")).startswith("quarantined_")
        if not record["included_in_formal_stats"]:
            record["formal_exclusion_reason"] = result.get("stop_reason", result.get("status"))
        per_trial.append(record)
    expected_provider, expected_model = requested_model.split("/", 1)
    all_sessions = [item["session_id"] for item in per_trial]
    conditions: dict[str, Any] = {}
    for condition in CONDITIONS:
        all_values = [item for item in per_trial if item["condition"] == condition]
        values = [item for item in all_values if item["included_in_formal_stats"]]
        all_totals = {
            key: sum(int(item["usage"].get(key) or 0) for item in values)
            for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
        }
        observed_totals = {
            key: sum(int(item["usage"].get(key) or 0) for item in all_values)
            for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
        }
        formal_totals = dict(all_totals)
        for totals in (observed_totals, formal_totals):
            totals["input_plus_output_tokens"] = totals["input_tokens"] + totals["output_tokens"]
            totals["recorded_cost"] = round(sum(float(item["opencode_db"]["recorded_cost"]) for item in (values if totals is formal_totals else all_values)), 8)
        conditions[condition] = {
            "planned_trials": 3,
            "result_files": len(all_values),
            "included_trials": len(values),
            "excluded_trials": len(all_values) - len(values),
            "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in values),
            "total_turns": sum(len(item["turns"]) for item in values),
            "model_turn_timeouts": sum(sum(bool(turn["model_turn_timeout"]) for turn in item["turns"]) for item in values),
            "verifier_timeouts": sum(sum(bool(turn["verifier_timeout"]) for turn in item["turns"]) for item in values),
            "unique_sessions": len({item["session_id"] for item in values if item["session_id"]}),
            "formal_totals": formal_totals,
            "all_attempt_totals": observed_totals,
        }
        if condition == "pd-vista":
            conditions[condition].update(
                {
                    key: sum(int(item["vista_metrics"].get(key) or 0) for item in values)
                    for key in (
                        "trigger_opportunities",
                        "routable_feedback",
                        "successful_disclosures",
                        "next_round_module_access",
                        "clean_complete_closure",
                    )
                }
            )
            conditions[condition]["disclosed_modules"] = sorted(
                {module for item in values for module in item["disclosed_modules"]}
            )
    observed_totals = {
        key: sum(int(item["usage"].get(key) or 0) for item in per_trial)
        for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
    }
    formal_trials = [item for item in per_trial if item["included_in_formal_stats"]]
    formal_totals = {
        key: sum(int(item["usage"].get(key) or 0) for item in formal_trials)
        for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
    }
    for totals, source in ((observed_totals, per_trial), (formal_totals, formal_trials)):
        totals["input_plus_output_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        totals["recorded_cost"] = round(sum(float(item["opencode_db"]["recorded_cost"]) for item in source), 8)

    excluded_trial_attempts: list[dict[str, Any]] = []
    for archived in sorted(RESULT_ROOT.glob("*/replicate-*/attempts/attempt-*/result.json")):
        try:
            archived_result = json.loads(archived.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(archived_result.get("status", "")).startswith("quarantined_"):
            excluded_trial_attempts.append(
                {
                    "condition": archived_result.get("condition"),
                    "replicate_id": archived_result.get("replicate_id"),
                    "status": archived_result.get("status"),
                    "stop_reason": archived_result.get("stop_reason"),
                    "session_id": archived_result.get("session_id"),
                    "attempt_result": str(archived),
                }
            )
    audit = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "task_id": "task-003",
        "benchmark_task_id": "data-to-d3",
        "audit_status": "passed" if len(per_trial) == 9 and all(item["final_verifier_pass"] for item in per_trial) else "completed_with_failures",
        "completed_trials": len(per_trial),
        "planned_trials": 9,
        "unique_sessions": len(set(all_sessions)),
        "session_ids_complete": all(all_sessions),
        "all_final_verifiers_passed": len(per_trial) == 9 and all(item["final_verifier_pass"] for item in per_trial),
        "formal_included_trials": len(formal_trials),
        "formal_all_final_verifiers_passed": bool(formal_trials) and all(item["final_verifier_pass"] for item in formal_trials),
        "nine_result_files": len(per_trial) == 9,
        "all_final_output_files_present": len(per_trial) == 9 and all(item["output_file_count"] > 0 for item in per_trial),
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "model_identity": {
            "requested_provider_id": expected_provider,
            "requested_model_id": expected_model,
            "consistent_across_events": all(
                item["actual_provider_ids"] == [expected_provider] and item["actual_model_ids"] == [expected_model]
                for item in per_trial
                if item["actual_provider_ids"] or item["actual_model_ids"]
            ),
            "consistent_across_isolated_databases": all(
                item["opencode_db"]["provider_ids"] == [expected_provider]
                and item["opencode_db"]["model_ids"] == [expected_model]
                for item in per_trial
                if item["opencode_db"]["exists"] and item["included_in_formal_stats"]
            ),
            "database_identity_complete_for_formal_trials": all(
                item["opencode_db"]["provider_ids"] == [expected_provider]
                and item["opencode_db"]["model_ids"] == [expected_model]
                for item in formal_trials
            ),
            "evidence": "read-only query of each isolated OpenCode opencode.db session and message tables",
        },
        "conditions": conditions,
        "totals": {
            "formal_included": formal_totals,
            "all_observed_result_files": observed_totals,
        },
        "trials": per_trial,
        "excluded_runs": EXCLUDED_DIAGNOSTIC_BATCHES,
        "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
        "excluded_trial_attempts": excluded_trial_attempts,
    }
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def controller_error_result(condition: str, replicate: int, requested_model: str, exc: Exception) -> dict[str, Any]:
    result_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-003",
        "condition": condition,
        "replicate_id": replicate,
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
        "controller_error": f"{type(exc).__name__}: {exc}",
        "workspace": str(LOCAL_ROOT / f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}" / "workspace"),
        "result_directory": str(result_dir),
        "final_output_files": [],
    }
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    if (RESULT_ROOT.exists() or AUDIT_ROOT.exists()) and not RESUME_MODE:
        raise RuntimeError(f"refusing to reuse task3 formal output: {RESULT_ROOT} or {AUDIT_ROOT}")
    launcher = freeze_launcher()
    readiness = dependency_readiness()
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not readiness["ready"]:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 2
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        try:
            result = run_replicate(condition, replicate, config_path=config_path, requested_model=requested_model)
        except Exception as exc:
            result = controller_error_result(condition, replicate, requested_model, exc)
            print(f"condition={condition} replicate={replicate} controller_error={type(exc).__name__}: {exc}", flush=True)
        results.append(result)

    all_results: list[dict[str, Any]] = []
    for replicate, condition in (
        (replicate, condition)
        for replicate in REPLICATES
        for condition in CONDITIONS
    ):
        latest = load_latest_result(condition, replicate)
        if latest is not None:
            all_results.append(latest)
    condition_summaries: dict[str, Any] = {}
    for condition in CONDITIONS:
        all_values = [item for item in all_results if item["condition"] == condition]
        values = [item for item in all_values if not str(item.get("status", "")).startswith("quarantined_")]
        condition_summaries[condition] = {
            "planned_trials": 3,
            "result_files": len(all_values),
            "formal_included_trials": len(values),
            "excluded_trials": len(all_values) - len(values),
            "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in values),
            "usage": {
                key: sum(int(item["usage"].get(key) or 0) for item in values)
                for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
            },
        }
    summary = {
        "schema_version": 1,
        "run_id": RUN_PREFIX,
        "protocol": PROTOCOL,
        "task_id": "task-003",
        "benchmark_task_id": "data-to-d3",
        "conditions": list(CONDITIONS),
        "replicates_per_condition": 3,
        "planned_trials": 9,
        "completed_trials": len(all_results),
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in FULL_SCHEDULE],
        "requested_provider_model": requested_model,
        "opencode": {
            "config_path": str(config_path),
            "launcher": launcher.launcher,
            "version": launcher.version,
            "binary_sha256": launcher.binary_sha256,
        },
        "timeouts": {
            "max_turns": MAX_TURNS,
            "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS,
            "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS,
        },
        "condition_summaries": condition_summaries,
        "results": [
            {
                key: item[key]
                for key in (
                    "condition",
                    "replicate_id",
                    "status",
                    "stop_reason",
                    "final_verifier_pass",
                    "session_id",
                    "actual_model_ids",
                    "actual_provider_ids",
                    "usage",
                    "initially_available_modules",
                    "disclosed_modules",
                    "accessed_modules_after_disclosure",
                    "disclosure_events",
                    "vista_metrics",
                    "final_output_files",
                )
            }
            for item in all_results
        ],
        "diagnostic_batches": EXCLUDED_DIAGNOSTIC_BATCHES,
        "docker_used": False,
        "skillbench_execution_route_used": False,
    }
    (AUDIT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = write_posthoc_audit(all_results, requested_model)
    print(json.dumps({"run_id": RUN_PREFIX, "formal_results": len(all_results), "resumed_trials": len(results), "audit_status": audit["audit_status"]}, ensure_ascii=False))
    return 0 if len(all_results) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
