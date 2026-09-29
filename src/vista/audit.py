"""Deterministic audit helpers shared by runtime, telemetry, and analysis."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:OPENAI|DEEPSEEK|ANTHROPIC|GEMINI|AWS|MODAL)_[A-Z0-9_]+\s*[:=]\s*\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?"
        r"-----END (?:[A-Z]+ )?PRIVATE KEY-----",
        re.S,
    ),
)
_WRITE_COMMAND = re.compile(
    r"\b(?:mkdir|touch|cp|mv|rm|tee|cat|sed|perl|python|node|apply_patch|write|edit|"
    r"truncate|chmod|chown)\b|(?:>|>>|\bheredoc\b)",
    re.IGNORECASE,
)


def redact_secret(value: str | None) -> str | None:
    if value is None:
        return None
    result = str(value)
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("<redacted-secret>", result)
    return result


def cap_public(value: str | None, limit: int = 12000) -> str | None:
    safe = redact_secret(value)
    if safe is None:
        return None
    return safe if len(safe) <= limit else safe[:limit] + "\n[output truncated]"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(
    root: str | Path,
    *,
    exclude_dirs: Iterable[str] = (),
    exclude_files: Iterable[str] = (),
) -> str:
    """Hash a stable relative-path/bytes inventory without following links."""

    base = Path(root)
    excluded_dirs = {str(item).replace("\\", "/").strip("/") for item in exclude_dirs}
    excluded_files = {str(item).replace("\\", "/") for item in exclude_files}
    digest = hashlib.sha256()
    if not base.exists():
        return digest.hexdigest()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base).as_posix()
        if relative in excluded_files:
            continue
        if any(
            relative == directory or relative.startswith(directory + "/")
            for directory in excluded_dirs
        ):
            continue
        files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tree_inventory(
    root: str | Path,
    *,
    exclude_dirs: Iterable[str] = (),
    exclude_files: Iterable[str] = (),
) -> list[dict[str, str | int]]:
    base = Path(root)
    excluded_dirs = {str(item).replace("\\", "/").strip("/") for item in exclude_dirs}
    excluded_files = {str(item).replace("\\", "/") for item in exclude_files}
    inventory: list[dict[str, str | int]] = []
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base).as_posix()
        if relative in excluded_files or any(
            relative == directory or relative.startswith(directory + "/")
            for directory in excluded_dirs
        ):
            continue
        inventory.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    return inventory


def public_artifact_digest(root: str | Path) -> str:
    return _public_artifact_tree_digest(root)


def public_artifact_inventory(root: str | Path) -> list[dict[str, str | int]]:
    """Return the auditable output tree without skill or hidden-source files."""

    return tree_inventory(
        root,
        exclude_dirs=("modules", "_agent_visible", ".pd-vista-sources", ".audit"),
        exclude_files=("SKILL.md", "manifest.yaml", "source.yaml", "materialization.json"),
    )


def _public_artifact_tree_digest(root: str | Path) -> str:
    return tree_digest(
        root,
        exclude_dirs=("modules", "_agent_visible", ".pd-vista-sources", ".audit"),
        exclude_files=("SKILL.md", "manifest.yaml", "source.yaml", "materialization.json"),
    )


def has_artifact_progress(before: str | None, after: str | None) -> bool:
    return bool(before and after and before != after)


def has_progress_signal(
    *,
    artifact_before: str | None,
    artifact_after: str | None,
    previous_feedback_fingerprints: Iterable[str] = (),
    observed_feedback_fingerprints: Iterable[str] = (),
    public_check_improved: bool = False,
    repair_action_observed: bool = False,
) -> bool:
    """Return true only for an auditable task-level progress signal.

    A disclosure or an arbitrary tool call is intentionally not a signal.  A
    changed public artifact, a changed/new public-check fingerprint, an
    improved public check, or an observed write/repair action is evidence that
    the next loop iteration had something concrete to work with.
    """

    if has_artifact_progress(artifact_before, artifact_after):
        return True
    if public_check_improved or repair_action_observed:
        return True
    previous = set(previous_feedback_fingerprints)
    return any(
        fingerprint and fingerprint not in previous
        for fingerprint in observed_feedback_fingerprints
    )


def has_repair_action(commands: Iterable[str]) -> bool:
    return any(_WRITE_COMMAND.search(str(command or "")) for command in commands)


def endpoint_identifier(base_url: str) -> str:
    """Return a non-sensitive provider endpoint identifier."""

    parsed = urlsplit(base_url)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme or 'unknown'}://{host}{port}{path}"


def config_digest(config: dict[str, object]) -> str:
    safe = {key: value for key, value in config.items() if "key" not in key.casefold()}
    return sha256_text(json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str))


__all__ = [
    "cap_public",
    "config_digest",
    "endpoint_identifier",
    "has_artifact_progress",
    "has_progress_signal",
    "has_repair_action",
    "public_artifact_digest",
    "public_artifact_inventory",
    "redact_secret",
    "sha256_file",
    "sha256_text",
    "tree_digest",
    "tree_inventory",
]
