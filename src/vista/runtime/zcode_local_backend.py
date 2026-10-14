"""Fail-closed macOS-local ZCode runner primitives for the VISTA runtime.

This module intentionally has no dependency on BenchFlow, Docker, or WSL.
It mirrors ``opencode_local_backend.py``: safe for zero-model preflight;
calling the model is a separate, explicitly gated operation owned by the
group runner.

The ZCode CLI is invoked through the bundled Electron runtime in Node mode
(``ELECTRON_RUN_AS_NODE=1``) so every turn is a single headless prompt:

    zcode.cjs -p <prompt> --cwd <workspace> --mode yolo --json [--resume <session>]

State isolation uses ``ZCODE_STORAGE_DIR``: sessions, rollouts, and plugins
land under the trial-scoped storage directory instead of ``~/.zcode``.
The model is pinned by the host provider configuration (``~/.zcode/v2/provider_config.json``);
the CLI has no ``--model`` flag, so ``current_zcode_model_config()`` is the
fail-closed gate that proves the host default model before any batch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[3])).resolve()

ZCODE_SCRIPT = Path(
    os.environ.get(
        "VISTA_ZCODE_SCRIPT",
        Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"),
    )
).expanduser().resolve()
ELECTRON_BINARY = Path(
    os.environ.get(
        "VISTA_ZCODE_BINARY",
        Path("/Applications/ZCode.app/Contents/MacOS/ZCode"),
    )
).expanduser().resolve()

EXPECTED_VERSION = "0.16.9"
PROVIDER_ID = "new-provider"
MODEL = "glm53-flash"
EXPECTED_MODEL = "glm53-flash"
FORBIDDEN_LAUNCHER_TOKENS = ("wsl", "docker", "container", "\\\\wsl$")


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
    electron_binary: str
    zcode_script: str
    binary_sha256: str
    script_sha256: str
    version: str
    parent_pid: int


def freeze_launcher(
    *,
    electron: Path = ELECTRON_BINARY,
    script: Path = ZCODE_SCRIPT,
) -> LauncherEvidence:
    """Prove the fixed host launcher identity before any model request."""

    if _normal(electron) != _normal(ELECTRON_BINARY) or _normal(script) != _normal(ZCODE_SCRIPT):
        raise LocalGateError("electron binary or zcode script differs from the frozen host paths")
    rendered = str(script).casefold()
    if not script.is_absolute() or any(token in rendered for token in FORBIDDEN_LAUNCHER_TOKENS):
        raise LocalGateError("zcode script is not an allowed absolute host path")
    if not electron.is_file() or not script.is_file():
        raise LocalGateError("frozen ZCode electron binary or script is missing")
    try:
        completed = subprocess.run(
            [str(electron), str(script), "--version"],
            capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalGateError("frozen ZCode launcher version probe timed out") from exc
    except OSError as exc:
        raise LocalGateError("could not execute the frozen ZCode launcher") from exc
    version = (completed.stdout or completed.stderr).strip().splitlines()[0].strip()
    if completed.returncode != 0 or version != EXPECTED_VERSION:
        raise LocalGateError("frozen ZCode version does not equal 0.16.9")
    return LauncherEvidence(
        electron_binary=str(ELECTRON_BINARY), zcode_script=str(ZCODE_SCRIPT),
        binary_sha256=sha256_file(electron), script_sha256=sha256_file(script),
        version=version, parent_pid=os.getpid(),
    )


def command_for_turn(
    *,
    workspace: Path,
    prompt: str,
    session_id: str | None = None,
    mode: str = "yolo",
) -> list[str]:
    """Build an argument array; secrets never belong in it."""

    workspace = workspace.resolve()
    if not workspace.is_absolute() or _normal(workspace).is_relative_to(_normal(REPO_ROOT)):
        raise LocalGateError(f"agent workspace must be an absolute path outside {REPO_ROOT}")
    command = [
        str(ELECTRON_BINARY),
        str(ZCODE_SCRIPT),
        "-p",
        prompt,
        "--cwd",
        str(workspace),
        "--mode",
        mode,
        "--json",
    ]
    if session_id:
        command.extend(["--resume", session_id])
    if "--attach" in command:
        raise LocalGateError("attaching external files to a trial prompt is prohibited")
    return command


def turn_environment(task_root: Path) -> dict[str, str]:
    """Return the process environment for one trial: node mode + task-scoped storage."""

    task_root = task_root.resolve()
    return {
        "ELECTRON_RUN_AS_NODE": "1",
        "ZCODE_STORAGE_DIR": str(task_root / "zcode-storage"),
    }


def isolated_environment(task_root: Path) -> dict[str, str]:
    """Return task-exclusive ZCode state locations without persisting secrets."""

    task_root = task_root.resolve()
    return {
        "ZCODE_STORAGE_DIR": str(task_root / "zcode-storage"),
    }


def model_io_rollout_path(storage_dir: Path, session_id: str) -> Path:
    """Path of the per-session request/usage rollout inside trial-scoped storage."""

    return Path(storage_dir) / "cli" / "rollout" / f"model-io-{session_id}.jsonl"


def parse_model_io_rollout(path: Path) -> list[dict[str, Any]]:
    """Normalize one model-io rollout into per-request telemetry records."""

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LocalGateError(f"malformed rollout line in {path}") from exc
            model = raw.get("model") or {}
            usage = (raw.get("response") or {}).get("usage") or {}
            records.append({
                "request_id": raw.get("requestId"),
                "attempt": raw.get("attempt"),
                "completed_at": raw.get("completedAt"),
                "duration_ms": raw.get("durationMs"),
                "provider_id": model.get("providerId"),
                "model_id": model.get("modelId"),
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
                "cache_read_tokens": usage.get("cacheReadTokens", 0),
                "reasoning_tokens": usage.get("reasoningTokens", 0),
            })
    if not records:
        raise LocalGateError(f"model-io rollout has no request records: {path}")
    return records


def rollout_usage_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    """Additive usage accounting across every request record of one session."""

    summary = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
    for record in records:
        summary["requests"] += 1
        summary["input_tokens"] += int(record["input_tokens"] or 0)
        summary["output_tokens"] += int(record["output_tokens"] or 0)
        summary["cache_read_tokens"] += int(record["cache_read_tokens"] or 0)
    summary["input_plus_output_tokens"] = summary["input_tokens"] + summary["output_tokens"]
    return summary


def rollout_model_identity(
    records: list[dict[str, Any]],
    *,
    expected_provider: str = PROVIDER_ID,
    expected_model: str = EXPECTED_MODEL,
) -> tuple[str, str]:
    """Return the single (provider_id, model_id) across all requests, fail-closed."""

    identities = {(r["provider_id"], r["model_id"]) for r in records}
    if len(identities) != 1:
        raise LocalGateError("model identity is not consistent across rollout requests")
    provider_id, model_id = identities.pop()
    if (provider_id, model_id) != (expected_provider, expected_model):
        raise LocalGateError(
            f"rollout model {provider_id}/{model_id} does not equal "
            f"{expected_provider}/{expected_model}"
        )
    return provider_id, model_id


def current_zcode_model_config(
    *,
    provider_id: str = PROVIDER_ID,
    model_id: str = MODEL,
) -> dict[str, str]:
    """Fail-closed proof that the host provider config pins the frozen model.

    The ZCode CLI has no ``--model`` flag; the host default model is the pin.
    A parallel instance may pin the second provider through a corrected config
    file passed via ``ZCODE_PERSONAL_PROVIDER_CONFIG_FILE`` (the CLI reads its
    personal provider config from that env override).
    Secrets are only referenced, never returned or persisted here.
    """

    path = Path(
        os.environ.get(
            "VISTA_ZCODE_PROVIDER_CONFIG",
            Path.home() / ".zcode" / "v2" / "provider_config.json",
        )
    ).expanduser().resolve()
    if not path.is_file():
        raise LocalGateError(f"ZCode provider config is missing: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    rules = (config.get("config") or {})
    provider_order = rules.get("providerOrder") or []
    if provider_order[:1] != [provider_id]:
        raise LocalGateError(
            f"host providerOrder must start with {provider_id}: {provider_order}"
        )
    provider_rule = ((rules.get("providerConfigRules") or {}).get("providerRules") or [None])[0]
    if not isinstance(provider_rule, dict) or provider_rule.get("providerId") != provider_id:
        raise LocalGateError(f"host provider rule for {provider_id} is absent")
    model_order = provider_rule.get("config", {}).get("modelOrder") or []
    if model_order[:1] != [model_id]:
        raise LocalGateError(f"host modelOrder must start with {model_id}: {model_order}")
    return {"provider_id": provider_id, "model_id": model_id, "config_path": str(path)}


def save_readiness(evidence: LauncherEvidence, model_config: dict[str, str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"launcher": asdict(evidence), "model": model_config}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "ELECTRON_BINARY",
    "EXPECTED_MODEL",
    "EXPECTED_VERSION",
    "LocalGateError",
    "MODEL",
    "PROVIDER_ID",
    "LauncherEvidence",
    "ZCODE_SCRIPT",
    "command_for_turn",
    "current_zcode_model_config",
    "freeze_launcher",
    "isolated_environment",
    "model_io_rollout_path",
    "parse_model_io_rollout",
    "rollout_model_identity",
    "rollout_usage_summary",
    "save_readiness",
    "sha256_file",
    "turn_environment",
]
