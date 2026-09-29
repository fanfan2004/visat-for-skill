"""Condition-isolated, non-overwriting Skill materialization."""

from __future__ import annotations

import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from vista.adapters.skillsbench import SkillsBenchSourceAdapter

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MaterializationError(RuntimeError):
    pass


class MaterializationExistsError(MaterializationError):
    pass


class Condition(StrEnum):
    NOSKILL = "noskill"
    NATIVE = "native"
    PD_VISTA = "pd-vista"

    @classmethod
    def parse(cls, value: Condition | str) -> Condition:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower().replace("_", "-").replace(" ", "-")
        aliases = {
            "no-skill": cls.NOSKILL,
            "noskill": cls.NOSKILL,
            "native-skill": cls.NATIVE,
            "native": cls.NATIVE,
            "pd-vista": cls.PD_VISTA,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown experiment condition: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    native_skill_path: str | None
    pd_vista_skill_path: str | None
    source_task_path: str | None = None
    source_skill_path: str | None = None
    enabled: bool = True
    source: str | None = None
    domain: str | None = None
    workflow_type: str | None = None
    validation_type: str | None = None

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.task_id):
            raise ValueError(f"unsafe task_id: {self.task_id!r}")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> TaskSpec:
        if "task_id" not in raw:
            raise ValueError("task manifest entry is missing task_id")
        return cls(
            task_id=str(raw["task_id"]),
            native_skill_path=_optional_string(raw.get("native_skill_path")),
            pd_vista_skill_path=_optional_string(raw.get("pd_vista_skill_path")),
            source_task_path=_optional_string(raw.get("source_task_path")),
            source_skill_path=_optional_string(raw.get("source_skill_path")),
            enabled=bool(raw.get("enabled", True)),
            source=_optional_string(raw.get("source")),
            domain=_optional_string(raw.get("domain")),
            workflow_type=_optional_string(raw.get("workflow_type")),
            validation_type=_optional_string(raw.get("validation_type")),
        )


@dataclass(frozen=True, slots=True)
class TaskManifest:
    tasks: tuple[TaskSpec, ...]

    @classmethod
    def load(cls, path: str | Path) -> TaskManifest:
        manifest_path = Path(path)
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if raw is None:
            entries: list[Mapping[str, Any]] = []
        elif isinstance(raw, list):
            entries = raw
        elif isinstance(raw, Mapping) and "tasks" in raw:
            entries = raw["tasks"]
        elif isinstance(raw, Mapping) and "task_id" in raw:
            entries = [raw]
        else:
            raise ValueError("task manifest must be a task, a task list, or contain 'tasks'")
        if not isinstance(entries, list):
            raise ValueError("task manifest 'tasks' must be a list")
        tasks = tuple(TaskSpec.from_mapping(entry) for entry in entries)
        ids = [task.task_id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task manifest contains duplicate task_id values")
        return cls(tasks=tasks)

    def get(self, task_id: str) -> TaskSpec:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    task_id: str
    condition: Condition
    destination: Path
    source: Path | None
    files: tuple[Path, ...]
    hidden_source: Path | None = None
    # The host keeps wrapper/audit files under ``destination``.  For PD-VISTA
    # this is the only directory that may be mounted into the agent sandbox.
    visible_root: Path | None = None


class SkillMaterializer:
    """Copy one condition's Skill into its isolated runtime directory.

    Destinations are never merged or replaced.  A caller must use a fresh
    task/condition location for every materialization set.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.skills_root = self.project_root / "skills"
        self.materialized_root = self.project_root / "data" / "materialized"
        self._lock = threading.RLock()

    def materialize(
        self,
        task: TaskSpec,
        condition: Condition | str,
        trial_id: str | None = None,
    ) -> MaterializationResult:
        selected = Condition.parse(condition)
        if not task.enabled:
            raise MaterializationError(f"task {task.task_id!r} is disabled")

        destination = self.materialized_root / selected.value / task.task_id
        if trial_id is not None:
            if not _SAFE_ID.fullmatch(trial_id):
                raise ValueError(f"unsafe trial_id: {trial_id!r}")
            destination /= trial_id
        self._assert_within(destination, self.materialized_root, "materialization destination")

        with self._lock:
            if destination.exists() or destination.is_symlink():
                raise MaterializationExistsError(
                    f"refusing to overwrite existing materialization: {destination}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)

            if selected is Condition.NOSKILL:
                destination.mkdir()
                return MaterializationResult(
                    task_id=task.task_id,
                    condition=selected,
                    destination=destination,
                    source=None,
                    files=(),
                    visible_root=destination,
                )

            hidden_source: Path | None = None
            if task.source_skill_path and selected in {Condition.NATIVE, Condition.PD_VISTA}:
                adapter = SkillsBenchSourceAdapter(self.project_root)
                audit = adapter.audit(task.source_task_path, task.source_skill_path)
                audit.raise_for_errors()
                if selected is Condition.NATIVE:
                    source = adapter.materialize_native(task.source_skill_path, destination)
                else:
                    pd_materialization = adapter.materialize_pd_vista(
                        task.source_task_path,
                        task.source_skill_path,
                        task.pd_vista_skill_path,
                        destination,
                    )
                    source = pd_materialization.native_source
                    hidden_source = pd_materialization.hidden_source
            else:
                source = self._source_for(task, selected)
                self._validate_source_tree(source)
                shutil.copytree(source, destination, symlinks=False)
            files = tuple(
                sorted(
                    (
                        path.relative_to(destination)
                        for path in destination.rglob("*")
                        if path.is_file()
                    ),
                    key=lambda path: path.as_posix(),
                )
            )
            visible_root = destination
            if selected is Condition.PD_VISTA:
                visible_root = self._build_pd_visible_root(destination)
            return MaterializationResult(
                task_id=task.task_id,
                condition=selected,
                destination=destination,
                source=source,
                files=files,
                hidden_source=hidden_source,
                visible_root=visible_root,
            )

    def expose_module(
        self,
        result: MaterializationResult,
        module_id: str,
    ) -> Path:
        """Expose one PD-VISTA module after a router decision."""

        if result.condition is not Condition.PD_VISTA or result.hidden_source is None:
            raise MaterializationError("module disclosure is only available for PD-VISTA")
        if not _SAFE_ID.fullmatch(module_id):
            raise MaterializationError(f"unsafe module_id: {module_id!r}")
        source = (result.hidden_source / module_id).resolve()
        visible_root = result.visible_root or result.destination
        destination = (visible_root / "modules" / module_id).resolve()
        host_destination = (result.destination / "modules" / module_id).resolve()
        self._assert_within(source, result.hidden_source, "hidden module source")
        self._assert_within(
            destination,
            visible_root / "modules",
            "visible module destination",
        )
        if not source.is_dir():
            raise MaterializationError(f"hidden module does not exist: {module_id}")
        if destination.exists() or (host_destination != destination and host_destination.exists()):
            raise MaterializationExistsError(
                f"refusing to overwrite disclosed module: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
        if host_destination != destination:
            host_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, host_destination, symlinks=False)
        return destination

    @staticmethod
    def _build_pd_visible_root(destination: Path) -> Path:
        """Create the exact initial PD-VISTA agent-visible file set.

        ``source.yaml`` and ``materialization.json`` remain host-side audit
        records.  The routing manifest also remains host-side so module IDs
        cannot be guessed or accessed before disclosure.
        """

        visible_root = destination / "_agent_visible"
        visible_root.mkdir()
        for name in ("SKILL.md",):
            source = destination / name
            if not source.is_file():
                raise MaterializationError(f"PD-VISTA wrapper is missing visible file: {source}")
            shutil.copy2(source, visible_root / name)
        return visible_root

    def _source_for(self, task: TaskSpec, condition: Condition) -> Path:
        if condition is Condition.NATIVE:
            declared = task.native_skill_path
            allowed_root = self.skills_root / "native" / task.task_id
            if task.source_skill_path and declared == task.source_skill_path:
                raise MaterializationError(
                    "native source references require source_task_path and the SkillsBench adapter"
                )
        else:
            declared = task.pd_vista_skill_path
            allowed_root = self.skills_root / "pd-vista" / task.task_id
        if not declared:
            raise MaterializationError(
                f"task {task.task_id!r} has no Skill path for {condition.value}"
            )
        candidate = (self.project_root / declared).resolve()
        self._assert_within(candidate, allowed_root.resolve(), "Skill source")
        if not candidate.is_dir():
            raise MaterializationError(f"Skill source is not a directory: {candidate}")
        return candidate

    @staticmethod
    def _assert_within(path: Path, root: Path, label: str) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise MaterializationError(f"{label} escapes its allowed root: {path}") from exc

    def _validate_source_tree(self, source: Path) -> None:
        for path in source.rglob("*"):
            if path.is_symlink():
                raise MaterializationError(f"symbolic links are not materialized: {path}")
            self._assert_within(path, source, "Skill member")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
