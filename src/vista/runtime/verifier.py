"""Verifier execution classification and readiness gating.

Verifier output is a boundary between infrastructure and task evidence.  This
module keeps that boundary typed so infrastructure failures cannot accidentally
be routed as agent-visible feedback.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class VerifierClass(StrEnum):
    PASS = "pass"
    TASK_FAILURE = "task_failure"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    SETUP_TIMEOUT = "setup_timeout"
    NETWORK_ERROR = "network_error"
    VERIFIER_TIMEOUT = "verifier_timeout"


class VerifierPhase(StrEnum):
    READINESS = "readiness"
    SOFT = "soft_verify"
    FINAL = "final_verify"


class VerifierEvidenceChannel(StrEnum):
    """Evidence provenance used to keep task output separate from harness errors."""

    PUBLIC_OUTPUT = "public_output"
    ERROR = "error"
    RUNTIME_EXCEPTION = "runtime_exception"
    TIMEOUT = "timeout"
    STRUCTURED = "structured"


@dataclass(frozen=True, slots=True)
class PublicVerifierFeedback:
    """Small, agent-visible verifier summary with hidden assertion values removed."""

    feedback_types: tuple[str, ...]
    failing_checks: tuple[str, ...]
    summary: str
    source_artifact: str
    schema_version: int = 1
    kind: str = "vista_public_verifier_feedback"
    outcome: str = "fail"

    def to_text(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "kind": self.kind,
                "outcome": self.outcome,
                "feedback_types": list(self.feedback_types),
                "failing_checks": list(self.failing_checks),
                "summary": self.summary,
                "source_artifact": self.source_artifact,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


# Distinct from ordinary task failure (1): the runner must quarantine the
# record and formal schedulers must stop rather than continue silently.
QUARANTINE_EXIT_CODE = 2


_NETWORK = re.compile(
    r"\b(?:network|connection|connect|dns|name resolution|proxy|tls|ssl|http\s+[45]\d\d|"
    r"temporary failure in name resolution|connection reset|connection refused)\b",
    re.IGNORECASE,
)
_SETUP = re.compile(
    r"\b(?:setup|install|dependency|cold[- ]?start|bootstrap|prewarm|package|apt|pip|npm|"
    r"module not found|no module named|soft verifier timed)\b",
    re.IGNORECASE,
)
_TIMEOUT = re.compile(r"\b(?:timed?\s*out|timeout|deadline exceeded)\b", re.IGNORECASE)
_INFRA = re.compile(
    r"\b(?:infrastructure|internal server|docker|container|sandbox|harness|runner|"
    r"permission denied|exec failed|process not found)\b",
    re.IGNORECASE,
)

_CHECK_NAME_SAFE = re.compile(r"[^A-Za-z0-9_.:/\[\]-]+")
_STRUCTURE_CHECK = re.compile(r"schema|structure|column|field|key|format", re.IGNORECASE)
_MISSING_CHECK = re.compile(
    r"missing|exists?|present|leftover|empty|required|output_structure|files?_", re.IGNORECASE
)
_DEPENDENCY_CHECK = re.compile(r"dependency|package|module|import", re.IGNORECASE)
_COMPILATION_CHECK = re.compile(
    r"compil|syntax error|not found: (?:type|value)|cannot find symbol", re.IGNORECASE
)
_RUNTIME_CHECK = re.compile(r"build|render|browser|runtime", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class VerifierObservation:
    """Publicly auditable result of one verifier phase."""

    phase: VerifierPhase
    evidence_channel: VerifierEvidenceChannel
    classification: VerifierClass
    strict_pass: bool | None
    return_code: int | None
    timed_out: bool
    timeout_source: str | None
    duration_ms: float
    public_output: str | None = None
    error_summary: str | None = None
    reward: Any = None

    @property
    def infrastructure_error(self) -> bool:
        return self.classification in {
            VerifierClass.INFRASTRUCTURE_ERROR,
            VerifierClass.SETUP_TIMEOUT,
            VerifierClass.NETWORK_ERROR,
            VerifierClass.VERIFIER_TIMEOUT,
        }

    @property
    def task_failure(self) -> bool:
        return self.classification is VerifierClass.TASK_FAILURE

    @property
    def feedback_allowed(self) -> bool:
        # Readiness is an infrastructure preflight boundary, even when the
        # untouched workspace simply fails a task-level check.  There is no
        # agent attempt to receive readiness output.
        return self.task_failure and self.phase is not VerifierPhase.READINESS

    @property
    def quarantine(self) -> bool:
        return self.infrastructure_error

    def to_metadata(self) -> dict[str, Any]:
        return {
            "verifier_phase": self.phase.value,
            "evidence_channel": self.evidence_channel.value,
            "verifier_class": self.classification.value,
            "infrastructure_class": (
                self.classification.value if self.infrastructure_error else None
            ),
            "return_code": self.return_code,
            "timed_out": self.timed_out,
            "timeout_source": self.timeout_source,
            "duration_ms": self.duration_ms,
            "public_output": self.public_output,
            "error_summary": self.error_summary,
            "reward": self.reward,
            "feedback_allowed": self.feedback_allowed,
            "quarantine": self.quarantine,
        }


def classify_verifier_result(
    *,
    phase: VerifierPhase | str,
    reward: Any = None,
    public_output: str | None = None,
    error: str | None = None,
    evidence_channel: VerifierEvidenceChannel | str | None = None,
    return_code: int | None = None,
    timed_out: bool = False,
    timeout_source: str | None = None,
    duration_ms: float = 0.0,
) -> VerifierObservation:
    """Classify a verifier result without exposing infrastructure as feedback."""

    phase_value = VerifierPhase(phase)
    error_text = str(error or "")
    output_text = str(public_output or "")
    channel = _resolve_evidence_channel(
        evidence_channel,
        error_text=error_text,
        output_text=output_text,
        timed_out=timed_out,
    )

    # A timeout flag/source is structured harness evidence.  Do not infer it
    # from arbitrary public task text after readiness has passed.
    timeout = bool(timed_out or channel is VerifierEvidenceChannel.TIMEOUT)

    if phase_value is VerifierPhase.READINESS:
        # Readiness is allowed to classify dependency/bootstrap/network output
        # as infrastructure because no model attempt has started yet.
        readiness_text = f"{error_text}\n{output_text}"
        if timeout or _TIMEOUT.search(readiness_text):
            classification = VerifierClass.SETUP_TIMEOUT
        elif _NETWORK.search(readiness_text):
            classification = VerifierClass.NETWORK_ERROR
        elif _SETUP.search(readiness_text) or _INFRA.search(readiness_text):
            classification = VerifierClass.INFRASTRUCTURE_ERROR
        else:
            classification = _classify_task_outcome(
                reward=reward,
                return_code=return_code,
                error_text=error_text,
            )
    elif timeout:
        classification = VerifierClass.VERIFIER_TIMEOUT
    elif channel in {
        VerifierEvidenceChannel.ERROR,
        VerifierEvidenceChannel.RUNTIME_EXCEPTION,
    } and error_text:
        # Runtime/harness error channels are never task feedback.  This is
        # intentionally independent of setup/network keywords.
        if _TIMEOUT.search(error_text):
            classification = VerifierClass.VERIFIER_TIMEOUT
        elif _NETWORK.search(error_text):
            classification = VerifierClass.NETWORK_ERROR
        else:
            classification = VerifierClass.INFRASTRUCTURE_ERROR
    else:
        # SOFT/FINAL public output is task evidence.  Terms such as
        # ModuleNotFoundError, package failure, or connection refused may be
        # caused by the produced artifact and must not quarantine by keyword.
        classification = _classify_task_outcome(
            reward=reward,
            return_code=return_code,
            error_text=error_text,
        )

    strict_pass = classification is VerifierClass.PASS
    return VerifierObservation(
        phase=phase_value,
        evidence_channel=channel,
        classification=classification,
        strict_pass=strict_pass,
        return_code=return_code,
        timed_out=timeout,
        timeout_source=timeout_source if timeout else None,
        duration_ms=max(0.0, float(duration_ms)),
        public_output=output_text or None,
        error_summary=error_text or None,
        reward=reward,
    )


def classify_command_result(
    result: Any,
    *,
    phase: VerifierPhase | str,
    timeout_source: str | None = "subprocess",
    duration_ms: float | None = None,
) -> VerifierObservation:
    """Classify a command-style verifier result using only public output."""

    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    return_code = getattr(result, "returncode", getattr(result, "return_code", None))
    timed_out = bool(getattr(result, "timed_out", False))
    if duration_ms is None:
        duration_ms = float(getattr(result, "duration_ms", 0.0) or 0.0)
    return classify_verifier_result(
        phase=phase,
        reward=1.0 if return_code == 0 else 0.0,
        public_output=stdout,
        error=stderr,
        evidence_channel=(
            VerifierEvidenceChannel.TIMEOUT
            if timed_out
            else (
                VerifierEvidenceChannel.ERROR
                if stderr
                else (
                    VerifierEvidenceChannel.PUBLIC_OUTPUT
                    if stdout
                    else VerifierEvidenceChannel.STRUCTURED
                )
            )
        ),
        return_code=int(return_code) if return_code is not None else None,
        timed_out=timed_out,
        timeout_source=timeout_source,
        duration_ms=duration_ms,
    )


async def run_runtime_verifier(
    operation: Awaitable[Any],
    *,
    phase: VerifierPhase | str,
    timeout_sec: int,
    timeout_source: str = "runtime.wait_for",
) -> VerifierObservation:
    """Run BenchFlow's verifier with a phase-specific wall-clock budget."""

    started = time.monotonic()
    phase_value = VerifierPhase(phase)
    try:
        value = await asyncio.wait_for(operation, timeout=timeout_sec)
    except TimeoutError:
        return classify_verifier_result(
            phase=phase_value,
            error=f"{phase_value.value} verifier timed out after {timeout_sec}s",
            evidence_channel=VerifierEvidenceChannel.TIMEOUT,
            return_code=124,
            timed_out=True,
            timeout_source=timeout_source,
            duration_ms=(time.monotonic() - started) * 1000,
        )
    except Exception as exc:  # boundary: never let raw verifier errors become feedback
        return classify_verifier_result(
            phase=phase_value,
            error=str(exc),
            evidence_channel=VerifierEvidenceChannel.RUNTIME_EXCEPTION,
            return_code=1,
            duration_ms=(time.monotonic() - started) * 1000,
        )

    reward, public_output, error = _unpack_runtime_result(value)
    return classify_verifier_result(
        phase=phase_value,
        reward=reward,
        public_output=public_output,
        error=error,
        evidence_channel=(
            VerifierEvidenceChannel.RUNTIME_EXCEPTION
            if error
            else (
                VerifierEvidenceChannel.PUBLIC_OUTPUT
                if public_output
                else VerifierEvidenceChannel.STRUCTURED
            )
        ),
        return_code=0 if _reward_is_pass(reward) and not error else 1,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def build_public_verifier_feedback(
    *,
    ctrf_text: str | None = None,
    output_log: str | None = None,
) -> PublicVerifierFeedback:
    """Build a diagnostic summary without publishing assertion messages or traces.

    CTRF test names are public check identifiers.  Assertion values, messages,
    traces and expected answers are deliberately ignored.
    """

    failing_checks = _failed_ctrf_check_names(ctrf_text)
    source_artifact = "ctrf.json" if failing_checks else "output.log"
    if not failing_checks:
        failing_checks = ("verifier_output_log",)
    evidence = " ".join(failing_checks)
    if output_log and source_artifact == "output.log":
        # Use log text for category inference only; never copy it into feedback.
        evidence = f"{evidence} {output_log}"
    feedback_types = _infer_public_feedback_types(evidence)
    count = len(failing_checks)
    noun = "check" if count == 1 else "checks"
    return PublicVerifierFeedback(
        feedback_types=feedback_types,
        failing_checks=failing_checks,
        summary=f"Verifier reported {count} failing {noun}.",
        source_artifact=source_artifact,
    )


async def collect_runtime_public_feedback(
    runtime: Any,
    observation: VerifierObservation,
    *,
    timeout_sec: int = 10,
) -> PublicVerifierFeedback | None:
    """Read verifier-owned diagnostic artifacts after a failed public check."""

    if not observation.feedback_allowed:
        return None
    ctrf_text = await _read_runtime_artifact(
        runtime,
        "/logs/verifier/ctrf.json",
        max_bytes=262_144,
        timeout_sec=timeout_sec,
    )
    output_log = None
    if not ctrf_text:
        output_log = await _read_runtime_artifact(
            runtime,
            "/logs/verifier/output.log",
            max_bytes=65_536,
            timeout_sec=timeout_sec,
        )
    return build_public_verifier_feedback(ctrf_text=ctrf_text, output_log=output_log)


async def _read_runtime_artifact(
    runtime: Any,
    path: str,
    *,
    max_bytes: int,
    timeout_sec: int,
) -> str | None:
    command = f"if [ -f {path} ]; then head -c {int(max_bytes)} {path}; fi"
    try:
        result = await runtime.env.exec(command, timeout_sec=timeout_sec)
    except Exception:
        return None
    if bool(getattr(result, "timed_out", False)):
        return None
    if int(getattr(result, "return_code", getattr(result, "exit_code", 1))) != 0:
        return None
    text = str(getattr(result, "stdout", "") or "").strip()
    return text or None


def _failed_ctrf_check_names(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ()
    results = payload.get("results") if isinstance(payload, Mapping) else None
    tests = results.get("tests") if isinstance(results, Mapping) else None
    if not isinstance(tests, list):
        return ()
    names: list[str] = []
    for test in tests:
        if not isinstance(test, Mapping):
            continue
        status = str(test.get("status") or "").strip().casefold()
        if status not in {"failed", "fail", "error", "timedout", "timed_out"}:
            continue
        raw_name = str(test.get("name") or test.get("test") or "unnamed_check")
        raw_name = raw_name.rsplit("::", 1)[-1]
        raw_name = re.sub(r"\[[^\]]*\]$", "[case]", raw_name)
        safe_name = _CHECK_NAME_SAFE.sub("_", raw_name).strip("_")[:160]
        if safe_name:
            names.append(safe_name)
    return tuple(dict.fromkeys(names))


def _infer_public_feedback_types(evidence: str) -> tuple[str, ...]:
    types: list[str] = []
    if _STRUCTURE_CHECK.search(evidence):
        types.append("schema_error")
    if _MISSING_CHECK.search(evidence):
        types.append("incomplete_output")
    if _DEPENDENCY_CHECK.search(evidence):
        types.append("dependency_error")
    if _COMPILATION_CHECK.search(evidence):
        types.append("compilation_error")
    if _RUNTIME_CHECK.search(evidence):
        types.append("runtime_error")
    types.append("test_failure")
    return tuple(dict.fromkeys(types))


def _unpack_runtime_result(value: Any) -> tuple[Any, str | None, str | None]:
    if isinstance(value, tuple):
        parts = list(value) + [None, None, None]
        return parts[0], _optional_text(parts[1]), _optional_text(parts[2])
    reward = getattr(value, "reward", None)
    if reward is None and isinstance(value, Mapping):
        reward = value.get("reward")
    stdout = getattr(value, "stdout", None)
    error = getattr(value, "error", None)
    if isinstance(value, Mapping):
        stdout = stdout or value.get("stdout") or value.get("public_output")
        error = error or value.get("error")
    return reward, _optional_text(stdout), _optional_text(error)


def _reward_is_pass(value: Any) -> bool:
    try:
        return float(value) >= 1.0
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            try:
                return float(value.get("reward")) >= 1.0
            except (TypeError, ValueError):
                return False
        return False


def _resolve_evidence_channel(
    value: VerifierEvidenceChannel | str | None,
    *,
    error_text: str,
    output_text: str,
    timed_out: bool,
) -> VerifierEvidenceChannel:
    if value is not None:
        return VerifierEvidenceChannel(value)
    if timed_out:
        return VerifierEvidenceChannel.TIMEOUT
    if error_text:
        return VerifierEvidenceChannel.ERROR
    if output_text:
        return VerifierEvidenceChannel.PUBLIC_OUTPUT
    return VerifierEvidenceChannel.STRUCTURED


def _classify_task_outcome(
    *,
    reward: Any,
    return_code: int | None,
    error_text: str,
) -> VerifierClass:
    if _reward_is_pass(reward) and not error_text and return_code in (None, 0):
        return VerifierClass.PASS
    return VerifierClass.TASK_FAILURE


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "PublicVerifierFeedback",
    "QUARANTINE_EXIT_CODE",
    "VerifierClass",
    "VerifierEvidenceChannel",
    "VerifierObservation",
    "VerifierPhase",
    "classify_command_result",
    "classify_verifier_result",
    "build_public_verifier_feedback",
    "collect_runtime_public_feedback",
    "run_runtime_verifier",
]
