"""Manifest-driven routing for progressive Skill disclosure."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vista.feedback import FeedbackClassification, FeedbackType
from vista.feedback.parser import feedback_type_names

_SAFE_MODULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    module_id: str
    path: str
    triggers: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ModuleSpec:
        module_id = str(raw.get("id", "")).strip()
        path = str(raw.get("path", "")).strip()
        triggers_raw = raw.get("triggers", ())
        if not _SAFE_MODULE_ID.fullmatch(module_id):
            raise ManifestError(f"invalid module id: {module_id!r}")
        if not path:
            raise ManifestError(f"module {module_id!r} is missing path")
        if not isinstance(triggers_raw, list | tuple):
            raise ManifestError(f"module {module_id!r} triggers must be a list")
        triggers = tuple(str(item).strip().lower() for item in triggers_raw if str(item).strip())
        if not triggers:
            raise ManifestError(f"module {module_id!r} must have at least one trigger")
        return cls(module_id=module_id, path=path, triggers=triggers)


@dataclass(frozen=True, slots=True)
class SkillManifest:
    root: Path
    modules: tuple[ModuleSpec, ...]

    @classmethod
    def load(cls, path: str | Path, *, require_files: bool = True) -> SkillManifest:
        manifest_path = Path(path).resolve()
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ManifestError("Skill manifest must be a mapping")
        modules_raw = raw.get("modules")
        if not isinstance(modules_raw, list):
            raise ManifestError("Skill manifest 'modules' must be a list")
        modules = tuple(ModuleSpec.from_mapping(item) for item in modules_raw)
        ids = [module.module_id for module in modules]
        if len(ids) != len(set(ids)):
            raise ManifestError("Skill manifest contains duplicate module ids")
        manifest = cls(root=manifest_path.parent, modules=modules)
        for module in modules:
            resolved = manifest.resolve_module_path(module)
            if require_files and not resolved.is_file():
                raise ManifestError(f"module file does not exist: {resolved}")
        return manifest

    def resolve_module_path(self, module: ModuleSpec) -> Path:
        declared = Path(module.path)
        if declared.is_absolute():
            raise ManifestError(f"module path must be relative: {module.path}")
        resolved = (self.root / declared).resolve()
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ManifestError(f"module path escapes Skill root: {module.path}") from exc
        return resolved

    def unroutable_feedback_types(self, feedback_types: Iterable[str]) -> tuple[str, ...]:
        declared = {trigger for module in self.modules for trigger in module.triggers}
        requested = tuple(dict.fromkeys(str(item).strip().casefold() for item in feedback_types))
        return tuple(item for item in requested if item and item not in declared)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    feedback_types: tuple[str, ...]
    modules: tuple[ModuleSpec, ...]
    previous_modules: tuple[str, ...]
    skipped_previous_modules: tuple[str, ...]
    reason: str

    @property
    def selected_modules(self) -> tuple[str, ...]:
        return tuple(module.module_id for module in self.modules)


@dataclass(frozen=True, slots=True)
class DisclosedModule:
    module_id: str
    path: Path
    content: str
    estimated_tokens: int


class DisclosureRouter:
    def __init__(
        self,
        manifest: SkillManifest,
        *,
        allow_revisit: bool = False,
        max_modules_per_decision: int = 1,
    ) -> None:
        if max_modules_per_decision < 1:
            raise ValueError("max_modules_per_decision must be positive")
        self.manifest = manifest
        self.allow_revisit = allow_revisit
        self.max_modules_per_decision = max_modules_per_decision

    def route(
        self,
        feedback: FeedbackClassification | FeedbackType | str | Iterable[FeedbackType | str],
        *,
        previous_modules: Iterable[str] = (),
    ) -> RoutingDecision:
        feedback_types = self._feedback_names(feedback)
        previous = tuple(dict.fromkeys(str(item) for item in previous_modules))
        previous_set = set(previous)
        selected: list[ModuleSpec] = []
        skipped: list[str] = []
        matched_reasons: list[str] = []

        # Structured feedback is ordered from specific diagnosis to generic
        # fallback.  Stop after the first category that has an undisclosed
        # module so a generic ``test_failure`` cannot fan out the decision.
        for feedback_type in feedback_types:
            category_selected: list[ModuleSpec] = []
            for module in self.manifest.modules:
                if feedback_type not in module.triggers:
                    continue
                if module.module_id in previous_set and not self.allow_revisit:
                    skipped.append(module.module_id)
                    continue
                category_selected.append(module)
                if len(category_selected) >= self.max_modules_per_decision:
                    break
            if category_selected:
                selected.extend(category_selected)
                matched_reasons.extend(
                    f"{module.module_id} matched {feedback_type}"
                    for module in category_selected
                )
                break

        if matched_reasons:
            reason = "; ".join(matched_reasons)
        elif skipped:
            reason = "all matching modules were previously disclosed"
        else:
            reason = f"no manifest trigger matched {', '.join(feedback_types)}"
        return RoutingDecision(
            feedback_types=feedback_types,
            modules=tuple(selected),
            previous_modules=previous,
            skipped_previous_modules=tuple(dict.fromkeys(skipped)),
            reason=reason,
        )

    def disclose(self, decision: RoutingDecision) -> tuple[DisclosedModule, ...]:
        disclosed: list[DisclosedModule] = []
        for module in decision.modules:
            path = self.manifest.resolve_module_path(module)
            if not path.is_file():
                raise ManifestError(f"module file does not exist: {path}")
            content = path.read_text(encoding="utf-8")
            disclosed.append(
                DisclosedModule(
                    module_id=module.module_id,
                    path=path,
                    content=content,
                    estimated_tokens=math.ceil(len(content) / 4),
                )
            )
        return tuple(disclosed)

    @staticmethod
    def _feedback_names(
        feedback: FeedbackClassification | FeedbackType | str | Iterable[FeedbackType | str],
    ) -> tuple[str, ...]:
        if isinstance(feedback, FeedbackClassification):
            return feedback.trigger_names
        if isinstance(feedback, FeedbackType | str):
            return feedback_type_names((feedback,))
        return feedback_type_names(feedback)
