"""Run task-001 locally with OpenCode in three conditions and three replicates."""

from __future__ import annotations

import json
import os
import re
import shutil
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
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "python-scala-translation"
ENVIRONMENT = TASK_SOURCE / "environment"
NATIVE_SOURCE = ENVIRONMENT / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-001"
TOOLS = REPO / ".tools"
TOOLS_BIN = TOOLS / "bin"
COURSIER_CACHE = TOOLS / "coursier" / "cache"
RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task001-local-3x3-opencode-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
)
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL", "task001-local-opencode-noskill-native-pd-vista-3x3"
)
LOCAL_ROOT = Path(
    os.environ.get(
        "VISTA_EXPERIMENT_ROOT", Path.home() / "Library" / "Caches" / "VistaExperiments"
    )
).expanduser().resolve()
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", sys.executable)
).expanduser().absolute()
MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
CONDITIONS = ("noskill", "native", "pd-vista")
REPLICATES = (1, 2, 3)
SCHEDULE = (
    (1, "noskill"), (1, "native"), (1, "pd-vista"),
    (2, "native"), (2, "pd-vista"), (2, "noskill"),
    (3, "pd-vista"), (3, "noskill"), (3, "native"),
)
MODULE_IDS = (
    "python-scala-syntax-mapping",
    "python-scala-idioms",
    "python-scala-oop",
    "python-scala-functional",
    "python-scala-collections",
    "python-scala-libraries",
)

COMMON_PROMPT = """Solve the isolated Python-to-Scala translation task in this directory. Work only inside this directory and do not access parent or external directories. Read task.md and Tokenizer.py. Write the complete Scala 2.13 implementation to ./Tokenizer.scala. Preserve all required classes and functions and use maintainable Scala conventions. You may run ./compile.sh for a public compilation check. Hidden unit tests are run only by the controller."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT + " No task Skill is available in this condition.",
    "native": COMMON_PROMPT + " All original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
    "pd-vista": COMMON_PROMPT + " Read VISTA.md. Original guidance modules are initially hidden; after a public compiler or test failure, VISTA may disclose one module under ./modules/<module-id>/SKILL.md.",
}


def java_home() -> Path:
    candidates = sorted((TOOLS / "jdk17").glob("*/Contents/Home"))
    if len(candidates) != 1 or not (candidates[0] / "bin" / "java").is_file():
        raise RuntimeError("project-local JDK 17 is not installed exactly once")
    return candidates[0].resolve()


def tool_environment(base: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home())
    env["PATH"] = str(TOOLS_BIN) + os.pathsep + str(java_home() / "bin") + os.pathsep + env.get("PATH", "")
    env["COURSIER_CACHE"] = str(COURSIER_CACHE)
    env["SBT_OPTS"] = "-Dsbt.supershell=false -Dsbt.log.noformat=true -Dsbt.server.autostart=false"
    if base is not None:
        # Dependency-only caches are shared and prewarmed; trial sources and build targets remain
        # isolated. This avoids nine network fetches without sharing any task/model state.
        env["SBT_GLOBAL_BASE"] = str(TOOLS / "sbt" / "global")
        env["SBT_BOOT_DIRECTORY"] = str(TOOLS / "sbt" / "boot")
        env["IVY_HOME"] = str(TOOLS / "sbt" / "ivy")
    return env


def dependency_readiness() -> dict[str, Any]:
    checks: dict[str, Any] = {
        "coursier": (TOOLS / "coursier" / "cs").is_file(),
        "sbt_wrapper": (TOOLS_BIN / "sbt").is_file(),
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "task": (TASK_SOURCE / "task.md").is_file(),
        "python_source": (ENVIRONMENT / "Tokenizer.py").is_file(),
        "test_source": (
            ENVIRONMENT / "scala_tokenizer" / "src" / "test" / "scala" / "tokenizer" / "TokenizerSpec.scala"
        ).is_file(),
        "vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
        "native_modules": {
            module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS
        },
    }
    try:
        home = java_home()
        completed = subprocess.run(
            [str(home / "bin" / "java"), "-version"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        checks["java"] = completed.returncode == 0 and "17." in (
            (completed.stdout or "") + (completed.stderr or "")
        )
        checks["java_home"] = str(home)
    except Exception as exc:
        checks["java"] = False
        checks["java_error"] = str(exc)
    checks["ready"] = all(
        value if isinstance(value, bool) else all(value.values())
        for key, value in checks.items()
        if key not in {"java_home", "java_error"}
    )
    return checks


def _write_compile_helper(workspace: Path) -> None:
    text = """#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -f "$ROOT/Tokenizer.scala"
mkdir -p "$ROOT/compile-project/src/main/scala/tokenizer"
cp "$ROOT/Tokenizer.scala" "$ROOT/compile-project/src/main/scala/tokenizer/Tokenizer.scala"
cd "$ROOT/compile-project"
exec sbt compile
"""
    path = workspace / "compile.sh"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def prepare_trial(condition: str, replicate: int) -> tuple[Path, Path, Path, Path]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    base = LOCAL_ROOT / f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}"
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace = base / "workspace"
    verifier = base / "verifier"
    router_source = base / "router-source"
    workspace.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(exist_ok=True)
    router_source.mkdir(exist_ok=True)
    (workspace / "modules").mkdir(exist_ok=True)
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    shutil.copy2(ENVIRONMENT / "Tokenizer.py", workspace / "Tokenizer.py")
    compile_project = workspace / "compile-project"
    (compile_project / "project").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENVIRONMENT / "scala_tokenizer" / "build.sbt", compile_project / "build.sbt")
    shutil.copy2(
        ENVIRONMENT / "scala_tokenizer" / "project" / "build.properties",
        compile_project / "project" / "build.properties",
    )
    _write_compile_helper(workspace)

    if condition == "native":
        for module_id in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module_id, workspace / "modules" / module_id)
    elif condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module_id in MODULE_IDS:
            shutil.copytree(NATIVE_SOURCE / module_id, router_source / "modules" / module_id)

    localtest = verifier / "localtest"
    (localtest / "project").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ENVIRONMENT / "scala_tokenizer" / "project" / "build.properties",
        localtest / "project" / "build.properties",
    )
    return base, workspace, verifier, router_source


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "sbt not found", "unable to locate a java runtime", "no module named",
        "modulenotfounderror", "failed to download", "connection timed out",
    ))


def public_diagnostic(output: str, *, limit: int = 5000) -> str:
    """Keep agent-observable failure lines while removing host-specific absolute paths."""

    selected: list[str] = []
    markers = (
        "[error]", "compilation failed", "tests failed", "tests passed:",
        "not found", "is not a member", "missing output", "overall score", "status:",
    )
    for raw in output.splitlines():
        if not any(marker in raw.casefold() for marker in markers):
            continue
        line = re.sub(r"/Users/[^\s:]+", "<local-path>", raw).strip()
        if line and line not in selected:
            selected.append(line)
    diagnostic = "\n".join(selected)
    return (diagnostic or "The local compiler or unit-test command failed.")[:limit]


def run_verifier(workspace: Path, verifier: Path, base: Path, turn: int) -> tuple[bool, str, bool]:
    output_file = workspace / "Tokenizer.scala"
    if not output_file.is_file():
        output = "Incomplete output: missing output Tokenizer.scala"
        (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
        return False, output, False
    command = [
        str(VERIFIER_PYTHON), str(TASK_SOURCE / "verifier" / "test_outputs.py"),
        str(output_file), str(ENVIRONMENT / "scala_tokenizer" / "build.sbt"),
        str(ENVIRONMENT / "scala_tokenizer" / "src" / "test" / "scala" / "tokenizer" / "TokenizerSpec.scala"),
        "--project-dir", str(verifier / "localtest"), "--json",
    ]
    completed = subprocess.run(
        command, cwd=verifier, env=tool_environment(base), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = verifier_infrastructure_failure(output)
    return completed.returncode == 0 and not infra, output, infra


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(router_source / "modules" / module_id, destination)
    return destination / "SKILL.md"


def run_replicate(
    condition: str, replicate: int, *, config_path: Path, requested_model: str
) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate)
    parser = FeedbackParser()
    router = (
        DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml"))
        if condition == "pd-vista" else None
    )
    disclosed: list[str] = []
    accessed_after_disclosure: list[str] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    final_pass = False
    stop_reason = "max_turns"
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    process_env = tool_environment(base)
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
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=workspace, env=process_env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=MODEL_TURN_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True

            def decoded(value: str | bytes | None) -> str:
                if value is None:
                    return ""
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return value

            completed = subprocess.CompletedProcess(
                command, 124, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr)
            )
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
        verifier_pass, verifier_output, verifier_infra = run_verifier(
            workspace, verifier, base, turn
        )
        turns.append({
            "turn": turn, "return_code": completed.returncode,
            "model_turn_timeout": timed_out,
            "event_summary": summary, "verifier_pass": verifier_pass,
            "verifier_infrastructure_error": verifier_infra,
        })
        print(
            f"condition={condition} replicate={replicate} turn={turn} "
            f"requests={summary['requests']} tools={summary['tool_uses']} "
            f"accessed={accessed_now} verifier_pass={verifier_pass} "
            f"model_timeout={timed_out}", flush=True,
        )
        if verifier_pass:
            final_pass = True
            stop_reason = (
                "verifier_pass_after_model_timeout"
                if any(item["model_turn_timeout"] for item in turns)
                else "verifier_pass"
            )
            break
        if verifier_infra:
            stop_reason = "verifier_infrastructure_error"
            break
        if summary["errors"]:
            stop_reason = summary["errors"][-1]["category"]
            break

        trigger_opportunities += 1
        classification = parser.parse(
            FeedbackObservation(
                source=FeedbackSource.TEST_OUTPUT, text=verifier_output, exit_code=1
            ),
            trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}-public",
        )
        feedback_types = list(classification.trigger_names)
        if not feedback_types or feedback_types == ["unknown"]:
            feedback_types = ["command_failure"]
        newly_disclosed: list[str] = []
        diagnostic = public_diagnostic(verifier_output)
        if condition == "pd-vista":
            assert router is not None
            payload = json.dumps({
                "schema_version": 1, "kind": "vista_public_verifier_feedback",
                "outcome": "fail", "feedback_types": feedback_types,
                "summary": diagnostic,
            })
            routed = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}",
            )
            decision = router.route(routed, previous_modules=disclosed)
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(
                f"./modules/{module}/SKILL.md" for module in newly_disclosed
            ) + ". Read it before repairing."
        elif condition == "native":
            instruction = "Use the original modules already available under ./modules/."
        elif disclosed:
            instruction = "Use the already disclosed modules: " + ", ".join(disclosed) + "."
        else:
            instruction = "No task Skill is available; repair from the public failure category."
        prompt = (
            f"The public local check failed with {', '.join(feedback_types)}. "
            f"Public diagnostic:\n{diagnostic}\n{instruction} "
            "Continue in the same session, work only inside this directory, repair "
            "./Tokenizer.scala, and use ./compile.sh when useful."
        )

    result_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    if (workspace / "Tokenizer.scala").is_file():
        shutil.copy2(workspace / "Tokenizer.scala", result_dir / "Tokenizer.scala")
    usage = {
        key: sum(int(turn["event_summary"].get(key) or 0) for turn in turns)
        for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
    }
    clean_closure = int(
        condition == "pd-vista" and final_pass and disclosure_count > 0 and access_rounds > 0
    )
    quarantined = stop_reason in {
        "provider_policy_rejection", "api_error", "session_identity_changed",
        "verifier_infrastructure_error",
    }
    status = (
        "passed_with_clean_vista_feedback_loop" if clean_closure
        else "passed" if final_pass and condition != "pd-vista"
        else "passed_without_clean_vista_feedback_loop" if final_pass
        else f"quarantined_{stop_reason}" if quarantined else "failed_max_turns"
    )
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-001", "condition": condition, "replicate_id": replicate,
        "backend": "opencode-local-minimal-dependencies", "docker_used": False,
        "requested_provider_model": requested_model, "session_id": session_id,
        "turns": turns, "usage": usage,
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "disclosed_modules": disclosed,
        "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)),
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities,
            "routable_feedback": routable_feedback,
            "successful_disclosures": disclosure_count,
            "next_round_module_access": access_rounds,
            "clean_complete_closure": clean_closure,
        },
        "final_verifier_pass": final_pass, "stop_reason": stop_reason,
        "status": status, "workspace": str(workspace),
    }
    (result_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    launcher = freeze_launcher()
    readiness = dependency_readiness()
    if not readiness["ready"]:
        raise RuntimeError("task-001 local dependencies are not ready: " + json.dumps(readiness))
    if RESULT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse result root: {RESULT_ROOT}")
    RESULT_ROOT.mkdir(parents=True)
    (RESULT_ROOT / "readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    results: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        result = run_replicate(
            condition, replicate, config_path=config_path, requested_model=requested_model
        )
        results.append(result)
        if result["status"].startswith("quarantined_"):
            break
    condition_summaries = {}
    for condition in CONDITIONS:
        values = [result for result in results if result["condition"] == condition]
        condition_summaries[condition] = {
            "planned_trials": 3, "completed_trials": len(values),
            "verifier_passes": sum(result["final_verifier_pass"] for result in values),
            "usage": {
                key: sum(result["usage"][key] for result in values)
                for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")
            },
        }
    summary = {
        "schema_version": 1, "protocol": PROTOCOL, "task_id": "task-001",
        "conditions": list(CONDITIONS), "replicates_per_condition": 3,
        "planned_trials": 9, "completed_trials": len(results),
        "requested_provider_model": requested_model,
        "opencode": {
            "version": launcher.version, "launcher": launcher.launcher,
            "binary_sha256": launcher.binary_sha256,
        },
        "condition_summaries": condition_summaries,
        "results": [
            {
                "condition": result["condition"], "replicate_id": result["replicate_id"],
                "status": result["status"], "final_verifier_pass": result["final_verifier_pass"],
                "session_id": result["session_id"], "usage": result["usage"],
                "disclosed_modules": result["disclosed_modules"],
                "accessed_modules_after_disclosure": result["accessed_modules_after_disclosure"],
                "vista_metrics": result["vista_metrics"],
            }
            for result in results
        ],
    }
    (RESULT_ROOT / "group-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if len(results) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
