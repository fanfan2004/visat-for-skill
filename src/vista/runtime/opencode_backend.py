"""OpenCode agent backend for the BenchFlow pilot.

OpenCode runs inside the BenchFlow task container.  The API key is injected
only into the individual OpenCode process through the sandbox ``exec`` API;
it is never written to the task files or command line.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from vista.audit import (
    cap_public,
    config_digest,
    endpoint_identifier,
    has_progress_signal,
    has_repair_action,
    redact_secret,
    sha256_text,
)
from vista.feedback import FeedbackParser, FeedbackSource

from .benchflow_backend import (
    _anchor_host_cwd,
    _close_runtime_preserving_prebuilt_image,
    _completion_hint,
    _container_artifact_digest,
    _container_artifact_snapshot,
    _ensure_agent_user,
    _materializer_for,
    _prepare_skill_mount,
    _record_public_feedback,
    _record_repair,
    _resolve_source_task,
    _soft_verify,
    _system_prompt,
    _upload_new_disclosures,
    _user_prompt,
    recorder_event,
)
from .materialization import Condition, MaterializationResult, TaskSpec
from .opencode_image import (
    OpenCodeImageContractError,
    load_task_image_contract,
    probe_opencode_identity,
    task_build_context_sha256,
    task_image_config_override,
    tree_sha256,
)
from .router import DisclosureRouter
from .usage import UsageAccumulator
from .verifier import (
    QUARANTINE_EXIT_CODE,
    VerifierEvidenceChannel,
    VerifierPhase,
    classify_verifier_result,
    collect_runtime_public_feedback,
)

_WORKDIR_RE = re.compile(r"^\s*WORKDIR\s+([^\s#]+)", re.IGNORECASE | re.MULTILINE)
_MODULE_ACCESS_RE = re.compile(
    r"(?:^|[\s'\"`])/(?:vista-skill|vista_skill)/modules/"
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:[/\s'\"`]|$)"
)
_OPEN_CODE_VERSION = "1.18.13"
_DEEPSEEK_PROVIDER = "deepseek-official"
_DEEPSEEK_PROVIDER_NAME = "DeepSeek Official"


class OpenCodeAPIErrorClass(StrEnum):
    AUTHENTICATION_ERROR = "authentication_error"
    MODEL_NOT_FOUND = "model_not_found"
    BAD_REQUEST = "bad_request"
    RATE_LIMIT = "rate_limit"
    NETWORK_ERROR = "network_error"
    PROVIDER_CONFIG_ERROR = "provider_config_error"
    UNKNOWN_API_ERROR = "unknown_api_error"


@dataclass(frozen=True, slots=True)
class OpenCodeConfig:
    api_key: str
    base_url: str
    model: str
    provider_id: str = _DEEPSEEK_PROVIDER
    provider_name: str = _DEEPSEEK_PROVIDER_NAME
    expected_actual_models: tuple[str, ...] = ()
    version: str = _OPEN_CODE_VERSION

    @classmethod
    def from_environment(cls) -> OpenCodeConfig:
        # VISTA_* is the explicit, provider-neutral configuration path.  The
        # legacy DeepSeek variables remain supported solely for historical runs.
        api_key = os.environ.get("VISTA_API_KEY", "").strip()
        generic = bool(api_key)
        if not api_key:
            api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "VISTA_API_KEY is not set (legacy fallback: DEEPSEEK_API_KEY)"
            )
        base_url = (os.environ.get("VISTA_BASE_URL", "").strip() if generic else os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()) or ("https://api.deepseek.com" if not generic else "")
        model = (os.environ.get("VISTA_MODEL", "").strip() if generic else os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()) or ("deepseek-v4-flash" if not generic else "")
        if not base_url or not model:
            raise RuntimeError("VISTA_BASE_URL and VISTA_MODEL are required with VISTA_API_KEY")
        if model.startswith("openai/"):
            model = model.removeprefix("openai/")
        provider_id = (os.environ.get("VISTA_PROVIDER_ID", "").strip() if generic else _DEEPSEEK_PROVIDER) or _DEEPSEEK_PROVIDER
        provider_name = (os.environ.get("VISTA_PROVIDER_NAME", "").strip() if generic else _DEEPSEEK_PROVIDER_NAME) or _DEEPSEEK_PROVIDER_NAME
        aliases = tuple(value.strip() for value in os.environ.get("VISTA_ALLOWED_ACTUAL_MODELS", "").split(",") if value.strip())
        return cls(api_key=api_key, base_url=base_url.rstrip("/"), model=model, provider_id=provider_id, provider_name=provider_name, expected_actual_models=aliases)


@dataclass(frozen=True, slots=True)
class _OpenCodeIdentity:
    requested_version: str
    actual_version: str | None
    binary_digest: str | None


def _config_summary(config: OpenCodeConfig) -> dict[str, Any]:
    return {
        "provider": config.provider_id,
        "provider_name": config.provider_name,
        "base_url_id": endpoint_identifier(config.base_url),
        "model_requested": config.model,
        "version_requested": config.version,
        "temperature": 0,
        "seed_controlled": False,
        "seed_support": "not exposed by OpenCode CLI adapter",
    }


@dataclass(frozen=True, slots=True)
class _ToolOutput:
    text: str
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class _OpenCodeResult:
    return_code: int
    session_id: str | None
    tool_calls: int
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    stop_reason: str | None
    final_text: str
    error: str | None
    tool_outputs: tuple[_ToolOutput, ...]
    module_accesses: tuple[str, ...] = ()
    tool_commands: tuple[str, ...] = ()
    actual_model_ids: tuple[str, ...] = ()
    provider_ids: tuple[str, ...] = ()
    api_error_class: str | None = None
    api_error_status_code: int | None = None
    api_error_summary: str | None = None
    api_key_present: bool | None = None
    timed_out: bool = False
    duration_ms: float = 0.0


async def run_opencode_trial(
    args: SimpleNamespace,
    *,
    project_root: Path,
    task: TaskSpec,
    condition: Condition,
    result: MaterializationResult,
    recorder: Any,
    router: DisclosureRouter | None,
) -> int:
    """Run one trial with OpenCode inside the BenchFlow sandbox."""

    source_task = _resolve_source_task(project_root, task)
    parser = FeedbackParser()
    disclosed: list[str] = []
    runtime: Any | None = None
    final_pass = False
    attempts_completed = 0
    stop_reason: str | None = None
    quarantined = False
    quarantine_reason: str | None = None
    quarantine_timed_out = False
    usage_totals = UsageAccumulator()
    session_id: str | None = None
    config = OpenCodeConfig.from_environment()
    identity = _OpenCodeIdentity(config.version, None, None)
    image_contract = None
    benchflow_task: Path | None = None
    config_summary = _config_summary(config)
    last_feedback = (
        "Verifier feedback was not available. Re-check the task outputs and run the "
        "public checks."
    )

    try:
        from benchflow.rollout import TaskRuntime, TaskRuntimeConfig
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "opencode-benchflow backend requires the bundled BenchFlow Python "
            "(.tools/benchflow/tools/benchflow/bin/python)"
        ) from exc

    async def anchor_hook(_env: Any) -> None:
        _anchor_host_cwd(project_root)

    try:
        image_manifest_path = getattr(args, "image_manifest_path", None)
        if not image_manifest_path:
            raise OpenCodeImageContractError(
                "formal OpenCode trial requires a qualified image registry"
            )
        image_contract = load_task_image_contract(
            Path(image_manifest_path), task.task_id, project_root=project_root
        )
        if tree_sha256(source_task) != image_contract.source_task_digest:
            raise OpenCodeImageContractError(
                f"vendor task source digest mismatch for {task.task_id}"
            )
        benchflow_task = Path(image_contract.stage_root)
        if not benchflow_task.is_dir():
            raise OpenCodeImageContractError(
                f"qualified task image build context is missing: {benchflow_task}"
            )
        if task_build_context_sha256(benchflow_task) != image_contract.build_context_digest:
            raise OpenCodeImageContractError(
                f"task image build context digest mismatch for {task.task_id}"
            )
        if image_contract.opencode_version != config.version:
            raise OpenCodeImageContractError(
                f"task image OpenCode version mismatch for {task.task_id}"
            )
        runtime = await TaskRuntime.create(
            TaskRuntimeConfig(
                task_path=benchflow_task,
                environment="docker",
                sandbox_user="agent",
                sandbox_setup_timeout=getattr(args, "build_timeout_sec", 180),
                job_name=f"vista-{args.trial_id}",
                rollout_name=args.trial_id,
                jobs_dir=project_root / "data" / "raw" / "_benchflow_jobs",
                pre_agent_hooks=[anchor_hook],
                skill_mode="no-skill",
                config_override=task_image_config_override(image_contract.image_tag),
            )
        )
        await _ensure_agent_user(runtime, project_root)
        workspace = await _repair_runtime_workspace(runtime, benchflow_task)
        identity = await probe_opencode_identity(
            runtime,
            workspace=workspace,
            expected_version=config.version,
            expected_digest=image_contract.opencode_binary_digest,
        )
        recorder.emit(
            recorder_event("RUNTIME_IDENTITY"),
            source="opencode_image",
            metadata={
                "task_id": task.task_id,
                "opencode_requested_version": config.version,
                "opencode_actual_version": identity.actual_version,
                "opencode_binary_sha256": identity.binary_digest,
                "task_image_ref": image_contract.immutable_image_ref,
                "task_image_digest": image_contract.image_digest,
                "vendor_dockerfile_sha256": image_contract.vendor_dockerfile_digest,
                "staged_dockerfile_sha256": image_contract.staged_dockerfile_digest,
                "build_context_sha256": image_contract.build_context_digest,
                "identity_probe_exit_code": identity.exit_code,
                "identity_probe_duration_ms": identity.duration_ms,
                "identity_probe_timed_out": identity.timed_out,
            },
        )
        config_summary["config_digest"] = await _write_opencode_config(runtime, workspace, config)
        await _prepare_skill_mount(runtime, result, condition, project_root)
    except Exception as exc:
        message = redact_secret(str(exc))
        recorder.emit(
            recorder_event("AGENT_ACTION"),
            agent_used=False,
            timed_out=False,
            source="opencode_agent",
            failure_category="infrastructure_error",
            metadata={"error": message, "phase": "sandbox_setup"},
        )
        recorder.emit(
            recorder_event("TRIAL_END"),
            strict_pass=None,
            timed_out=False,
            source="skillsbench_verifier",
            failure_category="infrastructure_error",
            metadata={
                "trial_status": "quarantined",
                "backend": "opencode-benchflow",
                "agent": "opencode",
                "model": config.model,
                "opencode_version": identity.actual_version or identity.requested_version,
                "opencode_binary_digest": identity.binary_digest,
                "model_provider": config.provider_id,
                "base_url_id": endpoint_identifier(config.base_url),
                "config_digest": config_summary.get("config_digest"),
                "randomization_id": getattr(
                    args, "randomization_id", None
                ) or f"{args.trial_id}-replicate-{args.seed}",
                "seed_controlled": False,
                "disclosure_mode": "attempt-boundary",
                "agent_attempts": 0,
                "same_chat_session": False,
                "setup_error": message,
            },
        )
        if runtime is not None:
            await _close_runtime_preserving_prebuilt_image(runtime)
            runtime = None
        return QUARANTINE_EXIT_CODE

    readiness = await _soft_verify(
        runtime, args.verifier_timeout_sec, project_root, phase=VerifierPhase.READINESS
    )
    recorder.emit(
        recorder_event("VERIFIER_RESULT"),
        strict_pass=None,
        timed_out=readiness.timed_out,
        source="skillsbench_verifier_readiness",
        failure_category="infrastructure_error" if readiness.quarantine else None,
        metadata={
            "attempt_index": 0,
            "readiness_gate": True,
            "timeout_budget_sec": args.verifier_timeout_sec,
            **readiness.to_metadata(),
        },
    )
    if readiness.quarantine:
        artifact_digest, artifact_inventory = await _container_artifact_snapshot(
            runtime, workspace
        )
        recorder.emit(
            recorder_event("TRIAL_END"),
            strict_pass=None,
            timed_out=readiness.timed_out,
            source="skillsbench_verifier_readiness",
            failure_category="infrastructure_error",
            metadata={
                "trial_status": "quarantined",
                "quarantine_reason": readiness.classification.value,
                "backend": "opencode-benchflow",
                "agent_attempts": 0,
                "disclosed_modules": [],
                "artifact_digest": artifact_digest,
                "artifact_inventory": artifact_inventory,
            },
        )
        await _close_runtime_preserving_prebuilt_image(runtime)
        return QUARANTINE_EXIT_CODE

    try:
        no_progress = 0
        seen_feedback_fingerprints: set[str] = set()
        for attempt in range(1, args.max_attempts + 1):
            attempts_completed = attempt
            prompt = (
                _initial_prompt(source_task, condition)
                if attempt == 1
                else _repair_prompt(last_feedback, disclosed)
            )
            recorder.emit(
                recorder_event("AGENT_ATTEMPT"),
                agent_used=True,
                source="opencode_agent",
                metadata={
                    "attempt_index": attempt,
                    "visible_modules": list(disclosed),
                    "same_chat_session": session_id is not None,
                    "agent": "opencode",
                    "model": config.model,
                    "model_provider": config.provider_id,
                    "base_url_id": endpoint_identifier(config.base_url),
                    "opencode_version": identity.actual_version or identity.requested_version,
                    "opencode_binary_digest": identity.binary_digest,
                    "config_digest": config_summary.get("config_digest"),
                    "prompt_sha256": sha256_text(prompt),
                    "randomization_id": getattr(
                        args, "randomization_id", None
                    ) or f"{args.trial_id}-replicate-{args.seed}",
                    "seed_controlled": False,
                    "disclosure_mode": "attempt-boundary",
                },
            )
            before_disclosures = len(disclosed)
            attempt_artifact_before = await _container_artifact_digest(runtime, workspace)
            try:
                agent_result = await asyncio.wait_for(
                    _run_opencode(
                        runtime,
                        workspace,
                        config,
                        prompt,
                        session_id=session_id,
                        timeout_sec=args.agent_timeout_sec,
                    ),
                    timeout=args.agent_timeout_sec + 30,
                )
                session_id = agent_result.session_id or session_id
                usage_totals.add(
                    requests=agent_result.requests,
                    tool_calls=agent_result.tool_calls,
                    input_tokens=agent_result.input_tokens,
                    output_tokens=agent_result.output_tokens,
                )
                split_tokens = agent_result.input_tokens + agent_result.output_tokens
                if agent_result.total_tokens > split_tokens:
                    usage_totals.add(total_tokens=agent_result.total_tokens - split_tokens)
                for output in agent_result.tool_outputs:
                    _record_opencode_tool_output(
                        recorder,
                        parser,
                        router,
                        result,
                        disclosed,
                        output,
                    )
                for module_id in agent_result.module_accesses:
                    recorder.emit(
                        recorder_event("MODULE_ACCESS"),
                        module_id=module_id,
                        agent_used=True,
                        source="opencode_tool_event",
                        failure_category=(
                            "module_access_before_disclosure"
                            if module_id not in disclosed
                            else None
                        ),
                        metadata={
                            "attempt_index": attempt,
                            "evidence": "opencode_tool_event_path",
                            "disclosed_before_access": module_id in disclosed,
                            "access_path_prefix": "/vista-skill/modules/",
                            "tool_commands": list(agent_result.tool_commands),
                        },
                    )
                uploaded_modules = await _upload_new_disclosures(
                    runtime,
                    result,
                    disclosed,
                    before_disclosures,
                    project_root,
                )
                _record_disclosure_upload(
                    recorder,
                    attempt=attempt,
                    modules=uploaded_modules,
                    session_id=session_id,
                )
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=agent_result.timed_out,
                    source="opencode_agent",
                    input_tokens=agent_result.input_tokens or None,
                    output_tokens=agent_result.output_tokens or None,
                    total_tokens=agent_result.total_tokens or None,
                    metadata={
                        "attempt_index": attempt,
                        "requests": agent_result.requests,
                        "tool_calls": agent_result.tool_calls,
                        "invocation_duration_ms": agent_result.duration_ms,
                        "stop_reason": agent_result.stop_reason,
                        "session_id": session_id,
                        "return_code": agent_result.return_code,
                        "error": agent_result.error,
                        "api_error_class": agent_result.api_error_class,
                        "api_error_status_code": agent_result.api_error_status_code,
                        "api_error_summary": agent_result.api_error_summary,
                        "api_key_present": agent_result.api_key_present,
                        "actual_model_ids": list(agent_result.actual_model_ids),
                        "provider_ids": list(agent_result.provider_ids),
                        "tool_commands": list(agent_result.tool_commands),
                        "disclosure_mode": "attempt-boundary",
                        "cumulative_usage": usage_totals.snapshot(),
                    },
                )
            except TimeoutError:
                agent_result = None
                quarantine_timed_out = True
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=True,
                    source="opencode_agent",
                    failure_category="infrastructure_error",
                    metadata={
                        "attempt_index": attempt,
                        "error": "agent_timeout",
                    },
                )
            except Exception as exc:
                agent_result = None
                message = redact_secret(str(exc))
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=False,
                    source="opencode_agent",
                    failure_category="infrastructure_error",
                    metadata={"attempt_index": attempt, "error": message},
                )

            if agent_result is None:
                quarantined = True
                stop_reason = "agent_infrastructure_error"
                quarantine_reason = "agent_infrastructure_error"
                break
            terminal_reason = _agent_terminal_reason(agent_result, config)
            if terminal_reason:
                quarantined = True
                stop_reason = terminal_reason
                quarantine_reason = terminal_reason
                quarantine_timed_out = terminal_reason == "agent_timeout"
                break
            attempt_artifact_after_agent = await _container_artifact_digest(runtime, workspace)
            repair_action_observed = has_repair_action(agent_result.tool_commands)
            progress = has_progress_signal(
                artifact_before=attempt_artifact_before,
                artifact_after=attempt_artifact_after_agent,
                previous_feedback_fingerprints=seen_feedback_fingerprints,
                public_check_improved=False,
                repair_action_observed=repair_action_observed,
            )
            if attempt > 1:
                from .cli import _record_repair_observed

                _record_repair_observed(
                    recorder,
                    attempt=attempt,
                    disclosed=disclosed,
                    artifact_changed=(
                        attempt_artifact_before != attempt_artifact_after_agent
                    ),
                    feedback_changed=False,
                    repair_action_observed=repair_action_observed,
                )

            verifier_observation = await _soft_verify(
                runtime, args.verifier_timeout_sec, project_root, phase=VerifierPhase.SOFT
            )
            recorder.emit(
                recorder_event("VERIFIER_RESULT"),
                strict_pass=(
                    None
                    if verifier_observation.quarantine
                    else verifier_observation.strict_pass
                ),
                timed_out=verifier_observation.timed_out,
                source="skillsbench_verifier_soft",
                failure_category=(
                    "infrastructure_error" if verifier_observation.quarantine else None
                ),
                metadata={
                    "attempt_index": attempt,
                    "timeout_budget_sec": args.verifier_timeout_sec,
                    **verifier_observation.to_metadata(),
                },
            )
            if verifier_observation.quarantine:
                quarantined = True
                quarantine_reason = verifier_observation.classification.value
                quarantine_timed_out = verifier_observation.timed_out
                break
            if verifier_observation.strict_pass:
                final_pass = True
                break

            structured_feedback = await collect_runtime_public_feedback(
                runtime, verifier_observation
            )
            last_feedback = cap_public(
                verifier_observation.public_output
                or verifier_observation.error_summary
                or (structured_feedback.to_text() if structured_feedback else "")
            )
            if not last_feedback:
                quarantined = True
                quarantine_reason = "missing_public_verifier_feedback"
                stop_reason = quarantine_reason
                break
            before_feedback_disclosures = len(disclosed)
            feedback_state: dict[str, object] = {"fingerprints": []}
            _record_public_feedback(
                recorder,
                parser,
                last_feedback,
                FeedbackSource.TEST_OUTPUT,
                1,
                "verifier_public_feedback",
                router=router,
                result=result,
                disclosed=disclosed,
                observation_state=feedback_state,
            )
            from .cli import _is_unroutable_pd_feedback

            if _is_unroutable_pd_feedback(condition, feedback_state):
                quarantined = True
                quarantine_reason = "unroutable_public_verifier_feedback"
                stop_reason = quarantine_reason
                break
            uploaded_modules = await _upload_new_disclosures(
                runtime,
                result,
                disclosed,
                before_feedback_disclosures,
                project_root,
            )
            _record_disclosure_upload(
                recorder,
                attempt=attempt,
                modules=uploaded_modules,
                session_id=session_id,
            )
            feedback_fingerprints = feedback_state.get("fingerprints", [])
            if not isinstance(feedback_fingerprints, list):
                feedback_fingerprints = []
            feedback_changed = any(
                fingerprint not in seen_feedback_fingerprints
                for fingerprint in feedback_fingerprints
            )
            seen_feedback_fingerprints.update(feedback_fingerprints)
            progress = progress or feedback_changed
            no_progress = no_progress + 1 if not progress else 0
            if attempt == args.max_attempts or no_progress >= args.no_progress_limit:
                stop_reason = "no_progress_guard"
                break
            _record_repair(recorder, attempt + 1, disclosed)

        if quarantined:
            artifact_digest, artifact_inventory = await _container_artifact_snapshot(
                runtime, workspace
            )
            recorder.emit(
                recorder_event("TRIAL_END"),
                strict_pass=None,
                timed_out=quarantine_timed_out,
                source="skillsbench_verifier",
                failure_category=(
                    "feedback_routing_error"
                    if quarantine_reason == "unroutable_public_verifier_feedback"
                    else "infrastructure_error"
                ),
                input_tokens=usage_totals.input_tokens or None,
                output_tokens=usage_totals.output_tokens or None,
                total_tokens=usage_totals.total_tokens or None,
                metadata={
                    "trial_status": "quarantined",
                    "quarantine_reason": quarantine_reason or stop_reason,
                    "backend": "opencode-benchflow",
                    "agent": "opencode",
                    "model": config.model,
                    "agent_attempts": attempts_completed,
                    "disclosed_modules": list(disclosed),
                    "same_chat_session": session_id is not None,
                    "session_id": session_id,
                    "requests": usage_totals.requests,
                    "tool_calls": usage_totals.tool_calls,
                    "usage": usage_totals.snapshot(),
                    "disclosure_mode": "attempt-boundary",
                    "artifact_digest": artifact_digest,
                    "artifact_inventory": artifact_inventory,
                },
            )
            return QUARANTINE_EXIT_CODE

        _anchor_host_cwd(project_root)
        started = time.monotonic()
        try:
            rewards = await asyncio.wait_for(
                runtime.rollout.verify(), timeout=args.verifier_timeout_sec
            )
            verifier_error = getattr(runtime.rollout, "_verifier_error", None)
            final_observation = classify_verifier_result(
                phase=VerifierPhase.FINAL,
                reward=(rewards or {}).get("reward") if isinstance(rewards, dict) else rewards,
                error=str(verifier_error) if verifier_error else None,
                evidence_channel=(
                    VerifierEvidenceChannel.RUNTIME_EXCEPTION
                    if verifier_error
                    else VerifierEvidenceChannel.STRUCTURED
                ),
                return_code=0 if not verifier_error else 1,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        except TimeoutError:
            final_observation = classify_verifier_result(
                phase=VerifierPhase.FINAL,
                error=f"final verifier timed out after {args.verifier_timeout_sec}s",
                evidence_channel=VerifierEvidenceChannel.TIMEOUT,
                return_code=124,
                timed_out=True,
                timeout_source="runtime.wait_for",
                duration_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:
            final_observation = classify_verifier_result(
                phase=VerifierPhase.FINAL,
                error=redact_secret(str(exc)),
                evidence_channel=VerifierEvidenceChannel.RUNTIME_EXCEPTION,
                return_code=1,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        final_pass = bool(final_observation.strict_pass and not final_observation.quarantine)
        quarantined = quarantined or final_observation.quarantine
        recorder.emit(
            recorder_event("VERIFIER_RESULT"),
            strict_pass=None if final_observation.quarantine else final_pass,
            timed_out=final_observation.timed_out,
            source="skillsbench_verifier",
            failure_category="infrastructure_error" if final_observation.quarantine else None,
            metadata={
                "attempt_index": attempts_completed,
                "timeout_budget_sec": args.verifier_timeout_sec,
                **final_observation.to_metadata(),
            },
        )

        if stop_reason and not quarantined:
            recorder.emit(
                recorder_event("LOOP_STOP"),
                strict_pass=None if quarantined else final_pass,
                source="vista_adapter",
                failure_category="infrastructure_error" if quarantined else "no_progress_guard",
                metadata={
                    "reason": stop_reason,
                    "disclosed_modules": list(disclosed),
                },
            )
        artifact_digest, artifact_inventory = await _container_artifact_snapshot(
            runtime, workspace
        )
        recorder.emit(
            recorder_event("TRIAL_END"),
            strict_pass=None if quarantined else final_pass,
            timed_out=final_observation.timed_out,
            source="skillsbench_verifier",
            input_tokens=usage_totals.input_tokens or None,
            output_tokens=usage_totals.output_tokens or None,
            total_tokens=usage_totals.total_tokens or None,
            failure_category="infrastructure_error" if quarantined else None,
            metadata={
                "trial_status": (
                    "quarantined" if quarantined else ("pass" if final_pass else "fail")
                ),
                "backend": "opencode-benchflow",
                "agent": "opencode",
                "model": config.model,
                "opencode_version": identity.actual_version or identity.requested_version,
                "opencode_binary_digest": identity.binary_digest,
                "model_provider": config.provider_id,
                "base_url_id": endpoint_identifier(config.base_url),
                "config_digest": config_summary.get("config_digest"),
                "randomization_id": getattr(
                    args, "randomization_id", None
                ) or f"{args.trial_id}-replicate-{args.seed}",
                "seed_controlled": False,
                "disclosure_mode": "attempt-boundary",
                "agent_attempts": attempts_completed,
                "disclosed_modules": list(disclosed),
                "same_chat_session": session_id is not None,
                "session_id": session_id,
                "requests": usage_totals.requests,
                "tool_calls": usage_totals.tool_calls,
                "usage": usage_totals.snapshot(),
                "artifact_digest": artifact_digest,
                "artifact_inventory": artifact_inventory,
            },
        )
        return QUARANTINE_EXIT_CODE if quarantined else (0 if final_pass else 1)
    except Exception as exc:
        message = redact_secret(str(exc))
        recorder.emit(
            recorder_event("AGENT_ACTION"),
            agent_used=False,
            timed_out=False,
            source="vista_adapter",
            failure_category="infrastructure_error",
            metadata={"phase": "backend_exception", "error": message},
        )
        recorder.emit(
            recorder_event("TRIAL_END"),
            strict_pass=None,
            timed_out=False,
            source="vista_adapter",
            failure_category="infrastructure_error",
            metadata={
                "trial_status": "quarantined",
                "quarantine_reason": "backend_exception",
                "agent_attempts": attempts_completed,
                "error": message,
            },
        )
        return QUARANTINE_EXIT_CODE
    finally:
        if runtime is not None:
            _anchor_host_cwd(project_root)
            await _close_runtime_preserving_prebuilt_image(runtime)


def _initial_prompt(source_task: Path, condition: Condition) -> str:
    return (
        _system_prompt(condition, completion_hint=_completion_hint(source_task))
        + "\n\n"
        + _user_prompt(source_task, condition)
    )


def _repair_prompt(feedback: str, disclosed: list[str] | tuple[str, ...] = ()) -> str:
    module_notice = ""
    if disclosed:
        paths = ", ".join(f"/vista-skill/modules/{module}/SKILL.md" for module in disclosed)
        module_notice = (
            "\nNew VISTA modules are available for this repair attempt. Read the relevant "
            f"module before editing: {paths}.\n"
        )
    return (
        "Public verifier feedback is below. Continue the same task session and repair "
        "the artifact. Do not infer hidden answers. Use bash or the file tools to "
        "make the required filesystem changes, run public checks, and verify every "
        "requested output exists before finishing."
        + module_notice
        + "\n"
        + feedback
    )


async def _repair_runtime_workspace(runtime: Any, task_path: Path) -> str:
    """Work around images whose default cwd is stale or inaccessible."""

    candidate = str(getattr(runtime, "workspace", "") or "")
    dockerfile = task_path / "environment" / "Dockerfile"
    match = (
        _WORKDIR_RE.findall(dockerfile.read_text(encoding="utf-8"))
        if dockerfile.is_file()
        else []
    )
    declared = match[-1] if match else "/root"
    candidates = []
    if candidate.startswith("/") and "getwd:" not in candidate:
        candidates.append(candidate)
    if declared not in candidates:
        candidates.append(declared)
    for fallback in ("/root", "/app"):
        if fallback not in candidates:
            candidates.append(fallback)

    for path in candidates:
        probe = await runtime.env.exec(
            "pwd",
            cwd=path,
            user="root",
            timeout_sec=15,
        )
        code = getattr(probe, "return_code", getattr(probe, "exit_code", 1))
        if code == 0 and str(getattr(probe, "stdout", "")).strip().startswith("/"):
            runtime.rollout._agent_cwd = path
            return path
    raise RuntimeError(f"no usable sandbox workspace found; candidates={candidates}")


async def _write_opencode_config(
    runtime: Any, workspace: str, config: OpenCodeConfig
) -> str:
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            config.provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": config.provider_name,
                "options": {
                    "baseURL": config.base_url,
                    "apiKey": "{env:VISTA_API_KEY}",
                },
                "models": {config.model: {"name": config.model}},
            }
        },
        "permission": {
            "bash": {
                "*": "allow",
                "* /verifier*": "deny",
                "* /oracle*": "deny",
                "* /solution*": "deny",
                "* /logs/verifier*": "deny",
                "* *reward.txt*": "deny",
            },
            "read": {
                "*": "allow",
                "/verifier/**": "deny",
                "/oracle/**": "deny",
                "/solution/**": "deny",
                "/logs/verifier/**": "deny",
            },
            "grep": {
                "*": "allow",
                "/verifier/**": "deny",
                "/oracle/**": "deny",
                "/solution/**": "deny",
                "/logs/verifier/**": "deny",
            },
            "external_directory": {
                "*": "allow",
                "/verifier/**": "deny",
                "/oracle/**": "deny",
                "/solution/**": "deny",
                "/logs/verifier/**": "deny",
            },
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
        },
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    command = (
        f"printf %s {shlex.quote(encoded)} | base64 -d > opencode.json && "
        "chown agent:agent opencode.json"
    )
    result = await runtime.env.exec(command, cwd=workspace, user="root", timeout_sec=30)
    code = getattr(result, "return_code", getattr(result, "exit_code", 1))
    if code != 0:
        raise RuntimeError("failed to write the OpenCode provider configuration")
    return config_digest(payload)


async def _run_opencode(
    runtime: Any,
    workspace: str,
    config: OpenCodeConfig,
    prompt: str,
    *,
    session_id: str | None,
    timeout_sec: int,
) -> _OpenCodeResult:
    command = [
        "/usr/local/bin/opencode",
        "run",
        "--auto",
        "--format",
        "json",
        "--model",
        f"{config.provider_id}/{config.model}",
    ]
    if session_id:
        command.extend(["--session", session_id])
    command.append(prompt)
    started = time.monotonic()
    command_text = " ".join(shlex.quote(item) for item in command)
    # The marker proves only presence of the process-local key.  It contains
    # neither the key nor a response body and is consumed by the JSON parser.
    result = await runtime.env.exec(
        "if test -n \"$VISTA_API_KEY\"; then printf '__VISTA_API_KEY_PRESENT__=1\\n'; "
        "else printf '__VISTA_API_KEY_PRESENT__=0\\n'; fi; " + command_text,
        cwd=workspace,
        env={"VISTA_API_KEY": config.api_key},
        user="agent",
        timeout_sec=timeout_sec,
    )
    parsed = _parse_opencode_result(result)
    if parsed.duration_ms == 0.0:
        parsed = replace(parsed, duration_ms=(time.monotonic() - started) * 1000)
    return parsed


def _parse_opencode_result(result: Any) -> _OpenCodeResult:
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)

    session_id: str | None = None
    tool_calls = 0
    requests = 0
    input_tokens = 0
    output_tokens = 0
    unattributed_tokens = 0
    total_tokens = 0
    stop_reason: str | None = None
    final_text: list[str] = []
    error: str | None = None
    tool_outputs: list[_ToolOutput] = []
    module_accesses: list[str] = []
    tool_commands: list[str] = []
    actual_model_ids: list[str] = []
    provider_ids: list[str] = []
    api_key_present: bool | None = None
    error_payloads: list[object] = []

    for event in events:
        session_id = session_id or event.get("sessionID") or event.get("sessionId")
        event_type = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
        for candidate in (event.get("modelID"), part.get("modelID"), info.get("modelID")):
            if candidate:
                actual_model_ids.append(str(candidate))
        for candidate in (event.get("providerID"), part.get("providerID"), info.get("providerID")):
            if candidate:
                provider_ids.append(str(candidate))
        session_id = session_id or part.get("sessionID") or part.get("sessionId")
        if event_type in {"step_start", "step-start"}:
            requests += 1
        if event_type in {"tool_use", "tool-use", "tool_call", "tool-call"}:
            tool_calls += 1
            module_accesses.extend(_module_ids_from_event(event))
            command = _command_from_event(event)
            if command:
                tool_commands.append(command)
        if event_type in {"text", "message"}:
            text = part.get("text") or event.get("text")
            if isinstance(text, str):
                final_text.append(text)
        if event_type in {"error", "fatal"}:
            data = event.get("error") or event.get("data") or {}
            error_payloads.append(data)
            if isinstance(data, dict):
                error = str(data.get("message") or data.get("name") or data)
            else:
                error = str(data)
        if event_type in {"step_finish", "step-finish"}:
            stop_reason = str(part.get("reason") or stop_reason or "") or None
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                step_input = _as_int(tokens.get("input"))
                step_output = _as_int(tokens.get("output"))
                step_total = _as_int(tokens.get("total"))
                input_tokens += step_input
                output_tokens += step_output
                if not (step_input or step_output):
                    unattributed_tokens += step_total
        if event_type in {"tool_result", "tool-result"}:
            module_accesses.extend(_module_ids_from_event(event))
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            output = state.get("output") or state.get("result") or part.get("output")
            if isinstance(output, str) and output:
                exit_code = _as_int(
                    state.get("exitCode")
                    or state.get("exit_code")
                    or (state.get("metadata") or {}).get("exit")
                )
                tool_outputs.append(_ToolOutput(output, exit_code))

    # OpenCode's ``step_finish.part.tokens.total`` can be cumulative.  Add
    # only per-step input/output counters; retain a total-only observation
    # when the provider does not expose the split.
    total_tokens = input_tokens + output_tokens + unattributed_tokens
    marker = re.search(r"__VISTA_API_KEY_PRESENT__=(0|1)", stdout)
    if marker:
        api_key_present = marker.group(1) == "1"
    api_error_class, api_error_status_code, api_error_summary = _classify_opencode_api_error(
        error_payloads, stderr=stderr, fallback_error=error
    )

    return _OpenCodeResult(
        return_code=int(
            getattr(result, "return_code", getattr(result, "exit_code", 1))
        ),
        session_id=str(session_id) if session_id else None,
        tool_calls=tool_calls,
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        stop_reason=stop_reason,
        final_text="\n".join(final_text),
        error=api_error_summary or (redact_secret(error) if error else None),
        tool_outputs=tuple(tool_outputs),
        module_accesses=tuple(dict.fromkeys(module_accesses)),
        tool_commands=tuple(tool_commands),
        actual_model_ids=tuple(dict.fromkeys(actual_model_ids)),
        provider_ids=tuple(dict.fromkeys(provider_ids)),
        api_error_class=api_error_class,
        api_error_status_code=api_error_status_code,
        api_error_summary=api_error_summary,
        api_key_present=api_key_present,
        timed_out=bool(getattr(result, "timed_out", False)),
        duration_ms=_result_duration_ms(result),
    )


def _classify_opencode_api_error(
    payloads: list[object], *, stderr: str, fallback_error: str | None
) -> tuple[str | None, int | None, str | None]:
    """Return a bounded, secret-redacted API diagnostic; never retain bodies."""
    fields: dict[str, list[str]] = {"status": [], "code": [], "message": []}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_lower = str(key).lower()
                if key_lower in {"status", "statuscode", "status_code"}:
                    fields["status"].append(str(child))
                elif key_lower in {"code", "errorcode", "error_code"}:
                    fields["code"].append(str(child))
                elif key_lower in {"message", "detail", "error"} and not isinstance(child, (dict, list)):
                    fields["message"].append(str(child))
                elif key_lower in {"data", "responsebody", "response_body", "error"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for payload in payloads:
        visit(payload)
    if stderr:
        fields["message"].append(stderr)
    if fallback_error:
        fields["message"].append(fallback_error)
    status_code = next((int(value) for value in fields["status"] if value.isdigit()), None)
    if status_code is None:
        status_match = re.search(r"\b(?:http\s*)?(400|401|403|404|429)\b", stderr, re.IGNORECASE)
        if status_match:
            status_code = int(status_match.group(1))
    text = " ".join([*fields["code"], *fields["message"]]).lower()
    if not (payloads or stderr or fallback_error):
        return None, None, None
    if status_code in {401, 403} or any(token in text for token in ("authentication", "unauthorized", "invalid api key", "forbidden")):
        category = OpenCodeAPIErrorClass.AUTHENTICATION_ERROR.value
    elif status_code == 404 or any(token in text for token in ("model_not_found", "model not found", "unknown model")):
        category = OpenCodeAPIErrorClass.MODEL_NOT_FOUND.value
    elif status_code == 429 or "rate limit" in text:
        category = OpenCodeAPIErrorClass.RATE_LIMIT.value
    elif any(token in text for token in ("connection", "network", "dns", "timeout", "tls")):
        category = OpenCodeAPIErrorClass.NETWORK_ERROR.value
    elif status_code == 400 and any(token in text for token in ("provider", "base url", "configuration", "config")):
        category = OpenCodeAPIErrorClass.PROVIDER_CONFIG_ERROR.value
    elif status_code == 400:
        category = OpenCodeAPIErrorClass.BAD_REQUEST.value
    elif any(token in text for token in ("provider", "base url", "configuration", "config")):
        category = OpenCodeAPIErrorClass.PROVIDER_CONFIG_ERROR.value
    else:
        category = OpenCodeAPIErrorClass.UNKNOWN_API_ERROR.value
    summary_source = next((message for message in fields["message"] if message), "APIError")
    summary = re.sub(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]+\b", "[REDACTED]", redact_secret(summary_source))
    summary = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", summary)
    summary = re.sub(r"\s+", " ", summary).strip()[:240]
    return category, status_code, summary or "APIError"


def _agent_terminal_reason(result: _OpenCodeResult, config: OpenCodeConfig) -> str | None:
    """Apply terminal checks in evidence order, before model-alias validation."""
    if result.timed_out:
        return "agent_timeout"
    if result.return_code != 0 or result.api_error_class:
        return "agent_api_error"
    if config.expected_actual_models:
        observed = set(result.actual_model_ids)
        if not observed or not observed.issubset(set(config.expected_actual_models)):
            return "unexpected_actual_model"
    return None


def _record_opencode_tool_output(
    recorder: Any,
    parser: FeedbackParser,
    router: DisclosureRouter | None,
    result: MaterializationResult,
    disclosed: list[str],
    output: _ToolOutput,
) -> None:
    from .cli import _process_observation

    _process_observation(
        recorder,
        parser,
        router,
        _materializer_for(result),
        result,
        disclosed,
        FeedbackSource.TOOL_RESULT,
        redact_secret(cap_public(output.text) or ""),
        output.exit_code,
        "opencode_tool_result",
    )


def _record_disclosure_upload(
    recorder: Any,
    *,
    attempt: int,
    modules: list[str],
    session_id: str | None,
) -> None:
    if not modules:
        return
    recorder.emit(
        recorder_event("DISCLOSURE"),
        selected_modules=tuple(modules),
        reason="upload_confirmed_before_next_turn",
        source="vista_adapter",
        progress_after_event=None,
        metadata={
            "attempt_index": attempt,
            "disclosure_boundary": "attempt_boundary",
            "upload_status": "confirmed",
            "visible_before_next_attempt": True,
            "next_turn_uses_same_session": session_id is not None,
            "session_id": session_id,
        },
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _result_duration_ms(result: Any) -> float:
    value = getattr(result, "duration_ms", None)
    if value is not None:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0
    elapsed_sec = getattr(result, "elapsed_sec", None)
    try:
        return max(0.0, float(elapsed_sec or 0.0) * 1000)
    except (TypeError, ValueError):
        return 0.0


def _module_ids_from_event(event: dict[str, Any]) -> list[str]:
    """Extract only auditable agent tool paths from an OpenCode event."""

    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    candidates: list[str] = []
    for value in (event, part, state, state.get("input"), state.get("output")):
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, (dict, list, tuple)):
            try:
                candidates.append(json.dumps(value, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
    found: list[str] = []
    for candidate in candidates:
        found.extend(match.group(1) for match in _MODULE_ACCESS_RE.finditer(candidate))
    return list(dict.fromkeys(found))


def _command_from_event(event: dict[str, Any]) -> str | None:
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    value = state.get("input")
    if isinstance(value, dict):
        command = value.get("command")
        return str(command) if command else None
    return None


__all__ = ["run_opencode_trial"]
