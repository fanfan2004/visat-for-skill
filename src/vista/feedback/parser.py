"""Parse agent-observable output into stable feedback categories.

The parser deliberately accepts only sources the evaluated agent can observe.  It
has no adapter for verifier internals, expected answers, or private benchmark
metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FeedbackSource(StrEnum):
    TEST_OUTPUT = "test_output"
    SHELL_STDOUT = "shell_stdout"
    SHELL_STDERR = "shell_stderr"
    TOOL_RESULT = "tool_result"
    RUNTIME_EXCEPTION = "runtime_exception"
    AGENT_PUBLIC_CHECK = "agent_public_check"
    PUBLIC_CHECK = "agent_public_check"


class FeedbackType(StrEnum):
    ASSERTION_ERROR = "assertion_error"
    TEST_FAILURE = "test_failure"
    SCHEMA_ERROR = "schema_error"
    WRONG_FORMAT = "wrong_format"
    INCOMPLETE_OUTPUT = "incomplete_output"
    DEPENDENCY_ERROR = "dependency_error"
    COMPILATION_ERROR = "compilation_error"
    TIMEOUT = "timeout"
    RUNTIME_ERROR = "runtime_error"
    COMMAND_FAILURE = "command_failure"
    REPEATED_FAILURE = "repeated_failure"
    # Public, task-domain failure classes for multi-module routing. They carry
    # no verifier assertion text or reference-answer content.
    GEOGRAPHIC_CONSTRAINT = "geographic_constraint"
    ACCOMMODATION_CONSTRAINT = "accommodation_constraint"
    CUISINE_CONSTRAINT = "cuisine_constraint"
    ATTRACTION_CONSTRAINT = "attraction_constraint"
    TRANSPORTATION_CONSTRAINT = "transportation_constraint"
    FLIGHT_REQUIRED = "flight_required"
    SUCCESS = "success"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FeedbackObservation:
    source: FeedbackSource
    text: str
    exit_code: int | None = None
    command: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            source = FeedbackSource(self.source)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in FeedbackSource)
            raise ValueError(
                f"feedback source must be agent-observable; allowed: {allowed}"
            ) from exc
        object.__setattr__(self, "source", source)
        if not isinstance(self.text, str):
            raise TypeError("feedback text must be a string")


@dataclass(frozen=True, slots=True)
class FeedbackClassification:
    primary_type: FeedbackType
    feedback_types: tuple[FeedbackType, ...]
    source: FeedbackSource
    reason: str
    fingerprint: str
    occurrence: int
    excerpt: str

    @property
    def is_failure(self) -> bool:
        return self.primary_type not in {FeedbackType.SUCCESS, FeedbackType.UNKNOWN}

    @property
    def trigger_names(self) -> tuple[str, ...]:
        return tuple(item.value for item in self.feedback_types)


@dataclass(frozen=True, slots=True)
class _Rule:
    feedback_type: FeedbackType
    pattern: re.Pattern[str]


_RULES: tuple[_Rule, ...] = (
    _Rule(
        FeedbackType.SCHEMA_ERROR,
        re.compile(
            r"\b(schema(?:\s+validation)?\s*(?:error|failed|invalid)|jsonschema|"
            r"required propert(?:y|ies)|additional properties? (?:are|is) not allowed)\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.WRONG_FORMAT,
        re.compile(
            r"\b(invalid json|expected (?:json|yaml|csv|format)|wrong format|"
            r"malformed (?:json|yaml|xml)|parse error|parsing failed)\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.ASSERTION_ERROR,
        re.compile(r"\b(assertionerror|assertion failed|assert .* failed)\b", re.IGNORECASE),
    ),
    _Rule(
        FeedbackType.TEST_FAILURE,
        re.compile(
            r"(?:\btests? failed\b|\bfailed tests?\b|=+\s*failures?\s*=+|"
            r"\b\d+ failed(?:,|\s|$)|\bFAIL:\s)",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.INCOMPLETE_OUTPUT,
        re.compile(
            r"\b(incomplete output|missing (?:field|key|section|value|output)|"
            r"output (?:is )?truncated|premature end|unexpected end of (?:file|input))\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.DEPENDENCY_ERROR,
        re.compile(
            r"\b(modulenotfounderror|importerror|no module named|dependency .* missing|"
            r"package .* not found)\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.COMPILATION_ERROR,
        re.compile(
            r"\b(compilation failed|compile error|compiler error|syntax error|"
            r"not found: (?:type|value)|cannot find symbol)\b",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        FeedbackType.TIMEOUT,
        re.compile(r"\b(timed? out|timeout(?:error)?|deadline exceeded)\b", re.IGNORECASE),
    ),
    _Rule(
        FeedbackType.RUNTIME_ERROR,
        re.compile(
            r"(?:\btraceback \(most recent call last\)|\b(?:type|value|key|index|runtime)error\b|"
            r"\bexception\b|\bsegmentation fault\b)",
            re.IGNORECASE,
        ),
    ),
)

_SUCCESS_PATTERN = re.compile(
    r"(?:\b(?:all tests? )?passed\b|\b\d+ passed(?:,|\s|$)|\bsuccess(?:ful(?:ly)?)?\b)",
    re.IGNORECASE,
)


class FeedbackParser:
    """Stateful parser that can identify repetition within a trial."""

    def __init__(self, *, excerpt_limit: int = 1000) -> None:
        if excerpt_limit < 1:
            raise ValueError("excerpt_limit must be positive")
        self.excerpt_limit = excerpt_limit
        self._occurrences: dict[tuple[str, str], int] = {}

    def parse(
        self,
        observation: FeedbackObservation,
        *,
        trial_id: str = "default",
    ) -> FeedbackClassification:
        if not trial_id or not trial_id.strip():
            raise ValueError("trial_id must not be empty")

        matches = self._structured_feedback_types(observation.text)
        if not matches:
            matches = [
                rule.feedback_type for rule in _RULES if rule.pattern.search(observation.text)
            ]
        if not matches and observation.exit_code not in (None, 0):
            matches.append(FeedbackType.COMMAND_FAILURE)
        if not matches and _SUCCESS_PATTERN.search(observation.text):
            matches.append(FeedbackType.SUCCESS)
        if not matches:
            matches.append(FeedbackType.UNKNOWN)

        fingerprint = self._fingerprint(observation, matches[0])
        key = (trial_id, fingerprint)
        occurrence = self._occurrences.get(key, 0) + 1
        self._occurrences[key] = occurrence

        feedback_types = list(dict.fromkeys(matches))
        if occurrence > 1 and matches[0] not in {FeedbackType.SUCCESS, FeedbackType.UNKNOWN}:
            feedback_types.append(FeedbackType.REPEATED_FAILURE)

        labels = ", ".join(item.value for item in feedback_types)
        reason = f"{observation.source.value} matched: {labels}"
        return FeedbackClassification(
            primary_type=matches[0],
            feedback_types=tuple(feedback_types),
            source=observation.source,
            reason=reason,
            fingerprint=fingerprint,
            occurrence=occurrence,
            excerpt=observation.text[: self.excerpt_limit],
        )

    @staticmethod
    def _structured_feedback_types(text: str) -> list[FeedbackType]:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(payload, Mapping):
            return []
        if payload.get("kind") != "vista_public_verifier_feedback":
            return []
        values = payload.get("feedback_types")
        if not isinstance(values, list):
            return []
        allowed_failures = set(FeedbackType) - {
            FeedbackType.SUCCESS,
            FeedbackType.UNKNOWN,
            FeedbackType.REPEATED_FAILURE,
        }
        parsed: list[FeedbackType] = []
        for value in values:
            try:
                feedback_type = FeedbackType(str(value).strip().casefold())
            except ValueError:
                continue
            if feedback_type in allowed_failures:
                parsed.append(feedback_type)
        return list(dict.fromkeys(parsed))

    def reset_trial(self, trial_id: str) -> None:
        self._occurrences = {
            key: count for key, count in self._occurrences.items() if key[0] != trial_id
        }

    @staticmethod
    def _fingerprint(
        observation: FeedbackObservation,
        primary_type: FeedbackType,
    ) -> str:
        normalized = observation.text.lower()
        normalized = re.sub(r"[a-z]:\\[^\s:]+|/(?:[^\s/]+/)+[^\s:]+", "<path>", normalized)
        normalized = re.sub(r"\bline\s+\d+\b", "line <n>", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?(?:ms|s)?\b", "<n>", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        payload = f"{observation.source.value}\0{primary_type.value}\0{normalized}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def feedback_type_names(items: Iterable[FeedbackType | str]) -> tuple[str, ...]:
    """Normalize feedback types for router integration."""

    return tuple(
        item.value if isinstance(item, FeedbackType) else str(item).strip().lower()
        for item in items
    )
