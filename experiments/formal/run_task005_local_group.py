"""Run task-005 locally with OpenCode in a balanced 3-condition experiment.

This task-specific runner uses the host OpenCode CLI only.  It never invokes
Docker, BenchFlow, or the SkillsBench execution platform.  Each attempt gets
its own task files, input corpus, verifier log directory, OpenCode state, and
session; only dependency caches and the read-only auth store are shared.
"""

from __future__ import annotations

import hashlib
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
TASK_IDENTITY = REPO / "tasks" / "pilot" / "task-005" / "task.yaml"
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "organize-messy-files"
ENVIRONMENT = TASK_SOURCE / "environment"
ASSET_ROOT = REPO / "data" / "materialized" / "task005-organize-messy-files"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-005"
ORIGINAL_VERIFIER = TASK_SOURCE / "verifier" / "test_outputs.py"

RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task005-local-3x3-minimaxm3-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
)
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task005-local-opencode-minimaxm3-public-diagnostics-resumable-timeout-noskill-native-pdvista-3x3",
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
VERIFIER_TIMEOUT_SECONDS = 900
MAX_RATE_LIMIT_RETRIES = 5
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
MODULE_IDS = (
    "planning-with-files",
    "file-organizer",
    "pdf",
    "docx",
    "pptx",
)
SUBJECTS = ("LLM", "trapped_ion_and_qc", "black_hole", "DNA", "music_history")
ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".docx"}

COMMON_PROMPT = """Solve the isolated organize-messy-files task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inventory all local input files. Organize every supplied PDF, PPTX, and DOCX into exactly one of the five requested subject folders, preserving file names and file contents. Use only local tools and local task materials; do not fetch data or install libraries. You may run the public verifier only through the controller. The controller will run the complete acceptance verifier after each turn and may return a cleaned public diagnostic for repair."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT
    + " No task Skill is available in this condition; solve from task.md and the local files only.",
    "native": COMMON_PROMPT
    + " The complete original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
    "pd-vista": COMMON_PROMPT
    + " Read VISTA.md. Original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose at most one relevant module under ./modules/<module-id>/SKILL.md. Read any disclosed module before the next repair turn.",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timeout_completed(command: list[str], exc: subprocess.TimeoutExpired) -> subprocess.CompletedProcess[str]:
    def decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    return subprocess.CompletedProcess(command, 124, stdout=decode(exc.stdout), stderr=decode(exc.stderr))


def tool_environment(base: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(REPO / ".venv" / "bin") + os.pathsep + env.get("PATH", "")
    env["OPENCODE_CONFIG"] = str(Path(os.environ.get("VISTA_OPENCODE_CONFIG", Path.home() / ".config/opencode/opencode.json")).expanduser().resolve())
    env.pop("OPENCODE_CONFIG_DIR", None)
    env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")
    return env


def _source_files() -> list[Path]:
    files = sorted(ASSET_ROOT.joinpath("all").glob("*.pdf"))
    files += [ENVIRONMENT / "DAMOP.pptx", ENVIRONMENT / "paper_file_1.docx", ENVIRONMENT / "paper_file_2.docx"]
    return files


def asset_readiness() -> dict[str, Any]:
    files = _source_files()
    pdfs = [path for path in files if path.suffix.lower() == ".pdf"]
    checks: dict[str, Any] = {
        "task_identity_file": TASK_IDENTITY.is_file(),
        "task_instruction": (TASK_SOURCE / "task.md").is_file(),
        "original_verifier": ORIGINAL_VERIFIER.is_file(),
        "original_verifier_script": (TASK_SOURCE / "verifier" / "test.sh").is_file(),
        "asset_root": ASSET_ROOT.is_dir(),
        "pdf_count": len(pdfs),
        "office_input_count": len(files) - len(pdfs),
        "task_asset_count": len(files),
        "native_modules": {module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS},
        "native_module_file_counts": {
            module: len([p for p in (NATIVE_SOURCE / module).rglob("*") if p.is_file()])
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
        "public_verifier_entry": str(ORIGINAL_VERIFIER),
        "asset_checksums": {},
    }
    try:
        import yaml

        identity = yaml.safe_load(TASK_IDENTITY.read_text(encoding="utf-8"))
        checks["task_identity_id"] = identity.get("task_id") if isinstance(identity, dict) else None
        source = identity.get("source", {}) if isinstance(identity, dict) else {}
        checks["task_identity_resolved"] = bool(
            isinstance(identity, dict)
            and identity.get("task_id") == "task-005"
            and source.get("benchmark_task_id") == "organize-messy-files"
            and source.get("definition_path") == "vendor/skillsbench/tasks/organize-messy-files"
        )
    except Exception as exc:
        checks["task_identity_error"] = f"{type(exc).__name__}: {exc}"
    checks["verifier_python"] = VERIFIER_PYTHON.is_file()
    if VERIFIER_PYTHON.is_file():
        probe = subprocess.run(
            [str(VERIFIER_PYTHON), "-c", "import pytest, PyPDF2; print('pytest', pytest.__version__); print('PyPDF2', PyPDF2.__version__)"],
            capture_output=True, text=True, check=False,
        )
        checks["verifier_dependencies"] = probe.returncode == 0
        checks["verifier_dependency_probe"] = ((probe.stdout or "") + (probe.stderr or "")).strip()
        collect = subprocess.run(
            [str(VERIFIER_PYTHON), "-m", "pytest", "--collect-only", "-q", str(ORIGINAL_VERIFIER)],
            cwd=TASK_SOURCE,
            capture_output=True, text=True, check=False,
        )
        checks["verifier_collect_ok"] = collect.returncode == 0
        checks["verifier_collect_output"] = ((collect.stdout or "") + (collect.stderr or "")).strip()[-2000:]
    else:
        checks["verifier_dependencies"] = False
        checks["verifier_collect_ok"] = False
    for path in files:
        if path.is_file():
            checks["asset_checksums"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    checks["assets_readable_by_file"] = all(path.is_file() and path.stat().st_size > 0 for path in files)
    checks["ready"] = all(
        bool(checks.get(key))
        for key in (
            "task_identity_file", "task_instruction", "original_verifier", "original_verifier_script",
            "asset_root", "task_identity_resolved", "verifier_python", "verifier_dependencies",
            "verifier_collect_ok", "assets_readable_by_file", "vista_root", "vista_manifest", "vista_source",
        )
    ) and checks["pdf_count"] == 100 and checks["office_input_count"] == 3 and all(checks["native_modules"].values())
    return checks


def active_process_check() -> dict[str, Any]:
    patterns = re.compile(r"opencode|run_task00[2345]", re.IGNORECASE)
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, check=False,
        )
        lines: list[str] = []
        for line in (completed.stdout or "").splitlines():
            if not patterns.search(line):
                continue
            if "run_task005_local_group" in line or "-m experiments.formal.run_task005_local_group" in line:
                continue
            lines.append(line)
        return {"available": True, "active_matching_processes": lines, "clear": not lines}
    except Exception as exc:
        return {"available": False, "active_matching_processes": [], "clear": True, "error": str(exc), "note": "Controller performed an external process check before formal start."}


def prepare_trial(condition: str, replicate: int, attempt: int) -> tuple[Path, Path, Path, Path]:
    run_id = f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}-attempt-{attempt:02d}"
    base = LOCAL_ROOT / run_id
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace = base / "workspace"
    verifier = base / "verifier"
    router_source = base / "router-source"
    workspace.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(parents=True, exist_ok=True)
    router_source.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    # The original verifier writes only diagnostic snapshots to /logs/verifier,
    # which is read-only on this macOS host.  Keep every assertion and expected
    # mapping byte-identical, but alias that public log location to this trial's
    # isolated verifier copy.
    verifier_source = ORIGINAL_VERIFIER.read_text(encoding="utf-8")
    verifier_source = verifier_source.replace(
        'Path("/logs/verifier")', 'Path.cwd() / ".verifier-logs"'
    )
    (verifier / "test_outputs.py").write_text(verifier_source, encoding="utf-8")
    for source in _source_files():
        shutil.copy2(source, workspace / source.name)
    if condition in {"native", "pd-vista"}:
        (workspace / "modules").mkdir()
    if condition == "native":
        for module_id in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module_id, workspace / "modules" / module_id)
    elif condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module_id in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module_id, router_source / "modules" / module_id)
    manifest = {
        "run_id": run_id,
        "condition": condition,
        "replicate_id": replicate,
        "attempt": attempt,
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "asset_root": str(ASSET_ROOT),
        "workspace": str(workspace),
        "verifier": str(ORIGINAL_VERIFIER),
        "docker_used": False,
        "skillbench_execution_route_used": False,
    }
    (base / "trial-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base, workspace, verifier, router_source


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "no module named", "modulenotfounderror", "failed to import", "error while loading conftest",
        "unrecognized arguments", "internalerror", "permission denied", "no tests ran",
        "read-only file system",
    ))


def run_verifier(workspace: Path, verifier: Path, turn: int) -> tuple[bool, str, bool, bool]:
    command = [
        str(VERIFIER_PYTHON), "-m", "pytest", "-q", str(verifier / "test_outputs.py"),
        "-rA", "--disable-warnings", f"--basetemp={verifier / f'pytest-temp-turn-{turn}'}",
    ]
    try:
        completed = subprocess.run(
            command, cwd=workspace, env=tool_environment(verifier.parent), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=VERIFIER_TIMEOUT_SECONDS, check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = _timeout_completed(command, exc)
        output = "Verifier timeout after 900 seconds.\n" + (completed.stdout or "")
        timed_out = True
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = timed_out or verifier_infrastructure_failure(output)
    return completed.returncode == 0 and not infra, output, infra, timed_out


def public_diagnostic(output: str, *, limit: int = 6000) -> str:
    selected: list[str] = []
    markers = (
        "failed", "failure", "error", "missing", "wrong", "duplicate", "unexpected", "stray",
        "unreadable", "empty", "passed", "assert", "could not find",
    )
    for raw in output.splitlines():
        line_folded = raw.casefold()
        if not any(marker in line_folded for marker in markers):
            continue
        line = re.sub(r"/Users/[^\s:]+", "<local-path>", raw).strip()
        line = re.sub(r"/private/tmp/[^\s:]+", "<local-path>", line)
        if line and line not in selected:
            selected.append(line)
    return ("\n".join(selected) or "The public local verifier failed; inspect the five subject folders and rerun the public check.")[:limit]


def public_feedback_types(diagnostic: str) -> list[str]:
    folded = diagnostic.casefold()
    if "timed out" in folded or "timeout" in folded:
        return ["timeout"]
    if "no module named" in folded or "modulenotfounderror" in folded or "dependency" in folded:
        return ["dependency_error"]
    if "unreadable" in folded or "zipfile" in folded or "pdfreader" in folded or "format" in folded:
        return ["wrong_format"]
    if "missing" in folded or "stray" in folded or "leftover" in folded or "subject folder" in folded:
        return ["incomplete_output"]
    if "wrong subject" in folded or "duplicate" in folded or "unexpected" in folded or "test_" in folded or "failed" in folded:
        return ["test_failure"]
    return ["command_failure"]


def rate_limit_reason(summary: dict[str, Any], text: str) -> str | None:
    folded = text.casefold()
    for error in summary.get("errors", []):
        if str(error.get("status_code")) == "429":
            return "http_429_rate_limit"
    # Do not match ordinary task text such as "rate limitation" in a paper.
    # Only structured HTTP/API/quota wording is eligible for exclusion.
    structured_markers = (
        r"\bhttp\s*429\b", r"\bstatus[_ ]?code\s*[:=]?\s*429\b",
        r"\brate_limit_error\b", r"\btoo many requests\b", r"\bquota exceeded\b",
        r"\btoken plan.{0,50}(?:usage|quota|rate).{0,30}limit\b",
        r"已达到[^\n]{0,60}(?:配额|速率|用量)限制",
        r"\b(?:temporarily overloaded|service overloaded)\b",
    )
    if any(re.search(marker, folded, re.IGNORECASE) for marker in structured_markers):
        return "zero_request_shared_limit" if summary.get("requests", 0) == 0 else "rate_limit_response"
    return None


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    source = router_source / "modules" / module_id
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(source, destination)
    return destination / "SKILL.md"


def _inventory(workspace: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    for subject in SUBJECTS:
        folder = workspace / subject
        files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
        supported = [p for p in files if p.suffix.lower() in ALLOWED_EXTENSIONS]
        counts[subject] = len(supported)
        names[subject] = [p.name for p in supported]
    outside = sorted(
        p.name for p in workspace.rglob("*")
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS and p.parent.name not in SUBJECTS
    )
    return {"subject_counts": counts, "subject_names": names, "allowed_files_outside_subject_folders": outside}


def run_attempt(condition: str, replicate: int, attempt: int, *, config_path: Path, requested_model: str) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate, attempt)
    parser = FeedbackParser()
    router = DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml")) if condition == "pd-vista" else None
    disclosed: list[str] = []
    disclosure_events: list[dict[str, Any]] = []
    accessed_after_disclosure: list[str] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    final_pass = False
    stop_reason = "max_turns"
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    process_env = tool_environment(base)
    link_opencode_auth_store(base)

    for turn in range(1, MAX_TURNS + 1):
        command = command_for_turn(workspace=workspace, prompt=prompt, session_id=session_id, model=requested_model)
        model_timed_out = False
        try:
            completed = subprocess.run(
                command, cwd=workspace, env=process_env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=MODEL_TURN_TIMEOUT_SECONDS, check=False,
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
        if accessed_now:
            accessed_after_disclosure.extend(accessed_now)
        for event in disclosure_events:
            if event["next_round_turn"] == turn and not event["accessed_next_round"] and event["module_id"] in accessed_now:
                event["accessed_next_round"] = True
                access_rounds += 1
        limit_reason = rate_limit_reason(summary, stdout + "\n" + stderr)
        if limit_reason:
            stop_reason = limit_reason
            (verifier / f"verifier-turn-{turn}.txt").write_text(
                f"Excluded before task verification: shared provider rate limit ({limit_reason}).\n", encoding="utf-8"
            )
            turns.append({
                "turn": turn, "prompt": prompt, "return_code": completed.returncode,
                "model_turn_timeout": model_timed_out, "event_summary": summary,
                "accessed_modules_this_turn": accessed_now, "verifier_pass": False,
                "verifier_infrastructure_error": False, "verifier_timeout": False,
                "rate_limit_excluded": True, "rate_limit_reason": limit_reason,
                "public_feedback_types": [], "public_diagnostic": "", "newly_disclosed_modules": [],
            })
            print(f"condition={condition} replicate={replicate} attempt={attempt} turn={turn} requests={summary['requests']} tools={summary['tool_uses']} rate_limit={limit_reason}", flush=True)
            break
        verifier_pass, verifier_output, verifier_infra, verifier_timed_out = run_verifier(workspace, verifier, turn)
        turn_record: dict[str, Any] = {
            "turn": turn, "prompt": prompt, "return_code": completed.returncode,
            "model_turn_timeout": model_timed_out, "event_summary": summary,
            "accessed_modules_this_turn": accessed_now, "verifier_pass": verifier_pass,
            "verifier_infrastructure_error": verifier_infra, "verifier_timeout": verifier_timed_out,
            "rate_limit_excluded": False, "public_feedback_types": [], "public_diagnostic": "",
            "newly_disclosed_modules": [],
        }
        turns.append(turn_record)
        print(f"condition={condition} replicate={replicate} attempt={attempt} turn={turn} requests={summary['requests']} tools={summary['tool_uses']} accessed={accessed_now} verifier_pass={verifier_pass} model_timeout={model_timed_out} verifier_timeout={verifier_timed_out}", flush=True)
        if verifier_pass:
            final_pass = True
            stop_reason = "verifier_pass_after_model_timeout" if any(item["model_turn_timeout"] for item in turns) else "verifier_pass"
            break
        if verifier_infra:
            stop_reason = "verifier_timeout" if verifier_timed_out else "verifier_infrastructure_error"
            break
        if summary["errors"]:
            stop_reason = summary["errors"][-1]["category"]
            break

        trigger_opportunities += 1
        diagnostic = public_diagnostic(verifier_output)
        feedback_types = public_feedback_types(diagnostic)
        turn_record["public_feedback_types"] = feedback_types
        turn_record["public_diagnostic"] = diagnostic
        newly_disclosed: list[str] = []
        if condition == "pd-vista":
            assert router is not None
            payload = json.dumps({
                "schema_version": 1, "kind": "vista_public_verifier_feedback",
                "outcome": "fail", "feedback_types": feedback_types, "summary": diagnostic,
            }, ensure_ascii=False)
            classified = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}",
            )
            decision = router.route(classified, previous_modules=disclosed)
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
                disclosure_events.append({
                    "trigger_turn": turn, "next_round_turn": turn + 1, "module_id": module.module_id,
                    "decision_reason": decision.reason, "accessed_next_round": False,
                })
        turn_record["newly_disclosed_modules"] = newly_disclosed
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(f"./modules/{module}/SKILL.md" for module in newly_disclosed) + ". Read it before repairing."
        elif condition == "native":
            instruction = "Use the original modules already available under ./modules/."
        elif disclosed:
            instruction = "Use the already disclosed modules: " + ", ".join(disclosed) + "."
        else:
            instruction = "No task Skill is available; repair from the public diagnostic and local files."
        prompt = (
            f"A public local check failed with {', '.join(feedback_types)}. Public diagnostic:\n{diagnostic}\n{instruction} "
            "Continue in the same OpenCode session, work only inside this directory, repair the five subject folders, "
            "preserve file names and contents, and let the controller rerun the complete public verifier."
        )

    actual_models = list(dict.fromkeys(model for turn in turns for model in turn["event_summary"]["actual_model_ids"]))
    actual_providers = list(dict.fromkeys(provider for turn in turns for provider in turn["event_summary"]["provider_ids"]))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model}:
        stop_reason = "model_identity_changed"
        final_pass = False
    if actual_providers and set(actual_providers) != {expected_provider}:
        stop_reason = "model_identity_changed"
        final_pass = False
    clean_closure = int(condition == "pd-vista" and final_pass and disclosure_count > 0 and access_rounds > 0)
    usage = {key: sum(int(turn["event_summary"].get(key) or 0) for turn in turns) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
    quarantined = stop_reason in {"provider_policy_rejection", "api_error", "session_identity_changed", "verifier_infrastructure_error", "verifier_timeout", "model_identity_changed"}
    rate_quarantined = stop_reason in {"http_429_rate_limit", "rate_limit_response", "zero_request_shared_limit"}
    status = (
        "quarantined_rate_limit" if rate_quarantined else
        "passed_with_clean_vista_feedback_loop" if clean_closure else
        "passed" if final_pass and condition != "pd-vista" else
        "passed_without_clean_vista_feedback_loop" if final_pass else
        f"quarantined_{stop_reason}" if quarantined else "failed_max_turns"
    )
    inventory = _inventory(workspace)
    (base / "workspace-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}-attempt-{attempt:02d}",
        "task_id": "task-005", "benchmark_task_id": "organize-messy-files",
        "condition": condition, "replicate_id": replicate, "attempt": attempt,
        "backend": "opencode-local-current-config", "docker_used": False,
        "skillbench_execution_route_used": False, "requested_provider_model": requested_model,
        "expected_actual_model": expected_model, "actual_model_ids": actual_models,
        "expected_provider": expected_provider, "actual_provider_ids": actual_providers,
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "usage": usage, "session_id": session_id, "turns": turns,
        "disclosed_modules": disclosed, "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)),
        "disclosure_events": disclosure_events,
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities, "routable_feedback": routable_feedback,
            "successful_disclosures": disclosure_count, "next_round_module_access": access_rounds,
            "clean_complete_closure": clean_closure,
        },
        "final_verifier_pass": final_pass, "stop_reason": stop_reason, "status": status,
        "workspace": str(workspace), "verifier": str(ORIGINAL_VERIFIER), "inventory": inventory,
        "result_directory": str(base),
    }
    return result


def save_formal_result(result: dict[str, Any]) -> dict[str, Any]:
    result_dir = RESULT_ROOT / result["condition"] / f"replicate-{int(result['replicate_id']):02d}"
    result_dir.mkdir(parents=True, exist_ok=False)
    base = Path(result["result_directory"])
    for artifact in sorted(base.glob("turn-*.*")):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in sorted((base / "verifier").glob("verifier-turn-*.txt")):
        shutil.copy2(artifact, result_dir / artifact.name)
    shutil.copy2(base / "workspace-inventory.json", result_dir / "workspace-inventory.json")
    result["result_directory"] = str(result_dir)
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def save_excluded_attempt(result: dict[str, Any]) -> dict[str, Any]:
    excluded = RESULT_ROOT / "excluded-attempts" / result["condition"] / f"replicate-{int(result['replicate_id']):02d}-attempt-{int(result['attempt']):02d}"
    excluded.mkdir(parents=True, exist_ok=False)
    base = Path(result["result_directory"])
    for artifact in sorted(base.glob("turn-*.*")):
        shutil.copy2(artifact, excluded / artifact.name)
    for artifact in sorted((base / "verifier").glob("verifier-turn-*.txt")):
        shutil.copy2(artifact, excluded / artifact.name)
    result["excluded_from_formal_statistics"] = True
    result["result_directory"] = str(excluded)
    (excluded / "excluded-attempt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def audit_db(db_path: Path, expected_model: str, expected_provider: str) -> dict[str, Any]:
    audit: dict[str, Any] = {"path": str(db_path), "exists": db_path.is_file(), "sessions": [], "db_read_error": None}
    if not db_path.is_file():
        return audit
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            "select id,directory,version,cost,tokens_input,tokens_output,tokens_reasoning,tokens_cache_read,tokens_cache_write,model from session order by time_created"
        ).fetchall()
        for row in rows:
            model_value = json.loads(row[9]) if row[9] else {}
            audit["sessions"].append({
                "session_id": row[0], "directory": row[1], "opencode_version": row[2], "recorded_cost_usd": row[3],
                "tokens_input": row[4], "tokens_output": row[5], "tokens_reasoning": row[6],
                "tokens_cache_read": row[7], "tokens_cache_write": row[8], "model": model_value,
                "model_matches": model_value.get("id") == expected_model,
                "provider_matches": model_value.get("providerID") == expected_provider,
            })
        audit["session_count"] = len(rows)
        audit["unique_session_ids"] = len({row[0] for row in rows})
        audit["provider_model_match"] = bool(rows) and all(
            json.loads(row[9]).get("id") == expected_model and json.loads(row[9]).get("providerID") == expected_provider
            for row in rows
        )
        con.close()
    except Exception as exc:
        audit["db_read_error"] = f"{type(exc).__name__}: {exc}"
    return audit


def posthoc_audit(results: list[dict[str, Any]], excluded_attempts: list[dict[str, Any]], requested_model: str) -> dict[str, Any]:
    expected_provider, expected_model = requested_model.split("/", 1)
    trials: list[dict[str, Any]] = []
    for result in results:
        base = Path(result["workspace"]).parent
        dbs = sorted(base.rglob("opencode.db"))
        db_audits = [audit_db(db, expected_model, expected_provider) for db in dbs]
        result["opencode_db_audit"] = db_audits
        result["db_session_count"] = sum(item.get("session_count", 0) for item in db_audits)
        result["db_recorded_cost_usd"] = sum(float(session.get("recorded_cost_usd") or 0) for item in db_audits for session in item.get("sessions", []))
        result["db_tokens"] = {
            key: sum(int(session.get(key) or 0) for item in db_audits for session in item.get("sessions", []))
            for key in ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
        }
        trials.append(result)
    unique_sessions = [item for result in trials for audit in result.get("opencode_db_audit", []) for item in audit.get("sessions", [])]
    audit = {
        "schema_version": 1, "run_id": RUN_PREFIX, "task_id": "task-005", "benchmark_task_id": "organize-messy-files",
        "formal_results": trials, "excluded_attempts": excluded_attempts,
        "formal_trial_count": len(trials), "formal_result_files": len(trials),
        "unique_formal_sessions": len({item["session_id"] for item in unique_sessions}),
        "formal_sessions_with_expected_provider_model": all(item["model_matches"] and item["provider_matches"] for item in unique_sessions),
        "docker_used": False, "skillbench_execution_route_used": False,
        "evidence": "Read-only sqlite queries against each isolated OpenCode opencode.db plus raw turn JSONL and final Verifier logs.",
        "excluded_note": "Diagnostic and shared 429/zero-request attempts are retained under excluded-attempts and excluded from formal condition statistics.",
    }
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    launcher = freeze_launcher()
    if RESULT_ROOT.exists() or AUDIT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse task5 formal output directory: {RESULT_ROOT} or {AUDIT_ROOT}")
    RESULT_ROOT.mkdir(parents=True, exist_ok=False)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=False)
    readiness = asset_readiness()
    readiness["active_process_check"] = active_process_check()
    readiness["opencode"] = {"launcher": launcher.launcher, "binary": launcher.binary, "version": launcher.version, "launcher_sha256": launcher.launcher_sha256, "binary_sha256": launcher.binary_sha256}
    readiness["requested_provider_model"] = requested_model
    readiness["timeouts"] = {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS}
    readiness["concurrency_note"] = "Pre-formal controller check found no active task2-task5 OpenCode process and no current shared 429 call; historical 429 records were retained only as prior evidence."
    (RESULT_ROOT / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not readiness["ready"]:
        (RESULT_ROOT / "group-summary.json").write_text(json.dumps({"schema_version": 1, "run_id": RUN_PREFIX, "task_id": "task-005", "status": "quarantined_readiness", "readiness": readiness}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2
    if not readiness["active_process_check"].get("clear", True):
        raise RuntimeError("active task2-task5 OpenCode process detected before formal batch")

    formal_results: list[dict[str, Any]] = []
    excluded_attempts: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        latest: dict[str, Any] | None = None
        for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 2):
            latest = run_attempt(condition, replicate, attempt, config_path=config_path, requested_model=requested_model)
            if latest["status"] == "quarantined_rate_limit":
                excluded_attempts.append({
                    "condition": condition, "replicate_id": replicate, "attempt": attempt,
                    "status": latest["status"], "stop_reason": latest["stop_reason"],
                    "session_id": latest["session_id"], "result_directory": latest["result_directory"],
                    "reason": "shared provider 429/zero-request limit; excluded from formal task statistics and retried serially with a fresh isolated state and session",
                })
                save_excluded_attempt(latest)
                if attempt <= MAX_RATE_LIMIT_RETRIES:
                    continue
            break
        if latest is None:
            raise RuntimeError(f"no result for {condition} replicate {replicate}")
        if latest["status"] == "quarantined_rate_limit":
            latest["status"] = "quarantined_rate_limit_retry_exhausted"
            formal_results.append(save_formal_result(latest))
        else:
            formal_results.append(save_formal_result(latest))

    # The formal set is exactly the 9 latest non-rate-limit records.  True model,
    # session, or verifier infrastructure errors remain explicitly quarantined.
    formal_included = [item for item in formal_results if not str(item.get("status", "")).startswith("quarantined_rate_limit")]
    condition_summaries: dict[str, Any] = {}
    for condition in CONDITIONS:
        values = [item for item in formal_included if item["condition"] == condition]
        condition_summaries[condition] = {
            "planned_trials": 3, "formal_trials": len(values), "result_files": len(values),
            "quarantined_trials": sum(str(item.get("status", "")).startswith("quarantined_") for item in values),
            "verifier_passes": sum(bool(item.get("final_verifier_pass")) for item in values),
            "usage": {key: sum(int(item.get("usage", {}).get(key) or 0) for item in values) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")},
        }
    summary = {
        "schema_version": 1, "run_id": RUN_PREFIX, "protocol": PROTOCOL,
        "task_id": "task-005", "benchmark_task_id": "organize-messy-files",
        "conditions": list(CONDITIONS), "replicates_per_condition": 3, "planned_trials": 9,
        "completed_trials": len(formal_results), "formal_included_trials": len(formal_included),
        "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE],
        "requested_provider_model": requested_model,
        "opencode": {"config_path": str(config_path), "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256},
        "timeouts": {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS},
        "docker_used": False, "skillbench_execution_route_used": False,
        "excluded_attempts": excluded_attempts,
        "condition_summaries": condition_summaries,
        "results": [{
            "condition": item["condition"], "replicate_id": item["replicate_id"], "attempt": item["attempt"],
            "status": item["status"], "final_verifier_pass": item["final_verifier_pass"], "session_id": item["session_id"],
            "actual_model_ids": item["actual_model_ids"], "actual_provider_ids": item["actual_provider_ids"],
            "usage": item["usage"], "initially_available_modules": item["initially_available_modules"],
            "disclosed_modules": item["disclosed_modules"], "accessed_modules_after_disclosure": item["accessed_modules_after_disclosure"],
            "disclosure_events": item["disclosure_events"], "vista_metrics": item["vista_metrics"], "inventory": item["inventory"],
        } for item in formal_results],
    }
    (RESULT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    posthoc_audit(formal_results, excluded_attempts, requested_model)
    return 0 if len(formal_results) == 9 and len(formal_included) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
