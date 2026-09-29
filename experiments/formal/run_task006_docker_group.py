"""Run the authorized task-006 Docker experiment with host OpenCode.

The BugSwarm image supplies the task repository and build runner.  OpenCode is
still invoked on the host with the current user configuration; only the public
task verifier/build is executed in an isolated linux/amd64 container.  Every
trial has a fresh container, workspace, verifier log directory, XDG state and
OpenCode session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
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
TASK_IDENTITY = REPO / "tasks" / "pilot" / "task-006" / "task.yaml"
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "fix-build-agentops"
NATIVE_SOURCE = TASK_SOURCE / "environment" / "skills"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-006"
ORIGINAL_VERIFIER = TASK_SOURCE / "verifier" / "test_outputs.py"
ORIGINAL_TEST_SCRIPT = TASK_SOURCE / "verifier" / "test.sh"
ORIGINAL_RUN_PASSED = TASK_SOURCE / "verifier" / "run_passed.sh"
MODULE_IDS = ("analyze-ci", "temporal-python-testing", "testing-python", "uv-package-manager")
REPO_ID = "AgentOps-AI/agentops"
IMAGE = "task6-agentops-24579293289-20260826"
BASE_IMAGE = "bugswarm/cached-images:AgentOps-AI-agentops-24579293289"
DOCKER_BIN = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
DOCKER_ENV_PATH = "/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"

RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task006-docker-local-3x3-minimaxm3-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
)
READINESS_RUN_ID = f"{RUN_PREFIX}-00-readiness"
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task006-docker-opencode-current-config-public-verifier-resumable-timeout-noskill-native-pdvista-3x3",
)
LOCAL_ROOT = Path(os.environ.get("VISTA_EXPERIMENT_ROOT", "/private/tmp/vista-task006-docker-experiments"))
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
AUDIT_ROOT = REPO / "outputs" / f"{RUN_PREFIX}-audit"
READINESS_RESULT_ROOT = REPO / "data" / "raw" / READINESS_RUN_ID
READINESS_AUDIT_ROOT = REPO / "outputs" / f"{READINESS_RUN_ID}-audit"

MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 420
VERIFIER_TIMEOUT_SECONDS = 900
MAX_RATE_LIMIT_RETRIES = 5
MAX_INFRA_RETRIES = 5
CONDITIONS = ("noskill", "native", "pd-vista")
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

COMMON_PROMPT = """Solve the isolated task-006 fix-build-agentops task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inspect the local repository. The task's /home/github/build maps to this workspace root: use ./failed/failed_reasons.txt and ./failed/AgentOps-AI/agentops. Write the required analysis, standard unified patch_*.diff files, and apply the patches to the repository. Use only local task materials and ordinary local tools; do not fetch hidden answers or use any benchmark platform. The controller runs the complete public BugSwarm verifier in an authorized isolated Docker image after each turn and may return a cleaned public diagnostic for repair."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT + " No task Skill is available in this condition; solve from task.md and the repository only.",
    "native": COMMON_PROMPT + " The complete original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
    "pd-vista": COMMON_PROMPT + " Read VISTA.md. Original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose at most one relevant module under ./modules/<module-id>/SKILL.md. Read any disclosed module before the next repair turn.",
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


def run_with_timeout(command: list[str], *, cwd: Path | None, env: dict[str, str], timeout: float) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a command in its own process group so timeout cleanup is complete."""
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=True,
    )
    timeout_state = {"triggered": False}

    def enforce_timeout() -> None:
        timeout_state["triggered"] = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # OpenCode may leave a detached child holding the JSONL pipe open.
        # Kill only commands that carry this exact isolated workspace path so
        # a timed-out turn cannot create a second session in the same trial.
        if cwd is not None:
            try:
                subprocess.run(["pkill", "-KILL", "-f", str(cwd.resolve())], capture_output=True, check=False)
            except OSError:
                pass

    # Keep a watchdog independent of pipe state.  OpenCode may detach a child
    # while retaining stdout; communicate(timeout=...) alone then cannot cleanly
    # finish even though the model turn has exceeded its limit.
    watchdog = threading.Timer(timeout, enforce_timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        stdout, stderr = process.communicate()
    finally:
        watchdog.cancel()
    if timeout_state["triggered"]:
        return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr), True
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr), False


def docker_command(args: list[str]) -> list[str]:
    return [str(DOCKER_BIN), *args]


def docker_run(args: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = DOCKER_ENV_PATH
    command = docker_command(args)
    try:
        return subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return _timeout_completed(command, exc)


def docker_text(args: list[str], *, timeout: float = 120) -> str:
    completed = docker_run(args, timeout=timeout)
    return ((completed.stdout or "") + (completed.stderr or "")).strip()


def tool_environment(base: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(REPO / ".venv" / "bin") + os.pathsep + env.get("PATH", "")
    env["OPENCODE_CONFIG"] = str(Path(os.environ.get("VISTA_OPENCODE_CONFIG", Path.home() / ".config/opencode/opencode.json")).expanduser().resolve())
    env.pop("OPENCODE_CONFIG_DIR", None)
    env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")
    return env


def active_process_check() -> dict[str, Any]:
    patterns = re.compile(r"opencode|run_task00[23457]", re.IGNORECASE)
    try:
        completed = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, check=False)
        lines = []
        for line in (completed.stdout or "").splitlines():
            if not patterns.search(line):
                continue
            if "run_task006_docker_group" in line or "-m experiments.formal.run_task006_docker_group" in line:
                continue
            lines.append(line.strip())
        return {"available": True, "active_matching_processes": lines, "clear": not lines}
    except Exception as exc:
        return {"available": False, "active_matching_processes": [], "clear": True, "error": str(exc)}


def wait_for_shared_opencode_clear() -> dict[str, Any]:
    checks = []
    for index in range(20):
        check = active_process_check()
        check["poll_index"] = index
        checks.append(check)
        if check.get("clear", True):
            return {"clear": True, "checks": checks}
        if index < 19:
            time.sleep(30)
    return {"clear": False, "checks": checks, "error": "other task OpenCode processes remained active during the bounded wait"}


def identity_readiness() -> dict[str, Any]:
    checks: dict[str, Any] = {
        "task_identity_file": TASK_IDENTITY.is_file(),
        "task_instruction": (TASK_SOURCE / "task.md").is_file(),
        "original_verifier": ORIGINAL_VERIFIER.is_file(),
        "original_verifier_script": ORIGINAL_TEST_SCRIPT.is_file(),
        "original_runner": ORIGINAL_RUN_PASSED.is_file(),
        "native_modules": {module: (NATIVE_SOURCE / module / "SKILL.md").is_file() for module in MODULE_IDS},
        "vista_root": (VISTA_SOURCE / "SKILL.md").is_file(),
        "vista_manifest": (VISTA_SOURCE / "manifest.yaml").is_file(),
        "vista_source": (VISTA_SOURCE / "source.yaml").is_file(),
        "docker_used": True,
        "skillbench_execution_route_used": False,
        "oracle_used": False,
        "public_diagnostic_cleaning": True,
        "task_source": str(TASK_SOURCE),
        "verifier_entry": str(ORIGINAL_VERIFIER),
        "docker_image": IMAGE,
        "docker_base_image": BASE_IMAGE,
        "docker_platform": "linux/amd64",
        "asset_checksums": {},
    }
    try:
        import yaml

        identity = yaml.safe_load(TASK_IDENTITY.read_text(encoding="utf-8"))
        source = identity.get("source", {}) if isinstance(identity, dict) else {}
        checks["task_identity_id"] = identity.get("task_id") if isinstance(identity, dict) else None
        checks["task_identity_resolved"] = bool(
            isinstance(identity, dict)
            and identity.get("task_id") == "task-006"
            and source.get("benchmark_task_id") == "fix-build-agentops"
            and source.get("definition_path") == "vendor/skillsbench/tasks/fix-build-agentops"
        )
    except Exception as exc:
        checks["task_identity_resolved"] = False
        checks["task_identity_error"] = f"{type(exc).__name__}: {exc}"
    for path in (TASK_IDENTITY, TASK_SOURCE / "task.md", ORIGINAL_VERIFIER, ORIGINAL_TEST_SCRIPT, ORIGINAL_RUN_PASSED):
        if path.is_file():
            checks["asset_checksums"][str(path.relative_to(REPO))] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    checks["native_module_checksums"] = {}
    for module in MODULE_IDS:
        module_path = NATIVE_SOURCE / module / "SKILL.md"
        if module_path.is_file():
            checks["native_module_checksums"][module] = {"bytes": module_path.stat().st_size, "sha256": sha256_file(module_path)}
    return checks


def ensure_image() -> dict[str, Any]:
    inspect = docker_run(["image", "inspect", IMAGE], timeout=120)
    built = inspect.returncode == 0
    build_output = ""
    if not built:
        built_result = docker_run([
            "build", "--platform", "linux/amd64", "--build-arg", f"bugswarm_image_tag={BASE_IMAGE.split(':', 1)[1]}",
            "--tag", IMAGE, str(TASK_SOURCE / "environment"),
        ], timeout=1800)
        build_output = (built_result.stdout or "") + (built_result.stderr or "")
        built = built_result.returncode == 0
        inspect = docker_run(["image", "inspect", IMAGE], timeout=120)
    details: dict[str, Any] = {"image": IMAGE, "base_image": BASE_IMAGE, "available": built, "build_output_tail": build_output[-3000:]}
    if inspect.returncode == 0:
        try:
            payload = json.loads(inspect.stdout)[0]
            details.update({"id": payload.get("Id"), "os": payload.get("Os"), "architecture": payload.get("Architecture"), "repo_digests": payload.get("RepoDigests", [])})
        except Exception as exc:
            details["inspect_parse_error"] = f"{type(exc).__name__}: {exc}"
    return details


def probe_container(image: str) -> dict[str, Any]:
    name = f"task6_readiness_probe_{os.getpid()}"
    created = docker_run(["create", "--platform", "linux/amd64", "--name", name, image, "tail", "-f", "/dev/null"], timeout=120)
    report: dict[str, Any] = {"container_name": name, "created": created.returncode == 0}
    if created.returncode != 0:
        report["create_output"] = (created.stdout or "") + (created.stderr or "")
        return report
    try:
        started = docker_run(["start", name], timeout=120)
        report["started"] = started.returncode == 0
        checks = {
            "uv": ["exec", name, "sh", "-lc", "command -v uv && uv --version"],
            "node": ["exec", name, "sh", "-lc", "node --version"],
            "repo": ["exec", name, "sh", "-lc", f"test -d /home/github/build/failed/{REPO_ID} && test -f /home/github/build/failed/{REPO_ID}/pyproject.toml"],
            "runner": ["exec", name, "sh", "-lc", "test -f /home/github/24653510459/event.json && test -d /home/github/24653510459/actions/actions-setup-python@v2"],
            "home_github": ["exec", name, "sh", "-lc", "test -x /home/github"],
        }
        for key, args in checks.items():
            result = docker_run(args, timeout=120)
            report[key] = {"ok": result.returncode == 0, "output": ((result.stdout or "") + (result.stderr or "")).strip()[-1000:]}
        inject = docker_run(["exec", "-u", "0", name, "mkdir", "-p", "/verifier", "/tests", "/logs/verifier"], timeout=120)
        report["verifier_dirs"] = inject.returncode == 0
        for source, target in ((ORIGINAL_TEST_SCRIPT, "/verifier/test.sh"), (ORIGINAL_VERIFIER, "/verifier/test_outputs.py"), (ORIGINAL_RUN_PASSED, "/tests/run_passed.sh")):
            copied = docker_run(["cp", str(source), f"{name}:{target}"], timeout=120)
            report[f"copied_{source.name}"] = copied.returncode == 0
        collect = docker_run(["exec", "-u", "0", "-e", "REPO_ID=" + REPO_ID, "-w", "/home/github/build", name, "bash", "-lc", "uv init --python 3.10 >/tmp/task6-uv-init.log 2>&1 || true; for i in 1 2 3 4 5; do uv add pytest==8.4.1 pytest-json-ctrf==0.3.5 unidiff==0.7.5 >/tmp/task6-uv-add.log 2>&1 && break; sleep 10; done; .venv/bin/python -m pytest --collect-only -q -c /dev/null --confcutdir=/tests /verifier/test_outputs.py"], timeout=600)
        report["verifier_collect"] = {"ok": collect.returncode == 0, "output": ((collect.stdout or "") + (collect.stderr or "")).strip()[-3000:]}
    finally:
        docker_run(["rm", "-f", name], timeout=120)
    report["ready"] = bool(report.get("started")) and all(bool(item.get("ok")) for key, item in report.items() if isinstance(item, dict) and key not in {"verifier_collect"}) and bool(report.get("verifier_collect", {}).get("ok"))
    return report


def readiness_report(config_path: Path, requested_model: str, launcher: Any, image: dict[str, Any], concurrency: dict[str, Any]) -> dict[str, Any]:
    checks = identity_readiness()
    checks.update({"image": image, "container_probe": probe_container(IMAGE) if image.get("available") else {"ready": False, "skipped": True}})
    checks["opencode"] = {"config_path": str(config_path), "requested_provider_model": requested_model, "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256}
    checks["concurrency"] = concurrency
    checks["timeouts"] = {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS}
    required = ("task_identity_file", "task_instruction", "original_verifier", "original_verifier_script", "original_runner", "task_identity_resolved", "vista_root", "vista_manifest", "vista_source")
    checks["ready"] = bool(image.get("available")) and all(bool(checks.get(item)) for item in required) and all(checks["native_modules"].values()) and bool(checks.get("container_probe", {}).get("ready"))
    checks["readiness_scope"] = "Container asset and verifier collection preflight; full public verifier is executed after every formal OpenCode turn."
    return checks


def public_diagnostic(output: str, *, limit: int = 9000) -> str:
    selected: list[str] = []
    markers = ("failed", "failure", "error", "missing", "assert", "traceback", "exception", "no patch", "does not exist", "build failed", "tox")
    for raw in output.splitlines():
        folded = raw.casefold()
        if not any(marker in folded for marker in markers):
            continue
        line = re.sub(r"/Users/[^\s:]+", "<local-path>", raw).strip()
        line = re.sub(r"/private/tmp/[^\s:]+", "<local-path>", line)
        line = re.sub(r"/home/github/[^\s:]+", "<container-path>", line)
        if line and line not in selected:
            selected.append(line)
    return ("\n".join(selected) or "The public BugSwarm verifier failed; inspect failed_reasons.txt, patch_*.diff, and the repository, then repair the build.")[:limit]


def public_feedback_types(diagnostic: str) -> list[str]:
    folded = diagnostic.casefold()
    if "timeout" in folded or "timed out" in folded or "deadline" in folded:
        return ["timeout"]
    if "no module named" in folded or "modulenotfounderror" in folded or "dependency" in folded or "package" in folded and "not found" in folded:
        return ["dependency_error"]
    if "syntaxerror" in folded or "compile" in folded:
        return ["compilation_error"]
    if "traceback" in folded or "exception" in folded or "runtimeerror" in folded:
        return ["runtime_error"]
    if "failed" in folded or "assert" in folded or "no patch" in folded or "does not exist" in folded:
        return ["test_failure", "assertion_error"]
    return ["command_failure"]


def verifier_infrastructure_failure(output: str, *, reward: str) -> bool:
    folded = output.casefold()
    if not reward.strip():
        return True
    return any(marker in folded for marker in (
        "uv: command not found", "no such file or directory: /verifier",
        "could not find the file /verifier", "error while loading conftest",
        "unrecognized arguments", "internalerror", "permission denied",
        "no tests ran", "read-only file system", "failed to fetch",
        "request failed after", "no module named 'ctrf'", 'no module named "ctrf"',
        "connection refused", "connection reset", "network is unreachable",
        "temporary failure in name resolution", "could not resolve host",
    ))


def rate_limit_reason(summary: dict[str, Any], text: str) -> str | None:
    for error in summary.get("errors", []):
        if str(error.get("status_code")) == "429":
            return "http_429_rate_limit"
    folded = text.casefold()
    markers = (r"\bhttp\s*429\b", r"\bstatus[_ ]?code\s*[:=]?\s*429\b", r"\brate_limit_error\b", r"\btoo many requests\b", r"\bquota exceeded\b", r"已达到[^\n]{0,60}(?:配额|速率|用量)限制", r"\b(?:temporarily overloaded|service overloaded)\b")
    if any(re.search(marker, folded, re.IGNORECASE) for marker in markers):
        return "zero_request_shared_limit" if summary.get("requests", 0) == 0 else "rate_limit_response"
    return None


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    source = router_source / "modules" / module_id
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(source, destination)
    return destination / "SKILL.md"


def prepare_trial(condition: str, replicate: int, attempt: int) -> tuple[Path, Path, Path, Path, str]:
    run_id = f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}-attempt-{attempt:02d}"
    base = LOCAL_ROOT / run_id
    if base.exists():
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace = base / "workspace"
    verifier = base / "verifier"
    router_source = base / "router-source"
    workspace.mkdir(parents=True)
    verifier.mkdir()
    router_source.mkdir()
    container = f"task6_{RUN_PREFIX[-18:]}_{condition}_{replicate:02d}_{attempt:02d}".replace("-", "_")
    created = docker_run(["create", "--platform", "linux/amd64", "--name", container, IMAGE, "tail", "-f", "/dev/null"], timeout=120)
    if created.returncode != 0:
        raise RuntimeError("docker create failed: " + (created.stdout or "") + (created.stderr or ""))
    started = docker_run(["start", container], timeout=120)
    if started.returncode != 0:
        docker_run(["rm", "-f", container], timeout=120)
        raise RuntimeError("docker start failed: " + (started.stdout or "") + (started.stderr or ""))
    try:
        (workspace / "failed" / REPO_ID).mkdir(parents=True)
        copied = docker_run(["cp", f"{container}:/home/github/build/failed/{REPO_ID}", str(workspace / "failed" / "AgentOps-AI")], timeout=300)
        if copied.returncode != 0:
            raise RuntimeError("docker cp source failed: " + (copied.stdout or "") + (copied.stderr or ""))
        # BugSwarm's public runner hook expects the cached dependency server
        # under failed/cacher so that test_build_success can copy it to
        # passed/cacher.  It is task runtime data, not agent-visible guidance;
        # keep one private copy inside this trial workspace and resync it into
        # each fresh verifier container.
        cacher = docker_run(["cp", f"{container}:/home/github/build/failed/cacher", str(workspace / "failed")], timeout=300)
        if cacher.returncode != 0:
            raise RuntimeError("docker cp BugSwarm cacher failed: " + (cacher.stdout or "") + (cacher.stderr or ""))
        shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
        (workspace / "modules").mkdir(exist_ok=True)
        if condition == "native":
            for module_id in MODULE_IDS:
                shutil.copytree(NATIVE_SOURCE / module_id, workspace / "modules" / module_id)
        elif condition == "pd-vista":
            shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
            shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
            for module_id in MODULE_IDS:
                shutil.copytree(NATIVE_SOURCE / module_id, router_source / "modules" / module_id)
        docker_run(["exec", "-u", "0", container, "mkdir", "-p", "/verifier", "/tests", "/logs/verifier"], timeout=120)
        for source, target in ((ORIGINAL_TEST_SCRIPT, "/verifier/test.sh"), (ORIGINAL_VERIFIER, "/verifier/test_outputs.py"), (ORIGINAL_RUN_PASSED, "/tests/run_passed.sh")):
            copied = docker_run(["cp", str(source), f"{container}:{target}"], timeout=120)
            if copied.returncode != 0:
                raise RuntimeError("docker cp verifier failed: " + (copied.stdout or "") + (copied.stderr or ""))
        manifest = {
            "run_id": run_id, "condition": condition, "replicate_id": replicate, "attempt": attempt,
            "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
            "workspace": str(workspace), "verifier": str(ORIGINAL_VERIFIER), "docker_used": True,
            "docker_image": IMAGE, "docker_container": container, "skillbench_execution_route_used": False,
        }
        (base / "trial-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return base, workspace, verifier, router_source, container
    except Exception:
        docker_run(["rm", "-f", container], timeout=120)
        raise


def sync_to_container(workspace: Path, container: str) -> None:
    clean = docker_run(["exec", "-u", "0", container, "sh", "-lc", "find /home/github/build/failed -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +"], timeout=120)
    if clean.returncode != 0:
        raise RuntimeError("failed to reset container failed tree: " + (clean.stdout or "") + (clean.stderr or ""))
    copied = docker_run(["cp", str(workspace / "failed") + "/.", f"{container}:/home/github/build/failed/"], timeout=300)
    if copied.returncode != 0:
        raise RuntimeError("failed to sync workspace into container: " + (copied.stdout or "") + (copied.stderr or ""))


def run_container_verifier(workspace: Path, verifier: Path, turn: int, trial_name: str, existing_container: str | None = None) -> tuple[bool, str, bool, bool]:
    # run_passed.sh mutates /home/github/workflow, /home/github/24653510459/steps,
    # and the passed tree.  The container is fresh per attempt, then reused
    # within that attempt so uv/tox caches are retained between public checks.
    owns_container = existing_container is None
    container = existing_container or f"task6v_{trial_name[-80:]}_t{turn}".replace("-", "_")
    if owns_container:
        created = docker_run(["create", "--platform", "linux/amd64", "--name", container, IMAGE, "tail", "-f", "/dev/null"], timeout=120)
        if created.returncode != 0:
            output = "Verifier container create failed.\n" + (created.stdout or "") + (created.stderr or "")
            (verifier / f"verifier-turn-{turn}.txt").write_text(output + "\n[reward=missing]\n", encoding="utf-8")
            return False, output, True, False
    try:
        if owns_container:
            started = docker_run(["start", container], timeout=120)
            if started.returncode != 0:
                output = "Verifier container start failed.\n" + (started.stdout or "") + (started.stderr or "")
                (verifier / f"verifier-turn-{turn}.txt").write_text(output + "\n[reward=missing]\n", encoding="utf-8")
                return False, output, True, False
        mkdir = docker_run(["exec", "-u", "0", container, "mkdir", "-p", "/verifier", "/tests", "/logs/verifier"], timeout=120)
        if mkdir.returncode != 0:
            output = "Verifier container setup failed.\n" + (mkdir.stdout or "") + (mkdir.stderr or "")
            (verifier / f"verifier-turn-{turn}.txt").write_text(output + "\n[reward=missing]\n", encoding="utf-8")
            return False, output, True, False
        for source, target in ((ORIGINAL_TEST_SCRIPT, "/verifier/test.sh"), (ORIGINAL_VERIFIER, "/verifier/test_outputs.py"), (ORIGINAL_RUN_PASSED, "/tests/run_passed.sh")):
            copied = docker_run(["cp", str(source), f"{container}:{target}"], timeout=120)
            if copied.returncode != 0:
                output = "Verifier asset injection failed.\n" + (copied.stdout or "") + (copied.stderr or "")
                (verifier / f"verifier-turn-{turn}.txt").write_text(output + "\n[reward=missing]\n", encoding="utf-8")
                return False, output, True, False
        sync_to_container(workspace, container)
        command = docker_command(["exec", "-u", "0", "-e", "REPO_ID=" + REPO_ID, "-w", "/home/github/build", container, "bash", "/verifier/test.sh"])
        completed, timed_out = run_with_timeout(command, cwd=None, env={**os.environ, "PATH": DOCKER_ENV_PATH}, timeout=VERIFIER_TIMEOUT_SECONDS)
        if timed_out:
            docker_run(["exec", "-u", "0", container, "pkill", "-TERM", "-f", "tox|run_passed.sh|test.sh"], timeout=30)
        output = (completed.stdout or "") + (completed.stderr or "")
        reward_result = docker_run(["exec", "-u", "0", container, "sh", "-lc", "cat /logs/verifier/reward.txt 2>/dev/null || true"], timeout=30)
        reward = (reward_result.stdout or "").strip()
        (verifier / f"verifier-turn-{turn}.txt").write_text(output + f"\n[reward={reward or 'missing'}]\n", encoding="utf-8")
        infra = timed_out or verifier_infrastructure_failure(output, reward=reward)
        return reward == "1" and not infra, output, infra, timed_out
    finally:
        if owns_container:
            docker_run(["rm", "-f", container], timeout=120)


def event_accessed(summary: dict[str, Any], disclosed: list[str]) -> list[str]:
    return [module for module in summary.get("accessed_modules", []) if module in disclosed]


def run_attempt(condition: str, replicate: int, attempt: int, *, requested_model: str) -> dict[str, Any]:
    base, workspace, verifier, router_source, container = prepare_trial(condition, replicate, attempt)
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
    try:
        for turn in range(1, MAX_TURNS + 1):
            command = command_for_turn(workspace=workspace, prompt=prompt, session_id=session_id, model=requested_model)
            model_timed_out = False
            completed, model_timed_out = run_with_timeout(command, cwd=workspace, env=process_env, timeout=MODEL_TURN_TIMEOUT_SECONDS)
            stdout, stderr = completed.stdout or "", completed.stderr or ""
            (base / f"turn-{turn}.jsonl").write_text(stdout, encoding="utf-8")
            (base / f"turn-{turn}.stderr.txt").write_text(stderr, encoding="utf-8")
            summary = event_summary(parse_events(stdout), MODULE_IDS)
            if summary.get("session_ids"):
                observed = summary["session_ids"][0]
                if session_id is not None and observed != session_id:
                    stop_reason = "session_identity_changed"
                    break
                session_id = observed
            accessed_now = event_accessed(summary, disclosed)
            accessed_after_disclosure.extend(accessed_now)
            for event in disclosure_events:
                if event["next_round_turn"] == turn and not event["accessed_next_round"] and event["module_id"] in accessed_now:
                    event["accessed_next_round"] = True
                    access_rounds += 1
            limit_reason = rate_limit_reason(summary, stdout + "\n" + stderr)
            if limit_reason:
                stop_reason = limit_reason
                turns.append({"turn": turn, "prompt": prompt, "return_code": completed.returncode, "model_turn_timeout": model_timed_out, "event_summary": summary, "accessed_modules_this_turn": accessed_now, "verifier_pass": False, "verifier_infrastructure_error": False, "verifier_timeout": False, "rate_limit_excluded": True, "rate_limit_reason": limit_reason, "public_feedback_types": [], "public_diagnostic": "", "newly_disclosed_modules": []})
                break
            verifier_pass, verifier_output, verifier_infra, verifier_timed_out = run_container_verifier(workspace, verifier, turn, base.name, existing_container=container)
            turn_record: dict[str, Any] = {"turn": turn, "prompt": prompt, "return_code": completed.returncode, "model_turn_timeout": model_timed_out, "event_summary": summary, "accessed_modules_this_turn": accessed_now, "verifier_pass": verifier_pass, "verifier_infrastructure_error": verifier_infra, "verifier_timeout": verifier_timed_out, "rate_limit_excluded": False, "public_feedback_types": [], "public_diagnostic": "", "newly_disclosed_modules": []}
            turns.append(turn_record)
            print(f"condition={condition} replicate={replicate} attempt={attempt} turn={turn} requests={summary.get('requests', 0)} tools={summary.get('tool_uses', 0)} accessed={accessed_now} verifier_pass={verifier_pass} model_timeout={model_timed_out} verifier_timeout={verifier_timed_out}", flush=True)
            if verifier_pass:
                final_pass = True
                stop_reason = "verifier_pass_after_model_timeout" if any(item["model_turn_timeout"] for item in turns) else "verifier_pass"
                break
            if verifier_infra:
                stop_reason = "verifier_timeout" if verifier_timed_out else "verifier_infrastructure_error"
                break
            if summary.get("errors"):
                stop_reason = summary["errors"][-1].get("category", "api_error")
                break
            trigger_opportunities += 1
            diagnostic = public_diagnostic(verifier_output)
            feedback_types = public_feedback_types(diagnostic)
            turn_record["public_feedback_types"] = feedback_types
            turn_record["public_diagnostic"] = diagnostic
            newly_disclosed: list[str] = []
            if condition == "pd-vista":
                assert router is not None
                payload = json.dumps({"schema_version": 1, "kind": "vista_public_verifier_feedback", "outcome": "fail", "feedback_types": feedback_types, "summary": diagnostic}, ensure_ascii=False)
                classified = parser.parse(FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1), trial_id=f"{RUN_PREFIX}-{condition}-{replicate:02d}")
                decision = router.route(classified, previous_modules=disclosed)
                if decision.modules or decision.skipped_previous_modules:
                    routable_feedback += 1
                for module in router.disclose(decision):
                    copy_disclosed_module(router_source, workspace, module.module_id)
                    disclosed.append(module.module_id)
                    newly_disclosed.append(module.module_id)
                    disclosure_count += 1
                    disclosure_events.append({"trigger_turn": turn, "next_round_turn": turn + 1, "module_id": module.module_id, "decision_reason": decision.reason, "accessed_next_round": False})
            turn_record["newly_disclosed_modules"] = newly_disclosed
            if newly_disclosed:
                instruction = "VISTA disclosed " + ", ".join(f"./modules/{module}/SKILL.md" for module in newly_disclosed) + ". Read it before repairing."
            elif condition == "native":
                instruction = "Use the original modules already available under ./modules/."
            elif disclosed:
                instruction = "Use the already disclosed modules: " + ", ".join(disclosed) + "."
            else:
                instruction = "No task Skill is available; repair from the public diagnostic and local files."
            prompt = f"A public local check failed with {', '.join(feedback_types)}. Public diagnostic:\n{diagnostic}\n{instruction} Continue in the same OpenCode session, work only inside this directory, write valid analysis and unified patch files, apply the fix, and let the controller rerun the complete public BugSwarm verifier."
    finally:
        docker_run(["rm", "-f", container], timeout=120)

    actual_models = list(dict.fromkeys(model for turn in turns for model in turn["event_summary"].get("actual_model_ids", [])))
    actual_providers = list(dict.fromkeys(provider for turn in turns for provider in turn["event_summary"].get("provider_ids", [])))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model} or actual_providers and set(actual_providers) != {expected_provider}:
        stop_reason = "model_identity_changed"
        final_pass = False
    clean_closure = int(condition == "pd-vista" and final_pass and disclosure_count > 0 and disclosure_count == access_rounds)
    usage = {key: sum(int(turn["event_summary"].get(key) or 0) for turn in turns) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}
    rate_quarantined = stop_reason in {"http_429_rate_limit", "rate_limit_response", "zero_request_shared_limit"}
    quarantined = stop_reason in {"provider_policy_rejection", "api_error", "session_identity_changed", "verifier_infrastructure_error", "verifier_timeout", "model_identity_changed"}
    status = "quarantined_rate_limit" if rate_quarantined else "passed_with_clean_vista_feedback_loop" if clean_closure else "passed" if final_pass and condition != "pd-vista" else "passed_without_clean_vista_feedback_loop" if final_pass else f"quarantined_{stop_reason}" if quarantined else "failed_max_turns"
    result = {
        "schema_version": 1, "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}-attempt-{attempt:02d}", "task_id": "task-006", "benchmark_task_id": "fix-build-agentops", "condition": condition, "replicate_id": replicate, "attempt": attempt, "backend": "opencode-local-current-config", "docker_used": True, "docker_image": IMAGE, "docker_platform": "linux/amd64", "skillbench_execution_route_used": False, "oracle_used": False, "requested_provider_model": requested_model, "expected_actual_model": expected_model, "actual_model_ids": actual_models, "expected_provider": expected_provider, "actual_provider_ids": actual_providers, "initially_available_modules": list(MODULE_IDS) if condition == "native" else [], "usage": usage, "session_id": session_id, "turns": turns, "disclosed_modules": disclosed, "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)), "disclosure_events": disclosure_events, "vista_metrics": {"trigger_opportunities": trigger_opportunities, "routable_feedback": routable_feedback, "successful_disclosures": disclosure_count, "next_round_module_access": access_rounds, "clean_complete_closure": clean_closure}, "final_verifier_pass": final_pass, "stop_reason": stop_reason, "status": status, "workspace": str(workspace), "verifier": str(ORIGINAL_VERIFIER), "result_directory": str(base), "container_lifecycle": "fresh-container-per-attempt; reused within attempt; removed after audit artifacts were written",
    }
    (base / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def save_formal_result(result: dict[str, Any]) -> dict[str, Any]:
    target = RESULT_ROOT / result["condition"] / f"replicate-{int(result['replicate_id']):02d}"
    target.mkdir(parents=True, exist_ok=False)
    base = Path(result["result_directory"])
    for artifact in sorted(base.glob("turn-*.*")):
        shutil.copy2(artifact, target / artifact.name)
    for artifact in sorted((base / "verifier").glob("verifier-turn-*.txt")):
        shutil.copy2(artifact, target / artifact.name)
    shutil.copy2(base / "trial-manifest.json", target / "trial-manifest.json")
    result["result_directory"] = str(target)
    (target / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def save_excluded_attempt(result: dict[str, Any]) -> dict[str, Any]:
    target = RESULT_ROOT / "excluded-attempts" / result["condition"] / f"replicate-{int(result['replicate_id']):02d}-attempt-{int(result['attempt']):02d}"
    target.mkdir(parents=True, exist_ok=False)
    base = Path(result["result_directory"])
    for artifact in sorted(base.glob("turn-*.*")):
        shutil.copy2(artifact, target / artifact.name)
    for artifact in sorted((base / "verifier").glob("verifier-turn-*.txt")):
        shutil.copy2(artifact, target / artifact.name)
    result["excluded_from_formal_statistics"] = True
    result["result_directory"] = str(target)
    (target / "excluded-attempt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def audit_db(db_path: Path, expected_model: str, expected_provider: str) -> dict[str, Any]:
    audit: dict[str, Any] = {"path": str(db_path), "exists": db_path.is_file(), "sessions": [], "db_read_error": None}
    if not db_path.is_file():
        return audit
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute("select id,directory,version,cost,tokens_input,tokens_output,tokens_reasoning,tokens_cache_read,tokens_cache_write,model from session order by time_created").fetchall()
        for row in rows:
            model_value = json.loads(row[9]) if row[9] else {}
            audit["sessions"].append({"session_id": row[0], "directory": row[1], "opencode_version": row[2], "recorded_cost_usd": row[3], "tokens_input": row[4], "tokens_output": row[5], "tokens_reasoning": row[6], "tokens_cache_read": row[7], "tokens_cache_write": row[8], "model": model_value, "model_matches": model_value.get("id") == expected_model, "provider_matches": model_value.get("providerID") == expected_provider})
        audit["session_count"] = len(rows)
        audit["unique_session_ids"] = len({row[0] for row in rows})
        audit["provider_model_match"] = bool(rows) and all(json.loads(row[9]).get("id") == expected_model and json.loads(row[9]).get("providerID") == expected_provider for row in rows)
        con.close()
    except Exception as exc:
        audit["db_read_error"] = f"{type(exc).__name__}: {exc}"
    return audit


def posthoc_audit(results: list[dict[str, Any]], excluded_attempts: list[dict[str, Any]], requested_model: str) -> dict[str, Any]:
    expected_provider, expected_model = requested_model.split("/", 1)
    for result in results:
        base = Path(result["workspace"]).parent
        db_audits = [audit_db(db, expected_model, expected_provider) for db in sorted(base.rglob("opencode.db"))]
        result["opencode_db_audit"] = db_audits
        result["db_session_count"] = sum(item.get("session_count", 0) for item in db_audits)
        result["db_recorded_cost_usd"] = sum(float(session.get("recorded_cost_usd") or 0) for item in db_audits for session in item.get("sessions", []))
        result["db_tokens"] = {key: sum(int(session.get(key) or 0) for item in db_audits for session in item.get("sessions", [])) for key in ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")}
    sessions = [session for result in results for audit in result.get("opencode_db_audit", []) for session in audit.get("sessions", [])]
    audit = {"schema_version": 1, "run_id": RUN_PREFIX, "task_id": "task-006", "benchmark_task_id": "fix-build-agentops", "formal_results": results, "excluded_attempts": excluded_attempts, "formal_trial_count": len(results), "formal_result_files": len(results), "unique_formal_sessions": len({item["session_id"] for item in sessions}), "formal_sessions_with_expected_provider_model": bool(sessions) and all(item["model_matches"] and item["provider_matches"] for item in sessions), "docker_used": True, "docker_image": IMAGE, "skillbench_execution_route_used": False, "oracle_used": False, "evidence": "Read-only sqlite queries against each isolated OpenCode opencode.db plus raw turn JSONL and final Docker Verifier logs.", "excluded_note": "All readiness batches and shared 429/zero-request attempts are retained outside formal statistics; excluded attempts are listed explicitly."}
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    (AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def write_readiness(readiness: dict[str, Any]) -> None:
    READINESS_RESULT_ROOT.mkdir(parents=True, exist_ok=False)
    READINESS_AUDIT_ROOT.mkdir(parents=True, exist_ok=False)
    (READINESS_RESULT_ROOT / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"schema_version": 1, "run_id": READINESS_RUN_ID, "task_id": "task-006", "benchmark_task_id": "fix-build-agentops", "status": "ready_for_formal" if readiness.get("ready") else "quarantined_readiness", "model_requests": 0, "docker_used": True, "skillbench_execution_route_used": False, "readiness": readiness}
    (READINESS_RESULT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (READINESS_AUDIT_ROOT / "posthoc-audit.json").write_text(json.dumps({"schema_version": 1, "run_id": READINESS_RUN_ID, "diagnostic_batch": True, "excluded_from_formal_statistics": True, "reason": "Docker image, task assets, runner and complete verifier collection readiness; no OpenCode request.", "readiness": readiness}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"current OpenCode model requires {key_env}")
    launcher = freeze_launcher()
    if RESULT_ROOT.exists() or AUDIT_ROOT.exists() or READINESS_RESULT_ROOT.exists() or READINESS_AUDIT_ROOT.exists():
        raise RuntimeError(f"refusing to reuse task6 output directory: {RESULT_ROOT} / {READINESS_RESULT_ROOT}")
    image = ensure_image()
    concurrency = wait_for_shared_opencode_clear()
    readiness = readiness_report(config_path, requested_model, launcher, image, concurrency)
    write_readiness(readiness)
    if not readiness["ready"]:
        print(f"task6 readiness failed; diagnostic batch={READINESS_RUN_ID}", flush=True)
        return 2
    if not concurrency.get("clear", False):
        raise RuntimeError("shared task OpenCode processes remained active before formal start")
    RESULT_ROOT.mkdir(parents=True, exist_ok=False)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=False)
    formal_results: list[dict[str, Any]] = []
    excluded_attempts: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        latest: dict[str, Any] | None = None
        for attempt in range(1, max(MAX_RATE_LIMIT_RETRIES, MAX_INFRA_RETRIES) + 2):
            latest = run_attempt(condition, replicate, attempt, requested_model=requested_model)
            status = str(latest.get("status", ""))
            if status.startswith("quarantined_"):
                excluded = save_excluded_attempt(latest)
                reason = "shared provider 429/zero-request limit" if status == "quarantined_rate_limit" else "isolated model/session/verifier infrastructure fault"
                excluded_attempts.append({"condition": condition, "replicate_id": replicate, "attempt": attempt, "status": latest["status"], "stop_reason": latest["stop_reason"], "session_id": latest["session_id"], "result_directory": excluded["result_directory"], "reason": reason + "; excluded and retried serially with a fresh container, XDG state and OpenCode session"})
                retry_limit = MAX_RATE_LIMIT_RETRIES if status == "quarantined_rate_limit" else MAX_INFRA_RETRIES
                if attempt <= retry_limit:
                    backoff_seconds = min(300, 30 * (2 ** min(attempt - 1, 3)))
                    print(f"excluded {status} for {condition} replicate={replicate}; backing off {backoff_seconds}s before retry", flush=True)
                    for _ in range(backoff_seconds // 30):
                        time.sleep(30)
                    continue
            break
        if latest is None:
            raise RuntimeError(f"no result for {condition} replicate {replicate}")
        formal_results.append(save_formal_result(latest))
    formal_included = [item for item in formal_results if not str(item.get("status", "")).startswith("quarantined_")]
    condition_summaries = {}
    for condition in CONDITIONS:
        values = [item for item in formal_included if item["condition"] == condition]
        condition_summaries[condition] = {"planned_trials": 3, "formal_trials": len(values), "result_files": len(values), "quarantined_trials": sum(str(item.get("status", "")).startswith("quarantined_") for item in values), "verifier_passes": sum(bool(item.get("final_verifier_pass")) for item in values), "usage": {key: sum(int(item.get("usage", {}).get(key) or 0) for item in values) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}}
    summary = {"schema_version": 1, "run_id": RUN_PREFIX, "protocol": PROTOCOL, "task_id": "task-006", "benchmark_task_id": "fix-build-agentops", "conditions": list(CONDITIONS), "replicates_per_condition": 3, "planned_trials": 9, "completed_trials": len(formal_results), "formal_included_trials": len(formal_included), "schedule": [{"replicate_id": rep, "condition": cond} for rep, cond in SCHEDULE], "requested_provider_model": requested_model, "opencode": {"config_path": str(config_path), "launcher": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256}, "docker": {"used": True, "image": IMAGE, "base_image": BASE_IMAGE, "platform": "linux/amd64", "container_per_attempt": True, "reuse_within_attempt": True}, "skillbench_execution_route_used": False, "oracle_used": False, "timeouts": {"max_turns": MAX_TURNS, "model_turn_timeout_seconds": MODEL_TURN_TIMEOUT_SECONDS, "verifier_timeout_seconds": VERIFIER_TIMEOUT_SECONDS}, "excluded_diagnostic_batches": [READINESS_RUN_ID, "formal-task006-local-3x3-minimaxm3-20260824-164022-readiness", "formal-task006-local-3x3-minimaxm3-20260824-02-readiness", "formal-task006-local-3x3-minimaxm3-20260824-03-readiness-final", "formal-task006-docker-local-3x3-minimaxm3-20260826-01", "formal-task006-docker-local-3x3-minimaxm3-20260826-02", "formal-task006-docker-local-3x3-minimaxm3-20260826-03", "formal-task006-docker-local-3x3-minimaxm3-20260826-04", "formal-task006-docker-local-3x3-minimaxm3-20260826-05", "formal-task006-docker-local-3x3-minimaxm3-20260826-06", "formal-task006-docker-local-3x3-minimaxm3-20260826-07", "formal-task006-docker-local-3x3-minimaxm3-20260826-08", "formal-task006-docker-local-3x3-minimaxm3-20260827-09"], "excluded_attempts": excluded_attempts, "condition_summaries": condition_summaries, "results": [{"condition": item["condition"], "replicate_id": item["replicate_id"], "attempt": item["attempt"], "status": item["status"], "final_verifier_pass": item["final_verifier_pass"], "session_id": item["session_id"], "actual_model_ids": item["actual_model_ids"], "actual_provider_ids": item["actual_provider_ids"], "usage": item["usage"], "initially_available_modules": item["initially_available_modules"], "disclosed_modules": item["disclosed_modules"], "accessed_modules_after_disclosure": item["accessed_modules_after_disclosure"], "disclosure_events": item["disclosure_events"], "vista_metrics": item["vista_metrics"]} for item in formal_results]}
    (RESULT_ROOT / "group-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    posthoc_audit(formal_results, excluded_attempts, requested_model)
    return 0 if len(formal_results) == 9 and len(formal_included) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
