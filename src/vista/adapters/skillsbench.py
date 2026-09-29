"""Read-only SkillsBench source auditing and reproducible Skill materialization.

This adapter never edits ``vendor/skillsbench``.  Native materializations are byte
copies of the source Skill tree. PD-VISTA materializations expose only the routing root
initially; an exact source snapshot is staged outside the agent workspace and copied
below ``modules/`` one selected module at a time.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:OPENAI|ANTHROPIC|GEMINI|AWS|MODAL)_[A-Z0-9_]+\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
)
class SourceAuditError(RuntimeError):
    """Raised when a source cannot be used without guessing or rewriting it."""


@dataclass(frozen=True, slots=True)
class AuditResult:
    source_task_path: Path
    source_skill_path: Path
    skill_ids: tuple[str, ...]
    file_count: int
    source_digest: str
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    credential_hits: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and not self.credential_hits

    def raise_for_errors(self) -> None:
        problems = list(self.errors)
        if self.credential_hits:
            problems.append(
                "credential-like material detected in read-only source: "
                + ", ".join(self.credential_hits)
            )
        if problems:
            raise SourceAuditError("; ".join(problems))


@dataclass(frozen=True, slots=True)
class PdvistaMaterialization:
    native_source: Path
    hidden_source: Path


class SkillsBenchSourceAdapter:
    """Audit and materialize one native SkillsBench task without mutating it."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.vendor_tasks_root = (self.project_root / "vendor" / "skillsbench" / "tasks").resolve()

    def audit(
        self,
        source_task_path: str | Path | None,
        source_skill_path: str | Path | None,
    ) -> AuditResult:
        if not source_task_path or not source_skill_path:
            return AuditResult(
                self.vendor_tasks_root,
                self.vendor_tasks_root,
                (),
                0,
                "",
                errors=("source_task_path and source_skill_path are required",),
            )
        task_root = self._resolve_vendor_path(source_task_path, "source task")
        skill_root = self._resolve_vendor_path(source_skill_path, "source Skill")
        errors: list[str] = []
        warnings: list[str] = []
        credential_hits: list[str] = []

        if not task_root.is_dir():
            errors.append(f"source task directory does not exist: {task_root}")
        if not skill_root.is_dir():
            errors.append(f"source Skill directory does not exist: {skill_root}")
        expected_skill_root = task_root / "environment" / "skills"
        if skill_root != expected_skill_root.resolve():
            errors.append(
                "source Skill is not the task environment/skills directory: "
                f"{skill_root}"
            )

        required = (
            task_root / "task.md",
            task_root / "environment" / "Dockerfile",
            task_root / "oracle" / "solve.sh",
            task_root / "verifier" / "test.sh",
        )
        for path in required:
            if not path.is_file():
                errors.append(f"missing SkillsBench entrypoint: {path}")

        skill_ids = tuple(sorted(
            child.name
            for child in skill_root.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        )) if skill_root.is_dir() else ()
        if not skill_ids:
            errors.append(f"no top-level Skill directories with SKILL.md: {skill_root}")

        files = tuple(self._iter_files(skill_root)) if skill_root.is_dir() else ()
        for path in (task_root / "task.md", task_root / "environment" / "Dockerfile", *files):
            hit = self._credential_hit(path)
            if hit:
                credential_hits.append(hit)
        dockerfile = task_root / "environment" / "Dockerfile"
        if dockerfile.is_file() and "network_mode: none" not in self._read_text(
            task_root / "task.md"
        ):
            warnings.append(
                "task metadata permits public/networked sandbox; no credential is required "
                "by this adapter"
            )
        source_digest = self._digest_tree(skill_root) if skill_root.is_dir() else ""
        return AuditResult(
            source_task_path=task_root,
            source_skill_path=skill_root,
            skill_ids=skill_ids,
            file_count=len(files),
            source_digest=source_digest,
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
            credential_hits=tuple(dict.fromkeys(credential_hits)),
        )

    def materialize_native(self, source_skill_path: str | Path, destination: str | Path) -> Path:
        source = self._resolve_vendor_path(source_skill_path, "native Skill")
        if not source.is_dir():
            raise SourceAuditError(f"native Skill source is not a directory: {source}")
        destination_path = Path(destination)
        self._assert_inside(destination_path.resolve(), self.project_root / "data" / "materialized")
        shutil.copytree(source, destination_path, symlinks=False)
        return source

    def materialize_pd_vista(
        self,
        source_task_path: str | Path | None,
        source_skill_path: str | Path | None,
        pd_vista_skill_path: str | Path | None,
        destination: str | Path,
    ) -> PdvistaMaterialization:
        audit = self.audit(source_task_path, source_skill_path)
        audit.raise_for_errors()
        if not pd_vista_skill_path:
            raise SourceAuditError("pd_vista_skill_path is required for PD-VISTA")
        pd_root = (self.project_root / pd_vista_skill_path).resolve()
        self._assert_inside(pd_root, self.project_root / "skills" / "pd-vista")
        if (
            not pd_root.is_dir()
            or not (pd_root / "SKILL.md").is_file()
            or not (pd_root / "manifest.yaml").is_file()
        ):
            raise SourceAuditError(
                "PD-VISTA wrapper must contain SKILL.md and manifest.yaml: "
                f"{pd_root}"
            )
        destination_path = Path(destination)
        self._assert_inside(destination_path.resolve(), self.project_root / "data" / "materialized")
        shutil.copytree(pd_root, destination_path, symlinks=False)
        materialized_root = self.project_root / "data" / "materialized"
        relative_destination = destination_path.relative_to(materialized_root)
        hidden_root = materialized_root / ".pd-vista-sources" / relative_destination
        self._assert_inside(hidden_root.resolve(), materialized_root / ".pd-vista-sources")
        if hidden_root.exists():
            raise SourceAuditError(f"refusing to overwrite hidden PD-VISTA source: {hidden_root}")
        hidden_root.mkdir(parents=True, exist_ok=False)
        source_root = audit.source_skill_path
        for skill_id in audit.skill_ids:
            shutil.copytree(source_root / skill_id, hidden_root / skill_id, symlinks=False)

        copied_digest = self._digest_tree(hidden_root, strip_prefix=hidden_root)
        inventory = {
            "schema_version": 1,
            "mode": "pd-vista-equivalent",
            "source_task_path": self._project_relative(audit.source_task_path),
            "source_skill_path": self._project_relative(audit.source_skill_path),
            "native_source_digest": audit.source_digest,
            "materialized_modules_digest": copied_digest,
            "byte_equivalent": self._module_digest_matches(source_root, hidden_root),
            "module_visibility": "root_only_until_disclosure",
            "skill_ids": list(audit.skill_ids),
        }
        if not inventory["byte_equivalent"]:
            raise SourceAuditError("PD-VISTA module copy failed byte-equivalence audit")
        (destination_path / "materialization.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return PdvistaMaterialization(native_source=source_root, hidden_source=hidden_root)

    def _resolve_vendor_path(self, declared: str | Path, label: str) -> Path:
        candidate = (self.project_root / declared).resolve()
        self._assert_inside(candidate, self.vendor_tasks_root)
        if not candidate.exists():
            raise SourceAuditError(f"{label} does not exist: {candidate}")
        return candidate

    @staticmethod
    def _assert_inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise SourceAuditError(f"path escapes allowed root: {path}") from exc

    @staticmethod
    def _iter_files(root: Path) -> tuple[Path, ...]:
        files: list[Path] = []
        for path in root.rglob("*"):
            if path.is_symlink():
                raise SourceAuditError(f"symbolic links are not accepted in Skill source: {path}")
            if path.is_file():
                files.append(path)
        return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))

    @classmethod
    def _digest_tree(cls, root: Path, *, strip_prefix: Path | None = None) -> str:
        prefix = strip_prefix or root
        digest = hashlib.sha256()
        for path in cls._iter_files(root):
            relative = path.relative_to(prefix).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @classmethod
    def _module_digest_matches(cls, source_root: Path, modules_root: Path) -> bool:
        source_files = cls._iter_files(source_root)
        module_files = cls._iter_files(modules_root)
        source_pairs = sorted(
            (path.relative_to(source_root).as_posix(), path.read_bytes())
            for path in source_files
        )
        module_pairs = sorted(
            (path.relative_to(modules_root).as_posix(), path.read_bytes())
            for path in module_files
        )
        return source_pairs == module_pairs

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    @classmethod
    def _credential_hit(cls, path: Path) -> str | None:
        text = cls._read_text(path)
        if not text:
            return None
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(text):
                return path.as_posix()
        return None

    def _project_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()
