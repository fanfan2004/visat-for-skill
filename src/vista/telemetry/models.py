"""Typed, forward-compatible telemetry event model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from vista.audit import redact_secret

_SECRET_KEYS = {"api_key", "apikey", "secret", "password", "authorization"}


class EventType(StrEnum):
    TRIAL_START = "trial_start"
    ROOT_SKILL_LOADED = "root_skill_loaded"
    FEEDBACK = "feedback"
    FEEDBACK_CLASSIFIED = "feedback_classified"
    ROUTING = "routing"
    DISCLOSURE = "disclosure"
    MODULE_ACCESS = "module_access"
    AGENT_ATTEMPT = "agent_attempt"
    AGENT_ACTION = "agent_action"
    REPAIR = "repair"
    LOOP_STOP = "loop_stop"
    VALIDATION = "validation"
    VALIDATION_RESULT = "validation_result"
    TEST_RESULT = "test_result"
    MODEL_USAGE = "model_usage"
    RUNTIME_IDENTITY = "runtime_identity"
    VERIFIER_RESULT = "verifier_result"
    FINAL_RESULT = "final_result"
    TRIAL_END = "trial_end"


@dataclass(frozen=True, slots=True)
class EventRecord:
    trial_id: str
    step: int
    event_type: EventType
    task_id: str | None = None
    condition: str | None = None
    trial_index: int | None = None
    seed: int | None = None
    elapsed_ms: float | None = None
    feedback_type: str | None = None
    selected_modules: tuple[str, ...] = ()
    module_id: str | None = None
    reason: str | None = None
    estimated_tokens: int | None = None
    previous_modules: tuple[str, ...] = ()
    progress_after_event: bool | None = None
    agent_used: bool | None = None
    repair_observed: bool | None = None
    strict_pass: bool | None = None
    timed_out: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    failure_category: str | None = None
    source: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.trial_id or not self.trial_id.strip():
            raise ValueError("trial_id must not be empty")
        if self.step < 0:
            raise ValueError("step must be non-negative")
        try:
            event_type = EventType(self.event_type)
        except ValueError as exc:
            raise ValueError(f"unknown event_type: {self.event_type!r}") from exc
        object.__setattr__(self, "event_type", event_type)
        if self.trial_index is not None and self.trial_index < 0:
            raise ValueError("trial_index must be non-negative")
        if self.elapsed_ms is not None and self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative")
        if self.estimated_tokens is not None and self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        sanitized = _sanitize_json(self.metadata)
        try:
            json.dumps(sanitized, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("metadata must be JSON-serializable") from exc
        object.__setattr__(self, "metadata", sanitized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "trial_id": self.trial_id,
            "step": self.step,
            "event_type": self.event_type.value,
            "task_id": self.task_id,
            "condition": self.condition,
            "trial_index": self.trial_index,
            "seed": self.seed,
            "elapsed_ms": self.elapsed_ms,
            "feedback_type": self.feedback_type,
            "selected_modules": list(self.selected_modules),
            "module_id": self.module_id,
            "reason": self.reason,
            "estimated_tokens": self.estimated_tokens,
            "previous_modules": list(self.previous_modules),
            "progress_after_event": self.progress_after_event,
            "agent_used": self.agent_used,
            "repair_observed": self.repair_observed,
            "strict_pass": self.strict_pass,
            "timed_out": self.timed_out,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "failure_category": self.failure_category,
            "source": self.source,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EventRecord:
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            event_id=str(raw.get("event_id") or uuid4()),
            timestamp=str(raw.get("timestamp") or datetime.now(UTC).isoformat()),
            trial_id=str(raw["trial_id"]),
            step=int(raw["step"]),
            event_type=EventType(str(raw["event_type"])),
            task_id=_optional_string(raw.get("task_id")),
            condition=_optional_string(raw.get("condition")),
            trial_index=(
                int(raw["trial_index"]) if raw.get("trial_index") is not None else None
            ),
            seed=int(raw["seed"]) if raw.get("seed") is not None else None,
            elapsed_ms=(
                float(raw["elapsed_ms"]) if raw.get("elapsed_ms") is not None else None
            ),
            feedback_type=_optional_string(raw.get("feedback_type")),
            selected_modules=tuple(str(item) for item in raw.get("selected_modules", ())),
            module_id=_optional_string(raw.get("module_id")),
            reason=_optional_string(raw.get("reason")),
            estimated_tokens=(
                int(raw["estimated_tokens"]) if raw.get("estimated_tokens") is not None else None
            ),
            previous_modules=tuple(str(item) for item in raw.get("previous_modules", ())),
            progress_after_event=raw.get("progress_after_event"),
            agent_used=raw.get("agent_used"),
            repair_observed=raw.get("repair_observed"),
            strict_pass=raw.get("strict_pass"),
            timed_out=raw.get("timed_out"),
            input_tokens=(
                int(raw["input_tokens"]) if raw.get("input_tokens") is not None else None
            ),
            output_tokens=(
                int(raw["output_tokens"]) if raw.get("output_tokens") is not None else None
            ),
            total_tokens=(
                int(raw["total_tokens"]) if raw.get("total_tokens") is not None else None
            ),
            cost_usd=float(raw["cost_usd"]) if raw.get("cost_usd") is not None else None,
            failure_category=_optional_string(raw.get("failure_category")),
            source=_optional_string(raw.get("source")),
            metadata=raw.get("metadata", {}),
        )


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _sanitize_json(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(item_key): (
                "<redacted-secret>"
                if str(item_key).casefold() in _SECRET_KEYS
                else _sanitize_json(item_value, key=str(item_key))
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json(item, key=key) for item in value]
    if isinstance(value, str):
        return redact_secret(value)
    return value
