"""Project-owned BenchFlow backend for the pilot runtime.

The backend keeps one task sandbox and one DeepSeek chat session per trial.  It
uses BenchFlow's public ``TaskRuntime`` primitive for lifecycle and verifier
execution, while VISTA owns the model/tool loop and progressive disclosure.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from vista.audit import (
    cap_public,
    has_progress_signal,
    has_repair_action,
    redact_secret,
    sha256_text,
)
from vista.feedback import FeedbackParser, FeedbackSource

from .deepseek_agent import (
    DeepSeekAgentError,
    DeepSeekBashAgent,
    DeepSeekConfig,
    ToolResult,
)
from .materialization import Condition, MaterializationResult, TaskSpec
from .router import DisclosureRouter
from .usage import UsageAccumulator
from .verifier import (
    QUARANTINE_EXIT_CODE,
    VerifierEvidenceChannel,
    VerifierObservation,
    VerifierPhase,
    classify_verifier_result,
    collect_runtime_public_feedback,
    run_runtime_verifier,
)


async def run_benchflow_trial(
    args: SimpleNamespace,
    *,
    project_root: Path,
    task: TaskSpec,
    condition: Condition,
    result: MaterializationResult,
    recorder: Any,
    router: DisclosureRouter | None,
) -> int:
    """Run one real trial and return the strict verifier process code."""

    source_task = _resolve_source_task(project_root, task)
    benchflow_task = _stage_task_source(source_task, project_root, task.task_id)
    config = DeepSeekConfig.from_environment()
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
    attempt_feedback_state: dict[str, object] = {"fingerprints": []}
    tool_commands: list[str] = []

    # BenchFlow is intentionally imported lazily.  The normal metadata-only
    # runtime and its unit tests do not require the bundled BenchFlow Python.
    try:
        from benchflow.rollout import TaskRuntime, TaskRuntimeConfig
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "deepseek-benchflow backend requires the bundled BenchFlow Python "
            "(.tools/benchflow/tools/benchflow/bin/python)"
        ) from exc

    async def anchor_hook(_env: Any) -> None:
        _anchor_host_cwd(project_root)

    try:
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
                # The project-owned agent mounts skill material explicitly.
                # BenchFlow therefore must not auto-inject native or bundled
                # SkillsBench content into the container.
                skill_mode="no-skill",
            )
        )
        await _ensure_agent_user(runtime, project_root)
        await _prepare_skill_mount(runtime, result, condition, project_root)
        readiness = await _soft_verify(
            runtime,
            args.verifier_timeout_sec,
            project_root,
            phase=VerifierPhase.READINESS,
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
                runtime, runtime.workspace
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
                    "agent_attempts": 0,
                    "disclosed_modules": [],
                    "artifact_digest": artifact_digest,
                    "artifact_inventory": artifact_inventory,
                },
            )
            return QUARANTINE_EXIT_CODE
        agent = DeepSeekBashAgent(
            config,
            system_prompt=_system_prompt(
                condition, completion_hint=_completion_hint(benchflow_task)
            ),
            user_prompt=_user_prompt(benchflow_task, condition),
            bash=lambda command, timeout_sec: _run_bash(
                runtime, command, timeout_sec, project_root
            ),
            on_tool_result=lambda tool_result: _handle_tool_result(
                tool_result,
                recorder=recorder,
                parser=parser,
                router=router,
                result=result,
                disclosed=disclosed,
                runtime=runtime,
                project_root=project_root,
                observation_state=attempt_feedback_state,
                tool_commands=tool_commands,
            ),
        )

        no_progress = 0
        seen_feedback_fingerprints: set[str] = set()
        for attempt in range(1, args.max_attempts + 1):
            attempts_completed = attempt
            attempt_feedback_state = {"fingerprints": []}
            tool_commands.clear()
            attempt_artifact_before = await _container_artifact_digest(
                runtime, runtime.workspace
            )
            recorder.emit(
                recorder_event("AGENT_ATTEMPT"),
                agent_used=True,
                source="deepseek_agent",
                metadata={
                    "attempt_index": attempt,
                    "visible_modules": list(disclosed),
                    "same_chat_session": True,
                    "model": config.model,
                },
            )
            try:
                agent_result = await asyncio.wait_for(
                    agent.run(), timeout=args.agent_timeout_sec
                )
            except TimeoutError:
                agent_result = None
                quarantine_timed_out = True
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=True,
                    source="deepseek_agent",
                    failure_category="infrastructure_error",
                    metadata={"attempt_index": attempt, "error": "agent_timeout"},
                )
            except DeepSeekAgentError as exc:
                agent_result = None
                message = redact_secret(str(exc))
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=False,
                    source="deepseek_agent",
                    failure_category="infrastructure_error",
                    metadata={"attempt_index": attempt, "error": message},
                )
            else:
                input_tokens = int(agent_result.usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(agent_result.usage.get("completion_tokens", 0) or 0)
                total_tokens = agent_result.usage.get("total_tokens")
                total_tokens = int(total_tokens) if total_tokens is not None else None
                usage_totals.add(
                    requests=agent_result.requests,
                    tool_calls=agent_result.tool_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                split_tokens = input_tokens + output_tokens
                if total_tokens is not None and total_tokens > split_tokens:
                    usage_totals.add(total_tokens=total_tokens - split_tokens)
                recorder.emit(
                    recorder_event("AGENT_ACTION"),
                    agent_used=True,
                    timed_out=agent_result.timed_out,
                    source="deepseek_agent",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    metadata={
                        "attempt_index": attempt,
                        "requests": agent_result.requests,
                        "tool_calls": agent_result.tool_calls,
                        "stop_reason": agent_result.stop_reason,
                        "cumulative_usage": usage_totals.snapshot(),
                    },
                )

            if agent_result is None:
                quarantined = True
                stop_reason = "agent_infrastructure_error"
                break
            attempt_artifact_after_agent = await _container_artifact_digest(
                runtime, runtime.workspace
            )
            attempt_fingerprints = attempt_feedback_state.get("fingerprints", [])
            if not isinstance(attempt_fingerprints, list):
                attempt_fingerprints = []
            repair_action_observed = has_repair_action(tool_commands)
            feedback_changed = any(
                fingerprint not in seen_feedback_fingerprints
                for fingerprint in attempt_fingerprints
            )
            progress = has_progress_signal(
                artifact_before=attempt_artifact_before,
                artifact_after=attempt_artifact_after_agent,
                previous_feedback_fingerprints=seen_feedback_fingerprints,
                observed_feedback_fingerprints=attempt_fingerprints,
                repair_action_observed=repair_action_observed,
            )
            seen_feedback_fingerprints.update(attempt_fingerprints)
            if attempt > 1:
                from .cli import _record_repair_observed

                _record_repair_observed(
                    recorder,
                    attempt=attempt,
                    disclosed=disclosed,
                    artifact_changed=(
                        attempt_artifact_before != attempt_artifact_after_agent
                    ),
                    feedback_changed=feedback_changed,
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
            feedback = cap_public(
                verifier_observation.public_output
                or verifier_observation.error_summary
                or (structured_feedback.to_text() if structured_feedback else "")
            )
            if not feedback:
                quarantined = True
                quarantine_reason = "missing_public_verifier_feedback"
                stop_reason = quarantine_reason
                break
            if feedback:
                before = len(disclosed)
                attempt_feedback_state = {"fingerprints": []}
                _record_public_feedback(
                    recorder,
                    parser,
                    feedback,
                    FeedbackSource.TEST_OUTPUT,
                    1,
                    "verifier_public_feedback",
                    router=router,
                    result=result,
                    disclosed=disclosed,
                    observation_state=attempt_feedback_state,
                )
                from .cli import _is_unroutable_pd_feedback

                if _is_unroutable_pd_feedback(condition, attempt_feedback_state):
                    quarantined = True
                    quarantine_reason = "unroutable_public_verifier_feedback"
                    stop_reason = quarantine_reason
                    break
                await _upload_new_disclosures(
                    runtime, result, disclosed, before, project_root
                )
                module_notice = ""
                if disclosed:
                    paths = ", ".join(
                        f"/vista-skill/modules/{module}/SKILL.md" for module in disclosed
                    )
                    module_notice = (
                        " New VISTA modules are available; read the relevant module before "
                        f"editing: {paths}."
                    )
                agent.add_user_feedback(
                    "Public verifier feedback is below. Do not infer hidden answers; "
                    "continue the same task session and repair the artifact."
                    + module_notice
                    + "\n\n"
                    + feedback
                    + "\n\nCompletion gate: do not stop with a prose response. Use bash "
                    "to create every output file explicitly requested by the task, "
                    "then check that those files exist and are valid."
                )
            attempt_fingerprints = attempt_feedback_state.get("fingerprints", [])
            if not isinstance(attempt_fingerprints, list):
                attempt_fingerprints = []
            feedback_changed = any(
                fingerprint not in seen_feedback_fingerprints
                for fingerprint in attempt_fingerprints
            )
            seen_feedback_fingerprints.update(attempt_fingerprints)
            attempt_artifact_after_feedback = await _container_artifact_digest(
                runtime, runtime.workspace
            )
            progress = progress or has_progress_signal(
                artifact_before=attempt_artifact_before,
                artifact_after=attempt_artifact_after_feedback,
                previous_feedback_fingerprints=seen_feedback_fingerprints,
                observed_feedback_fingerprints=attempt_fingerprints,
                repair_action_observed=repair_action_observed,
            ) or feedback_changed
            no_progress = no_progress + 1 if not progress else 0
            if attempt == args.max_attempts or no_progress >= args.no_progress_limit:
                stop_reason = "no_progress_guard"
                break
            _record_repair(recorder, attempt + 1, disclosed)

        if quarantined:
            artifact_digest, artifact_inventory = await _container_artifact_snapshot(
                runtime, runtime.workspace
            )
            recorder.emit(
                recorder_event("TRIAL_END"),
                strict_pass=None,
                timed_out=quarantine_timed_out,
                source="skillsbench_verifier",
                failure_category="infrastructure_error",
                input_tokens=usage_totals.input_tokens or None,
                output_tokens=usage_totals.output_tokens or None,
                total_tokens=usage_totals.total_tokens or None,
                metadata={
                    "trial_status": "quarantined",
                    "quarantine_reason": quarantine_reason or stop_reason,
                    "backend": "deepseek-benchflow",
                    "agent_attempts": attempts_completed,
                    "disclosed_modules": list(disclosed),
                    "same_chat_session": True,
                    "model": config.model,
                    "requests": usage_totals.requests,
                    "tool_calls": usage_totals.tool_calls,
                    "usage": usage_totals.snapshot(),
                    "artifact_digest": artifact_digest,
                    "artifact_inventory": artifact_inventory,
                },
            )
            return QUARANTINE_EXIT_CODE

        # Always perform exactly one final strict verifier after the soft loop.
        # This is the only score used for the trial outcome.
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
                metadata={"reason": stop_reason, "disclosed_modules": list(disclosed)},
            )
        artifact_digest, artifact_inventory = await _container_artifact_snapshot(
            runtime, runtime.workspace
        )
        recorder.emit(
            recorder_event("TRIAL_END"),
            strict_pass=None if quarantined else final_pass,
            timed_out=bool(final_observation.timed_out),
            source="skillsbench_verifier",
            input_tokens=usage_totals.input_tokens or None,
            output_tokens=usage_totals.output_tokens or None,
            total_tokens=usage_totals.total_tokens or None,
            failure_category="infrastructure_error" if quarantined else None,
            metadata={
                "trial_status": (
                    "quarantined" if quarantined else ("pass" if final_pass else "fail")
                ),
                "backend": "deepseek-benchflow",
                "agent_attempts": attempts_completed,
                "disclosed_modules": list(disclosed),
                "same_chat_session": True,
                "model": config.model,
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


async def _prepare_skill_mount(
    runtime: Any,
    result: MaterializationResult,
    condition: Condition,
    project_root: Path,
) -> None:
    _anchor_host_cwd(project_root)
    if condition is Condition.NOSKILL:
        await _assert_container_skill_layout(runtime, expected_files=None)
        return
    if condition is Condition.NATIVE:
        await runtime.env.upload_dir(result.visible_root or result.destination, "/vista-skill")
        await _lock_container_skill_mount(runtime)
        await _assert_container_skill_layout(
            runtime,
            expected_files={path.as_posix() for path in result.files},
        )
        return
    visible_root = result.visible_root or result.destination / "_agent_visible"
    if not visible_root.is_dir():
        visible_root.mkdir(parents=True)
    if result.visible_root is None:
        for name in ("SKILL.md",):
            source = result.destination / name
            if not source.is_file():
                raise RuntimeError(f"PD-VISTA wrapper is missing visible file: {source}")
            shutil.copy2(source, visible_root / name)
    required = {"SKILL.md"}
    visible_files = {
        path.relative_to(visible_root).as_posix()
        for path in visible_root.rglob("*")
        if path.is_file()
    }
    if visible_files != required:
        raise RuntimeError(
            "PD-VISTA initial mount must contain exactly SKILL.md; "
            f"found {sorted(visible_files)}"
        )
    await runtime.env.upload_dir(visible_root, "/vista-skill")
    await _lock_container_skill_mount(runtime)
    await _assert_container_skill_layout(runtime, expected_files=required)


async def _lock_container_skill_mount(runtime: Any) -> None:
    env = getattr(runtime, "env", None)
    if env is None or not hasattr(env, "exec"):
        return
    result = await env.exec(
        "chmod -R a-w /vista-skill",
        user="root",
        timeout_sec=30,
    )
    return_code = getattr(result, "return_code", getattr(result, "exit_code", 1))
    if return_code != 0:
        raise RuntimeError("failed to make the skill mount read-only")


async def _assert_container_skill_layout(
    runtime: Any,
    *,
    expected_files: set[str] | None,
) -> None:
    """Assert the agent-visible container file whitelist when exec is available."""

    env = getattr(runtime, "env", None)
    if env is None or not hasattr(env, "exec"):
        return
    if expected_files is None:
        result = await env.exec(
            "test ! -e /vista-skill",
            user="root",
            timeout_sec=30,
        )
        return_code = getattr(result, "return_code", getattr(result, "exit_code", 1))
        if return_code != 0:
            raise RuntimeError("No-Skill container unexpectedly contains /vista-skill")
        return
    result = await env.exec(
        "find /vista-skill -type f -printf '%P\\n' | sort",
        user="root",
        timeout_sec=30,
    )
    return_code = getattr(result, "return_code", getattr(result, "exit_code", 1))
    if return_code != 0:
        raise RuntimeError("failed to inspect the agent-visible skill mount")
    actual = {
        line.strip()
        for line in str(getattr(result, "stdout", "") or "").splitlines()
        if line.strip()
    }
    if actual != expected_files:
        raise RuntimeError(
            "agent-visible skill whitelist mismatch: "
            f"expected={sorted(expected_files)} actual={sorted(actual)}"
        )


async def _ensure_agent_user(runtime: Any, project_root: Path) -> None:
    """Make the task image compatible with BenchFlow's standard agent user.

    Some SkillsBench images omit the optional ``agent`` account.  BenchFlow's
    normal installed-agent path creates it, but this project-owned direct
    policy loop intentionally skips agent installation.  Recreate the same
    workspace ownership boundary as a root-only setup step.
    """

    _anchor_host_cwd(project_root)
    result = await runtime.env.exec(
        "id -u agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent; "
        "chown -R agent:agent /root",
        user="root",
        timeout_sec=120,
    )
    return_code = getattr(result, "return_code", getattr(result, "exit_code", 0))
    if return_code != 0:
        raise RuntimeError("failed to create the BenchFlow sandbox agent user")


async def _upload_new_disclosures(
    runtime: Any,
    result: MaterializationResult,
    disclosed: list[str],
    before: int,
    project_root: Path,
) -> list[str]:
    _anchor_host_cwd(project_root)
    uploaded: list[str] = []
    for module_id in disclosed[before:]:
        module_path = result.destination / "modules" / module_id
        await runtime.env.upload_dir(module_path, f"/vista-skill/modules/{module_id}")
        uploaded.append(module_id)
    if len(disclosed) > before:
        await _lock_container_skill_mount(runtime)
        visible_root = result.visible_root or result.destination
        expected = {
            path.relative_to(visible_root).as_posix()
            for path in visible_root.rglob("*")
            if path.is_file()
        }
        await _assert_container_skill_layout(runtime, expected_files=expected)
    return uploaded


async def _handle_tool_result(
    tool_result: ToolResult,
    *,
    recorder: Any,
    parser: FeedbackParser,
    router: DisclosureRouter | None,
    result: MaterializationResult,
    disclosed: list[str],
    runtime: Any,
    project_root: Path,
    observation_state: dict[str, object] | None = None,
    tool_commands: list[str] | None = None,
) -> None:
    from .cli import _process_observation

    before = len(disclosed)
    _process_observation(
        recorder,
        parser,
        router,
        # Materialization is only used here for host-side controlled copies.
        _materializer_for(result),
        result,
        disclosed,
        FeedbackSource.TOOL_RESULT,
        redact_secret(tool_result.text),
        tool_result.return_code,
        "agent_tool_result",
        observation_state=observation_state,
    )
    if tool_commands is not None:
        tool_commands.append(tool_result.command)
    await _upload_new_disclosures(runtime, result, disclosed, before, project_root)


def _materializer_for(result: MaterializationResult) -> Any:
    # The caller replaces this seam in the CLI path with the real materializer.
    # This function is kept as a narrow fallback so the backend remains easy to
    # unit-test without importing BenchFlow.
    from .materialization import SkillMaterializer

    project_root = result.destination
    for parent in result.destination.parents:
        if (parent / "skills").is_dir() and (parent / "data" / "materialized").is_dir():
            project_root = parent
            break
    return SkillMaterializer(project_root)


async def _run_bash(
    runtime: Any, command: str, timeout_sec: int, project_root: Path
) -> tuple[int, str, str]:
    _anchor_host_cwd(project_root)
    result = await runtime.bash(command, timeout_sec=timeout_sec)
    return result.return_code, result.stdout, result.stderr


async def _container_artifact_inventory(
    runtime: Any, workspace: str
) -> list[str] | None:
    """List public task files and their hashes, excluding hidden inputs."""

    env = getattr(runtime, "env", None)
    if env is None or not hasattr(env, "exec"):
        return None
    command = (
        "find . -type f "
        "! -path './verifier/*' ! -path './oracle/*' ! -path './solution/*' "
        "! -path './logs/verifier/*' ! -path './vista-skill/*' "
        "-exec sha256sum '{}' \\; | sort"
    )
    try:
        result = await env.exec(command, cwd=workspace, user="root", timeout_sec=30)
    except Exception:
        return None
    return_code = getattr(result, "return_code", getattr(result, "exit_code", 1))
    if return_code != 0:
        return None
    stdout = str(getattr(result, "stdout", "") or "")
    return [line.strip() for line in stdout.splitlines() if line.strip()]


async def _container_artifact_digest(runtime: Any, workspace: str) -> str | None:
    """Hash the public artifact inventory used for progress comparisons."""

    inventory = await _container_artifact_inventory(runtime, workspace)
    return sha256_text("\n".join(inventory)) if inventory is not None else None


async def _container_artifact_snapshot(
    runtime: Any, workspace: str
) -> tuple[str | None, list[str]]:
    inventory = await _container_artifact_inventory(runtime, workspace)
    if inventory is None:
        return None, []
    return sha256_text("\n".join(inventory)), inventory


async def _soft_verify(
    runtime: Any,
    timeout_sec: int,
    project_root: Path,
    *,
    phase: VerifierPhase = VerifierPhase.SOFT,
) -> VerifierObservation:
    _anchor_host_cwd(project_root)
    return await run_runtime_verifier(
        runtime.rollout.soft_verify(),
        phase=phase,
        timeout_sec=timeout_sec,
    )


async def _close_runtime_preserving_prebuilt_image(runtime: Any) -> None:
    """Remove the trial container while retaining its qualified base image."""

    try:
        environment = runtime.env
    except Exception:
        await runtime.close()
        return
    if not hasattr(environment, "_keep_containers") or not hasattr(environment, "stop"):
        await runtime.close()
        return

    # BenchFlow's normal delete path uses ``docker compose down --rmi all``.
    # Stop with delete=False first: compose removes the trial container and
    # network but keeps the already-qualified image. Mark the already-stopped
    # sandbox as externally owned so Rollout.cleanup() does not call
    # stop(delete=True) a second time and remove the prebuilt image.
    await environment.stop(delete=False)
    rollout = getattr(runtime, "rollout", None)
    if rollout is not None:
        setattr(rollout, "_env_externally_owned", True)
    await runtime.close()


def _record_public_feedback(
    recorder: Any,
    parser: FeedbackParser,
    text: str,
    source: FeedbackSource,
    exit_code: int,
    phase: str,
    *,
    router: DisclosureRouter | None = None,
    result: MaterializationResult | None = None,
    disclosed: list[str] | None = None,
    observation_state: dict[str, object] | None = None,
) -> int:
    from .cli import _process_observation

    if router is None:
        # _process_observation supports the no-skill/native conditions too.
        router_result = None
    else:
        router_result = router
    if result is None or disclosed is None:
        raise RuntimeError("public feedback routing requires materialization state")
    return _process_observation(
        recorder,
        parser,
        router_result,
        _materializer_for(result),
        result,
        disclosed,
        source,
        redact_secret(text),
        exit_code,
        phase,
        observation_state=observation_state,
    )


def _record_repair(recorder: Any, next_attempt: int, disclosed: list[str]) -> None:
    from .cli import _record_repair as record

    record(recorder, next_attempt, disclosed)


def recorder_event(name: str) -> Any:
    from vista.telemetry import EventType

    return getattr(EventType, name)


def _resolve_source_task(project_root: Path, task: TaskSpec) -> Path:
    if not task.source_task_path:
        raise RuntimeError(f"task {task.task_id} has no source_task_path")
    source = (project_root / task.source_task_path).resolve()
    allowed = (project_root / "vendor" / "skillsbench" / "tasks").resolve()
    try:
        source.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError(f"source task escapes vendor task root: {source}") from exc
    if not source.is_dir():
        raise RuntimeError(f"source task does not exist: {source}")
    return source


def _stage_task_source(source: Path, project_root: Path, task_id: str) -> Path:
    """Stage only public task inputs and normalize verifier text line endings.

    The repository is checked out on Windows, so ``verifier/test.sh`` can carry
    CRLF bytes.  A Linux container then reports ``/verifier/test.sh: not found``
    because the shebang ends in ``\\r``.  The stage is project-owned and excludes
    any optional answer/solution files from the vendor task tree; the vendor
    checkout itself is never modified.
    """

    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
    staged = project_root / "data" / "raw" / "_benchflow_tasks" / safe_task_id
    staged.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "task.md", staged / "task.md")
    for name in ("environment", "verifier"):
        source_dir = source / name
        if not source_dir.is_dir():
            raise RuntimeError(f"source task is missing public {name} directory: {source_dir}")
        shutil.copytree(source_dir, staged / name, dirs_exist_ok=True)

    _patch_staged_dockerfile(staged / "environment" / "Dockerfile")
    verifier_dir = staged / "verifier"
    for path in verifier_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".md",
            ".py",
            ".sh",
            ".txt",
            ".yaml",
            ".yml",
        }:
            continue
        data = path.read_bytes()
        normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if normalized != data:
            path.write_bytes(normalized)
    return staged


def _patch_staged_dockerfile(path: Path) -> None:
    """Make Ubuntu apt-based task images work behind the local Windows proxy.

    Docker containers cannot reach the user's loopback-only proxy.  The public
    mirror is reachable directly from Docker, so rewrite apt source files in
    the project-owned staging copy before any apt-get layer runs.
    """

    if not path.is_file():
        return
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    text = data.decode("utf-8")
    if "scala:2.13.12" in text and "coursier" in text.lower():
        text = text.replace(
            next(
                line
                for line in text.splitlines()
                if line.lstrip().startswith("RUN curl -fL https://github.com/coursier/")
            ),
            "RUN curl -fL "
            "https://github.com/scala/scala/releases/download/v2.13.12/"
            "scala-2.13.12.tgz -o /tmp/scala.tgz && "
            "tar -xzf /tmp/scala.tgz -C /opt && "
            "ln -s /opt/scala-2.13.12/bin/scala /usr/local/bin/scala && "
            "ln -s /opt/scala-2.13.12/bin/scalac /usr/local/bin/scalac",
        )
    if "apt-get" not in text or "mirrors.aliyun.com" in text:
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    marker = (
        "RUN sed -i "
        "'s#archive.ubuntu.com#mirrors.aliyun.com#g; "
        "s#security.ubuntu.com#mirrors.aliyun.com#g; "
        "s#deb.debian.org#mirrors.aliyun.com#g' "
        "/etc/apt/sources.list.d/ubuntu.sources "
        "2>/dev/null || true\n"
    )
    lines = text.splitlines(keepends=True)
    insert_at = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.lstrip().upper().startswith("FROM ")
        ),
        0,
    )
    lines.insert(insert_at, marker)
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


def _system_prompt(condition: Condition, *, completion_hint: str = "") -> str:
    skill = {
        Condition.NOSKILL: "No Skill is mounted for this trial.",
        Condition.NATIVE: (
            "A native Skill is mounted read-only under /vista-skill. Read the relevant "
            "SKILL.md before solving."
        ),
        Condition.PD_VISTA: (
            "A PD-VISTA root entrypoint is mounted at /vista-skill/SKILL.md. Read it "
            "first. Additional modules may appear under /vista-skill/modules only after "
            "public feedback triggers disclosure."
        ),
    }[condition]
    return (
        "You are the agent under evaluation in a SkillsBench task. Complete the task "
        "using the bash tool and write the requested artifact in the sandbox. "
        "You may inspect task inputs and run public checks. Never access /verifier, "
        "/oracle, /solution, /logs/verifier, reward files, or hidden expected answers. "
        "This interface exposes only bash; execute any skill procedure directly and "
        "do not rely on an unavailable subagent or Task tool. Keep command output "
        "focused instead of dumping whole datasets. Do not stop merely because you "
        "have described a solution: make the required filesystem changes, verify "
        "that every requested output exists, and only then finish.\n\n"
        + skill
        + ("\n\n" + completion_hint if completion_hint else "")
    )


def _user_prompt(source_task: Path, condition: Condition) -> str:
    task_path = source_task / "task.md"
    if not task_path.is_file():
        raise RuntimeError(f"task instruction is missing: {task_path}")
    instruction = task_path.read_text(encoding="utf-8")
    return (
        "Task instructions (public benchmark input):\n\n"
        + instruction[:50000]
        + "\n\nThe task sandbox is already prepared. Begin by inspecting the public "
        "inputs. Before your final response, use bash to create and validate every "
        "requested output artifact."
    )


def _completion_hint(source_task: Path) -> str:
    instruction = (source_task / "task.md").read_text(encoding="utf-8")
    paths: list[str] = []
    patterns = (
        r"(?is)\b(?:write|save|output|return|generate|create|copy|apply).*?\x60([^\x60]+)\x60",
        r"(?is)\b(?:write|save|output|return|generate|create|copy|apply).*?['\"]([^'\"]*?/[^'\"]+)['\"]",
    )
    for pattern in patterns:
        for value in re.findall(pattern, instruction):
            path = value.strip().rstrip(".,;")
            if path.startswith(("/root/", "/home/github/build/failed/")) and path not in paths:
                paths.append(path)
    if not paths:
        return ""
    return "Explicit output paths mentioned by the task: " + ", ".join(paths)


def _reward_pass(rewards: Any) -> bool:
    if not isinstance(rewards, dict):
        return False
    value = rewards.get("reward")
    try:
        return float(value) >= 1.0
    except (TypeError, ValueError):
        return False


def _anchor_host_cwd(project_root: Path) -> None:
    os.chdir(project_root)


__all__ = ["run_benchflow_trial"]
