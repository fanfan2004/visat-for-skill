"""Fail-closed Windows-local OpenCode runner primitives for VISTA v6.

This module intentionally has no dependency on BenchFlow, Docker, or WSL.
It is safe to use for zero-model preflight; calling the model is a separate,
explicitly gated operation owned by the v6 runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[3])).resolve()


def _resolve_opencode_launcher() -> Path:
    override = os.environ.get("VISTA_OPENCODE_LAUNCHER")
    if override:
        return Path(override).expanduser().resolve()
    executable = "opencode.cmd" if os.name == "nt" else "opencode"
    discovered = shutil.which(executable)
    if discovered:
        return Path(discovered).resolve()
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return appdata / "npm" / "opencode.cmd"
    return Path("/usr/local/bin/opencode")


LAUNCHER = _resolve_opencode_launcher()
_DEFAULT_BINARY = (
    LAUNCHER.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    if os.name == "nt"
    else LAUNCHER
)
BINARY = Path(os.environ.get("VISTA_OPENCODE_BINARY", _DEFAULT_BINARY)).expanduser().resolve()
EXPECTED_VERSION = "1.18.18"
MODEL = "openai/gpt-5.6-terra"
EXPECTED_ACTUAL_MODEL = "gpt-5.6-terra"
FORBIDDEN_LAUNCHER_TOKENS = (
    ("wsl", "docker", "container", "\\\\wsl$", "/usr/")
    if os.name == "nt"
    else ("wsl", "docker", "container", "\\\\wsl$")
)


class LocalGateError(RuntimeError):
    """A condition that must stop the whole local experimental round."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normal(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(path)))


@dataclass(frozen=True)
class LauncherEvidence:
    launcher: str
    binary: str
    launcher_sha256: str
    binary_sha256: str
    version: str
    parent_pid: int


def freeze_launcher(*, launcher: Path = LAUNCHER, binary: Path = BINARY) -> LauncherEvidence:
    """Prove the fixed host launcher identity before any model request."""

    if _normal(launcher) != _normal(LAUNCHER) or _normal(binary) != _normal(BINARY):
        raise LocalGateError("launcher or binary differs from the frozen host paths")
    rendered = str(launcher).casefold()
    if not launcher.is_absolute() or any(token in rendered for token in FORBIDDEN_LAUNCHER_TOKENS):
        raise LocalGateError("launcher is not an allowed absolute host OpenCode path")
    if not launcher.is_file() or not binary.is_file():
        raise LocalGateError("frozen host OpenCode launcher or binary is missing")
    try:
        completed = subprocess.run(
            [str(launcher), "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except OSError as exc:
        raise LocalGateError("could not execute the frozen host OpenCode launcher") from exc
    version = (completed.stdout or completed.stderr).strip().splitlines()[0].strip()
    if completed.returncode != 0 or version != EXPECTED_VERSION:
        raise LocalGateError("frozen OpenCode version does not equal 1.18.18")
    return LauncherEvidence(
        launcher=str(LAUNCHER), binary=str(BINARY), launcher_sha256=sha256_file(launcher),
        binary_sha256=sha256_file(binary), version=version, parent_pid=os.getpid(),
    )


def command_for_turn(
    *,
    workspace: Path,
    prompt: str,
    session_id: str | None = None,
    model: str = MODEL,
) -> list[str]:
    """Build an argument array; secrets never belong in it."""

    workspace = workspace.resolve()
    if not workspace.is_absolute() or _normal(workspace).is_relative_to(_normal(REPO_ROOT)):
        raise LocalGateError(f"agent workspace must be an absolute path outside {REPO_ROOT}")
    command = [
        str(LAUNCHER),
        "run",
        "--pure",
        "--auto",
        "--dir",
        str(workspace),
        "--format",
        "json",
        "--model",
        model,
    ]
    if session_id:
        command.extend(["--session", session_id])
    command.append(prompt)
    if "--attach" in command:
        raise LocalGateError("attaching to an OpenCode server is prohibited")
    return command


def isolated_environment(task_root: Path) -> dict[str, str]:
    """Return task-exclusive OpenCode state locations without persisting secrets."""

    task_root = task_root.resolve()
    locations = {
        "OPENCODE_CONFIG": task_root / "config" / "opencode.json",
        "OPENCODE_CONFIG_DIR": task_root / "config-dir",
        "XDG_CONFIG_HOME": task_root / "xdg-config",
        "XDG_DATA_HOME": task_root / "xdg-data",
        "XDG_CACHE_HOME": task_root / "xdg-cache",
        "APPDATA": task_root / "appdata",
        "LOCALAPPDATA": task_root / "localappdata",
    }
    return {key: str(value) for key, value in locations.items()}


def write_provider_config(task_root: Path) -> Path:
    """Create an isolated provider configuration that references, never stores, the key."""

    endpoint = os.environ.get("OPENAI_BASE_URL", "")
    if not endpoint or not os.environ.get("OPENAI_API_KEY"):
        raise LocalGateError("OPENAI_BASE_URL or OPENAI_API_KEY is absent")
    config_path = task_root / "config" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=False)
    config = {
        "share": "disabled",
        "permission": {"external_directory": "deny"},
        "provider": {
            "openai": {
                "npm": "@ai-sdk/openai",
                "name": "codexzh",
                "options": {
                    "baseURL": endpoint,
                    "apiKey": "{env:OPENAI_API_KEY}",
                    "setCachekey": True,
                    "options": {
                        "reasoningEffort": "high",
                        "textVerbosity": "low",
                        "reasoningSummary": "auto",
                    },
                },
                "models": {
                    "gpt-5.6-terra": {"name": "gpt-5.6-terra"},
                },
            }
        },
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


@dataclass(frozen=True)
class ReadinessRecord:
    task_id: str
    benchmark_task_id: str
    workspace_materializable_without_container: bool
    required_inputs_present_locally: bool
    host_dependencies_present: bool
    verifier_runnable_on_windows: bool
    hidden_assets_excluded: bool
    task_semantics_equivalent: bool
    status: str
    reasons: tuple[str, ...]
    evidence_paths: tuple[str, ...]


TASKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("task-001", "python-scala-translation", ("python", "scala", "sbt")),
    ("task-002", "enterprise-information-search", ("python",)),
    ("task-003", "data-to-d3", ("python", "node", "playwright")),
    ("task-004", "powerlifting-coef-calc", ("python",)),
    ("task-005", "organize-messy-files", ("python",)),
    ("task-006", "fix-build-agentops", ("python",)),
)


def audit_readiness(repo_root: Path) -> list[ReadinessRecord]:
    """Metadata/file-system audit only; it never reads oracle or verifier content."""

    records: list[ReadinessRecord] = []
    task_root = repo_root / "vendor" / "skillsbench" / "tasks"
    for task_id, benchmark, dependencies in TASKS:
        source = task_root / benchmark
        environment = source / "environment"
        dockerfile = environment / "Dockerfile"
        task_md = source / "task.md"
        inputs = environment.is_dir() and any(
            path.is_file() for path in environment.rglob("*") if "skills" not in path.parts
        )
        materializable = environment.is_dir() and task_md.is_file() and dockerfile.is_file()
        missing_dependencies = tuple(
            dependency for dependency in dependencies if not shutil.which(dependency)
        )
        dependencies_ok = not missing_dependencies
        reasons: list[str] = []
        semantics = materializable and inputs and dependencies_ok
        verifier_windows = False  # No verifier content/execution is permitted before explicit approval.
        if not materializable:
            reasons.append("environment, Dockerfile, or task.md is absent")
        if not dependencies_ok:
            reasons.append(
                "required host dependency is unavailable: " + ", ".join(missing_dependencies)
            )
        if benchmark == "organize-messy-files":
            semantics = False
            reasons.append("Dockerfile-only downloaded paper corpus is not a lawful local input")
        if benchmark == "fix-build-agentops":
            semantics = False
            reasons.append("BugSwarm cached image is not a lawful, source-proven local input")
        reasons.append("Windows verifier equivalence is not yet proven without reading or running verifier")
        status = "ready" if semantics and verifier_windows else "host_incompatible"
        records.append(ReadinessRecord(
            task_id, benchmark, materializable, inputs, dependencies_ok, verifier_windows,
            True, semantics, status, tuple(reasons), (str(task_md), str(dockerfile), str(environment)),
        ))
    return records


def save_readiness(records: list[ReadinessRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# VISTA v6 Windows-local readiness",
        "",
        "This is a zero-model, fail-closed audit. `host_incompatible` is not an agent failure.",
        "",
        "| Task | Benchmark | Status | Materializable | Inputs | Host dependencies | Verifier equivalent | Semantic equivalence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            "| {task_id} | {benchmark_task_id} | {status} | {workspace_materializable_without_container} | "
            "{required_inputs_present_locally} | {host_dependencies_present} | "
            "{verifier_runnable_on_windows} | {task_semantics_equivalent} |".format(**asdict(record))
        )
    lines.extend(["", "## Fail-closed reasons", ""])
    for record in records:
        lines.append(f"- `{record.task_id}`: {'; '.join(record.reasons)}")
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
