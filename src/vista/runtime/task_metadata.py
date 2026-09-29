"""Read the public SkillsBench task budget/resource front matter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class SkillsBenchTaskMetadata:
    """Public task metadata used to cap adapter budgets without exposing answers."""

    path: Path
    digest: str | None
    agent_timeout_sec: int | None
    verifier_timeout_sec: int | None
    build_timeout_sec: int | None
    network_mode: str | None
    resource_summary: dict[str, Any]

    @classmethod
    def load(cls, task_root: str | Path) -> SkillsBenchTaskMetadata:
        path = Path(task_root) / "task.md"
        if not path.is_file():
            raise FileNotFoundError(f"SkillsBench task metadata is missing: {path}")
        text = path.read_text(encoding="utf-8")
        raw = _front_matter(text)
        agent = _mapping(raw.get("agent"))
        verifier = _mapping(raw.get("verifier"))
        sandbox = _mapping(raw.get("sandbox"))
        resource_summary = {
            key: value
            for key, value in sandbox.items()
            if key in {"network_mode", "cpu", "memory", "disk", "resource_limits"}
            and _public_scalar_or_mapping(value)
        }
        return cls(
            path=path,
            digest=_sha256(path),
            agent_timeout_sec=_positive_int(agent.get("timeout_sec")),
            verifier_timeout_sec=_positive_int(verifier.get("timeout_sec")),
            build_timeout_sec=_positive_int(sandbox.get("build_timeout_sec")),
            network_mode=(
                str(sandbox["network_mode"]) if sandbox.get("network_mode") is not None else None
            ),
            resource_summary=resource_summary,
        )

    def cap_agent_timeout(self, requested: int) -> int:
        return _cap(requested, self.agent_timeout_sec)

    def cap_verifier_timeout(self, requested: int) -> int:
        return _cap(requested, self.verifier_timeout_sec)

    def to_public_metadata(
        self, *, requested_agent: int, requested_verifier: int
    ) -> dict[str, Any]:
        return {
            "task_metadata_path": self.path.as_posix(),
            "task_metadata_sha256": self.digest,
            "declared_agent_timeout_sec": self.agent_timeout_sec,
            "declared_verifier_timeout_sec": self.verifier_timeout_sec,
            "declared_build_timeout_sec": self.build_timeout_sec,
            "effective_agent_timeout_sec": self.cap_agent_timeout(requested_agent),
            "effective_verifier_timeout_sec": self.cap_verifier_timeout(requested_verifier),
            "effective_build_timeout_sec": self.build_timeout_sec or 180,
            "requested_agent_timeout_sec": requested_agent,
            "requested_verifier_timeout_sec": requested_verifier,
            "sandbox_resources": self.resource_summary,
            "sandbox_network_mode": self.network_mode,
        }


def _front_matter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError("SkillsBench task front matter is not closed")
    value = yaml.safe_load("\n".join(lines[1:end]))
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _cap(requested: int, declared: int | None) -> int:
    value = max(1, int(requested))
    return min(value, declared) if declared is not None else value


def _public_scalar_or_mapping(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        return all(_public_scalar_or_mapping(item) for item in value.values())
    if isinstance(value, list):
        return all(_public_scalar_or_mapping(item) for item in value)
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["SkillsBenchTaskMetadata"]
