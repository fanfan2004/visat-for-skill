"""Bounded agent/feedback/disclosure/verifier adapter for one pilot trial."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from vista.adapters.skillsbench import SourceAuditError
from vista.audit import (
    has_progress_signal,
    public_artifact_digest,
    public_artifact_inventory,
    redact_secret,
)
from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.telemetry import EventRecord, EventType, JsonlEventLog

from .materialization import Condition, MaterializationResult, SkillMaterializer, TaskManifest
from .router import DisclosureRouter, SkillManifest
from .task_metadata import SkillsBenchTaskMetadata
from .verifier import (
    QUARANTINE_EXIT_CODE,
    VerifierPhase,
    classify_command_result,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False
    duration_ms: float = 0.0


@dataclass(slots=True)
class TrialRecorder:
    log: JsonlEventLog
    trial_id: str
    task_id: str
    condition: str
    trial_index: int
    # Legacy callers may still provide a numeric seed, but formal runs pass a
    # randomization_id and leave the uncontrolled model seed out of telemetry.
    seed: int | None
    replicate_id: int | None = None
    pair_id: str | None = None
    randomization_id: str | None = None
    step: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    def emit(self, event_type: EventType, **fields: object) -> None:
        fields.setdefault("elapsed_ms", (time.monotonic() - self.started_monotonic) * 1000)
        metadata = dict(fields.get("metadata") or {})
        if self.replicate_id is not None:
            metadata.setdefault("replicate_id", self.replicate_id)
        if self.pair_id is not None:
            metadata.setdefault("pair_id", self.pair_id)
        if self.randomization_id is not None:
            metadata.setdefault("randomization_id", self.randomization_id)
            metadata.setdefault("seed_controlled", False)
            metadata.setdefault("seed_claim", "none")
        fields["metadata"] = metadata
        self.log.append(
            EventRecord(
                trial_id=self.trial_id,
                task_id=self.task_id,
                condition=self.condition,
                trial_index=self.trial_index,
                seed=None if self.randomization_id is not None else self.seed,
                step=self.step,
                event_type=event_type,
                **fields,
            )
        )
        self.step += 1


def run_trial(args: argparse.Namespace) -> int:
    manifest_path = Path(args.task_manifest).resolve()
    project_root = manifest_path.parent.parent
    # BenchFlow launches Docker Compose subprocesses without an explicit host
    # cwd.  WSL started from a hidden PowerShell process can inherit a stale
    # virtual cwd (for example ``/vista``); anchor the process to the real
    # project root before any backend work.
    os.chdir(project_root)
    condition_config = Path(args.condition_config).resolve()
    if not condition_config.is_file():
        raise RuntimeError(f"condition config does not exist: {condition_config}")
    materialized_root = Path(args.materialized_root).resolve()
    expected_materialized_root = (project_root / "data" / "materialized").resolve()
    if materialized_root != expected_materialized_root:
        raise RuntimeError(
            f"materialized root must be {expected_materialized_root}, found {materialized_root}"
        )
    events_path = Path(args.events_jsonl).resolve()
    raw_root = (project_root / "data" / "raw").resolve()
    _assert_inside(events_path, raw_root, "events-jsonl")
    if events_path.exists():
        raise RuntimeError(f"refusing to overwrite event log: {events_path}")
    if args.max_attempts < 1 or args.no_progress_limit < 1:
        raise ValueError("max_attempts and no_progress_limit must be positive")

    backend = getattr(args, "backend", None) or os.environ.get(
        "VISTA_RUNTIME_BACKEND", "commands"
    )
    if backend not in {"commands", "deepseek-benchflow", "opencode-benchflow"}:
        raise RuntimeError(f"unknown runtime backend: {backend}")
    agent_command = args.agent_command or os.environ.get("VISTA_AGENT_COMMAND")
    verifier_command = args.verifier_command or os.environ.get("VISTA_VERIFIER_COMMAND")
    if backend == "commands":
        if not agent_command:
            raise RuntimeError("set VISTA_AGENT_COMMAND or pass --agent-command")
        if not verifier_command:
            raise RuntimeError(
                "set VISTA_VERIFIER_COMMAND or pass --verifier-command; "
                "the adapter never infers a final score"
            )

    manifest = TaskManifest.load(manifest_path)
    task = manifest.get(args.task_id)
    condition = Condition.parse(args.condition)
    trial_id = args.trial_id or f"{task.task_id}-{condition.value}-{args.trial_index:03d}"
    # Backends receive the canonical identifier even when the caller omitted
    # the optional CLI flag.
    args.trial_id = trial_id
    raw_agent_timeout = getattr(args, "agent_timeout_sec", None)
    raw_verifier_timeout = getattr(args, "verifier_timeout_sec", None)
    task_metadata = (
        SkillsBenchTaskMetadata.load(project_root / task.source_task_path)
        if task.source_task_path
        else None
    )
    if task_metadata is not None:
        requested_agent_timeout = int(
            raw_agent_timeout
            if raw_agent_timeout is not None
            else task_metadata.agent_timeout_sec or 1800
        )
        requested_verifier_timeout = int(
            raw_verifier_timeout
            if raw_verifier_timeout is not None
            else task_metadata.verifier_timeout_sec or 900
        )
        args.agent_timeout_sec = (
            task_metadata.agent_timeout_sec
            if raw_agent_timeout is None and task_metadata.agent_timeout_sec is not None
            else task_metadata.cap_agent_timeout(requested_agent_timeout)
        )
        args.verifier_timeout_sec = (
            task_metadata.verifier_timeout_sec
            if raw_verifier_timeout is None and task_metadata.verifier_timeout_sec is not None
            else task_metadata.cap_verifier_timeout(requested_verifier_timeout)
        )
        timeout_budget_source = (
            "skillsbench_task_metadata_default"
            if raw_agent_timeout is None and raw_verifier_timeout is None
            else "explicit_budget_capped_by_skillsbench_metadata"
        )
        args.build_timeout_sec = task_metadata.build_timeout_sec or 180
    else:
        requested_agent_timeout = int(raw_agent_timeout or 1800)
        requested_verifier_timeout = int(raw_verifier_timeout or 900)
        args.agent_timeout_sec = requested_agent_timeout
        args.verifier_timeout_sec = requested_verifier_timeout
        timeout_budget_source = "adapter_default_or_explicit"
        args.build_timeout_sec = 180
    task_metadata_public = (
        task_metadata.to_public_metadata(
            requested_agent=requested_agent_timeout,
            requested_verifier=requested_verifier_timeout,
        )
        if task_metadata is not None
        else {}
    )
    task_metadata_public["timeout_budget_source"] = timeout_budget_source
    materializer = SkillMaterializer(project_root)
    try:
        result = materializer.materialize(task, condition, trial_id=trial_id)
    except SourceAuditError as exc:
        raise RuntimeError(f"source/PD-VISTA preflight failed: {exc}") from exc

    randomization_id = getattr(args, "randomization_id", None)
    recorder = TrialRecorder(
        log=JsonlEventLog(events_path, expected_trial_id=trial_id),
        trial_id=trial_id,
        task_id=task.task_id,
        condition=condition.value,
        trial_index=args.trial_index,
        seed=None if randomization_id else args.seed,
        replicate_id=getattr(args, "replicate_id", None) or args.trial_index,
        pair_id=getattr(args, "pair_id", None),
        randomization_id=randomization_id,
    )
    recorder.emit(
        EventType.TRIAL_START,
        source="vista_adapter",
        metadata={
            "materialized_path": str(result.destination),
            "protocol_version": 3,
            "max_attempts": args.max_attempts,
            "no_progress_limit": args.no_progress_limit,
            **task_metadata_public,
        },
    )

    router: DisclosureRouter | None = None
    disclosed: list[str] = []
    if condition is Condition.PD_VISTA:
        router = DisclosureRouter(
            SkillManifest.load(result.destination / "manifest.yaml", require_files=False)
        )
        recorder.emit(
            EventType.ROOT_SKILL_LOADED,
            source="vista_adapter",
            metadata={
                "entrypoint": "SKILL.md",
                "routing_manifest": "host_only",
                "visible_modules": [],
                "initial_visible_files": ["SKILL.md"],
                "hidden_source": True,
            },
        )

    if backend in {"deepseek-benchflow", "opencode-benchflow"}:
        if backend == "opencode-benchflow":
            from .opencode_backend import run_opencode_trial

            return int(
                asyncio.run(
                    run_opencode_trial(
                        args,
                        project_root=project_root,
                        task=task,
                        condition=condition,
                        result=result,
                        recorder=recorder,
                        router=router,
                    )
                )
            )
        from .benchflow_backend import run_benchflow_trial

        return int(
            asyncio.run(
                run_benchflow_trial(
                    args,
                    project_root=project_root,
                    task=task,
                    condition=condition,
                    result=result,
                    recorder=recorder,
                    router=router,
                )
            )
        )
    visible_skill_root = result.visible_root or result.destination
    environment = os.environ.copy()
    environment.update(
        {
            "VISTA_AGENT_PROTOCOL_VERSION": "3",
            "VISTA_TRIAL_ID": trial_id,
            "VISTA_TASK_ID": task.task_id,
            "VISTA_CONDITION": condition.value,
            "VISTA_WORKSPACE": str(result.destination),
            "VISTA_SKILL_DIR": str(visible_skill_root),
            "VISTA_SKILL_ENTRYPOINT": str(visible_skill_root / "SKILL.md"),
            "VISTA_SOURCE_TASK_DIR": "",
            "VISTA_TASK_INPUT_DIR": os.environ.get("VISTA_TASK_INPUT_DIR", ""),
            "VISTA_BENCHMARK_TASK_ID": Path(task.source_task_path or "").name,
            "VISTA_EVENTS_JSONL": str(events_path),
        }
    )
    parser = FeedbackParser()
    last_agent: CommandResult | None = None
    final_verifier: CommandResult | None = None
    verifier_ran = False
    no_progress = 0
    stop_reason: str | None = None
    attempts_completed = 0
    quarantined = False
    seen_feedback_fingerprints: set[str] = set()

    # Readiness is deliberately outside the agent-attempt loop.  It pays the
    # verifier cold-start/setup cost before any model call and, on an
    # infrastructure failure, prevents both feedback routing and repair.
    readiness = _run_readiness_command(
        verifier_command,
        result.destination,
        environment,
        args.verifier_timeout_sec,
    )
    readiness_observation = classify_command_result(
        readiness, phase=VerifierPhase.READINESS
    )
    recorder.emit(
        EventType.VERIFIER_RESULT,
        strict_pass=None,
        timed_out=readiness_observation.timed_out,
        source="skillsbench_verifier_readiness",
        failure_category=("infrastructure_error" if readiness_observation.quarantine else None),
        metadata={
            "attempt_index": 0,
            "readiness_gate": True,
            "timeout_budget_sec": args.verifier_timeout_sec,
            **readiness_observation.to_metadata(),
        },
    )
    if readiness_observation.quarantine:
        quarantined = True
        artifact_digest, artifact_inventory = _public_artifact_snapshot(result.destination)
        recorder.emit(
            EventType.TRIAL_END,
            strict_pass=None,
            timed_out=readiness_observation.timed_out,
            source="skillsbench_verifier_readiness",
            failure_category="infrastructure_error",
            metadata={
                "trial_status": "quarantined",
                "quarantine_reason": readiness_observation.classification.value,
                "agent_attempts": 0,
                "disclosed_modules": [],
                "verifier_readiness": readiness_observation.to_metadata(),
                "artifact_digest": artifact_digest,
                "artifact_inventory": artifact_inventory,
            },
        )
        return QUARANTINE_EXIT_CODE

    for attempt in range(1, args.max_attempts + 1):
        attempts_completed = attempt
        attempt_artifact_before = public_artifact_digest(result.destination)
        attempt_feedback_fingerprints: list[str] = []
        environment.update(
            {
                "VISTA_AGENT_ATTEMPT": str(attempt),
                "VISTA_DISCLOSED_MODULES": ",".join(disclosed),
                "VISTA_VISIBLE_MODULES_DIR": str(visible_skill_root / "modules"),
            }
        )
        recorder.emit(
            EventType.AGENT_ATTEMPT,
            agent_used=True,
            source="agent_command",
            metadata={
                "attempt_index": attempt,
                "visible_modules": list(disclosed),
            },
        )
        agent = _run_command(
            agent_command,
            result.destination,
            environment,
            args.agent_timeout_sec,
        )
        last_agent = agent
        recorder.emit(
            EventType.AGENT_ACTION,
            agent_used=True,
            timed_out=agent.timed_out,
            source="agent_command",
            failure_category=("infrastructure_error" if agent.timed_out else None),
            metadata={
                "attempt_index": attempt,
                "exit_code": agent.returncode,
                "duration_ms": agent.duration_ms,
                "timeout_budget_sec": args.agent_timeout_sec,
            },
        )
        if agent.timed_out:
            quarantined = True
            recorder.emit(
                EventType.LOOP_STOP,
                strict_pass=None,
                source="vista_adapter",
                failure_category="infrastructure_error",
                metadata={"reason": "agent_timeout", "attempt_index": attempt},
            )
            break
        _process_observation(
            recorder,
            parser,
            router,
            materializer,
            result,
            disclosed,
            FeedbackSource.SHELL_STDOUT,
            agent.stdout,
            agent.returncode,
            "agent_stdout",
            observation_state={"fingerprints": attempt_feedback_fingerprints},
        )
        _process_observation(
            recorder,
            parser,
            router,
            materializer,
            result,
            disclosed,
            FeedbackSource.SHELL_STDERR,
            agent.stderr,
            agent.returncode,
            "agent_stderr",
            observation_state={"fingerprints": attempt_feedback_fingerprints},
        )
        attempt_artifact_after_agent = public_artifact_digest(result.destination)
        attempt_progress = has_progress_signal(
            artifact_before=attempt_artifact_before,
            artifact_after=attempt_artifact_after_agent,
            previous_feedback_fingerprints=seen_feedback_fingerprints,
            observed_feedback_fingerprints=attempt_feedback_fingerprints,
        )
        feedback_changed = any(
            fingerprint not in seen_feedback_fingerprints
            for fingerprint in attempt_feedback_fingerprints
        )
        seen_feedback_fingerprints.update(attempt_feedback_fingerprints)
        if attempt > 1:
            _record_repair_observed(
                recorder,
                attempt=attempt,
                disclosed=disclosed,
                artifact_changed=attempt_artifact_before != attempt_artifact_after_agent,
                feedback_changed=feedback_changed,
            )

        if agent.returncode != 0:
            no_progress = no_progress + 1 if not attempt_progress else 0
            if attempt == args.max_attempts or no_progress >= args.no_progress_limit:
                stop_reason = "agent_failed_without_further_progress"
                break
            _record_repair(recorder, attempt + 1, disclosed)
            continue

        verifier = _run_command(
            verifier_command,
            result.destination,
            environment,
            args.verifier_timeout_sec,
        )
        final_verifier = verifier
        verifier_ran = True
        verifier_observation = classify_command_result(
            verifier, phase=VerifierPhase.SOFT
        )
        recorder.emit(
            EventType.VERIFIER_RESULT,
            strict_pass=(
                None if verifier_observation.quarantine else verifier_observation.strict_pass
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
            break
        if verifier_observation.strict_pass:
            break
        _process_observation(
            recorder,
            parser,
            router,
            materializer,
            result,
            disclosed,
            FeedbackSource.TEST_OUTPUT,
            verifier.stdout,
            verifier.returncode,
            "verifier_public_stdout",
            observation_state={"fingerprints": attempt_feedback_fingerprints},
        )
        _process_observation(
            recorder,
            parser,
            router,
            materializer,
            result,
            disclosed,
            FeedbackSource.TEST_OUTPUT,
            verifier.stderr,
            verifier.returncode,
            "verifier_public_stderr",
            observation_state={"fingerprints": attempt_feedback_fingerprints},
        )
        attempt_artifact_after_verifier = public_artifact_digest(result.destination)
        attempt_progress = has_progress_signal(
            artifact_before=attempt_artifact_before,
            artifact_after=attempt_artifact_after_verifier,
            previous_feedback_fingerprints=seen_feedback_fingerprints,
            observed_feedback_fingerprints=attempt_feedback_fingerprints,
        )
        seen_feedback_fingerprints.update(attempt_feedback_fingerprints)
        no_progress = no_progress + 1 if not attempt_progress else 0
        if attempt == args.max_attempts or no_progress >= args.no_progress_limit:
            stop_reason = "verifier_failed_without_further_progress"
            break
        _record_repair(recorder, attempt + 1, disclosed)

    if quarantined:
        artifact_digest, artifact_inventory = _public_artifact_snapshot(result.destination)
        recorder.emit(
            EventType.TRIAL_END,
            strict_pass=None,
            timed_out=bool(last_agent and last_agent.timed_out)
            or bool(final_verifier and final_verifier.timed_out),
            source="skillsbench_verifier",
            failure_category="infrastructure_error",
            metadata={
                "trial_status": "quarantined",
                "quarantine_reason": stop_reason or "verifier_infrastructure_error",
                "agent_attempts": attempts_completed,
                "disclosed_modules": list(disclosed),
                "artifact_digest": artifact_digest,
                "artifact_inventory": artifact_inventory,
            },
        )
        return QUARANTINE_EXIT_CODE

    if not verifier_ran:
        if last_agent is None:
            raise RuntimeError("loop ended without an agent attempt")
        verifier = _run_command(
            verifier_command,
            result.destination,
            environment,
            args.verifier_timeout_sec,
        )
        final_verifier = verifier
        verifier_ran = True
        verifier_observation = classify_command_result(
            verifier, phase=VerifierPhase.FINAL
        )
        recorder.emit(
            EventType.VERIFIER_RESULT,
            strict_pass=(
                None if verifier_observation.quarantine else verifier_observation.strict_pass
            ),
            timed_out=verifier_observation.timed_out,
            source="skillsbench_verifier",
            failure_category=(
                "infrastructure_error" if verifier_observation.quarantine else None
            ),
            metadata={
                "attempt_index": attempts_completed,
                "timeout_budget_sec": args.verifier_timeout_sec,
                **verifier_observation.to_metadata(),
            },
        )
        if verifier_observation.quarantine:
            quarantined = True
        else:
            _process_observation(
                recorder,
                parser,
                router,
                materializer,
                result,
                disclosed,
                FeedbackSource.TEST_OUTPUT,
                verifier.stdout,
                verifier.returncode,
                "verifier_public_stdout",
            )
            _process_observation(
                recorder,
                parser,
                router,
                materializer,
                result,
                disclosed,
                FeedbackSource.TEST_OUTPUT,
                verifier.stderr,
                verifier.returncode,
                "verifier_public_stderr",
                observation_state={"fingerprints": []},
            )

    passed = (
        final_verifier is not None
        and not quarantined
        and final_verifier.returncode == 0
    )
    if stop_reason and not quarantined:
        recorder.emit(
            EventType.LOOP_STOP,
            strict_pass=passed,
            source="vista_adapter",
            failure_category="no_progress_guard",
            metadata={"reason": stop_reason, "disclosed_modules": list(disclosed)},
        )
    artifact_digest, artifact_inventory = _public_artifact_snapshot(result.destination)
    recorder.emit(
        EventType.TRIAL_END,
        strict_pass=None if quarantined else passed,
        timed_out=bool(final_verifier and final_verifier.timed_out),
        source="skillsbench_verifier",
        failure_category="infrastructure_error" if quarantined else None,
        metadata={
            "trial_status": "quarantined" if quarantined else ("pass" if passed else "fail"),
            "agent_attempts": attempts_completed,
            "disclosed_modules": list(disclosed),
            "artifact_digest": artifact_digest,
            "artifact_inventory": artifact_inventory,
        },
    )
    return QUARANTINE_EXIT_CODE if quarantined else (0 if passed else 1)


def _process_observation(
    recorder: TrialRecorder,
    parser: FeedbackParser,
    router: DisclosureRouter | None,
    materializer: SkillMaterializer,
    result: MaterializationResult,
    disclosed: list[str],
    source: FeedbackSource,
    text: str,
    exit_code: int,
    phase: str,
    observation_state: dict[str, object] | None = None,
) -> int:
    if not text.strip():
        return 0
    safe_text = redact_secret(text) or ""
    classification = parser.parse(
        FeedbackObservation(source=source, text=safe_text, exit_code=exit_code),
        trial_id=recorder.trial_id,
    )
    if observation_state is not None:
        fingerprints = observation_state.setdefault("fingerprints", [])
        if isinstance(fingerprints, list):
            fingerprints.append(classification.fingerprint)
    recorder.emit(
        EventType.FEEDBACK,
        feedback_type=classification.primary_type.value,
        source=classification.source.value,
        failure_category=(
            "feedback_misclassification"
            if classification.primary_type.value == "unknown"
            else None
        ),
        metadata={
            "phase": phase,
            "feedback_types": [item.value for item in classification.feedback_types],
            "fingerprint": classification.fingerprint,
            "excerpt": classification.excerpt,
            "exit_code": exit_code,
            "observable": True,
        },
    )
    if router is None:
        if observation_state is not None:
            observation_state["routing_status"] = "not_applicable"
        return 0
    decision = router.route(classification, previous_modules=disclosed)
    recorder.emit(
        EventType.ROUTING,
        feedback_type=classification.primary_type.value,
        selected_modules=decision.selected_modules,
        previous_modules=tuple(decision.previous_modules),
        reason=decision.reason,
        source=classification.source.value,
        metadata={"phase": phase},
    )
    if observation_state is not None:
        observation_state["routing_reason"] = decision.reason
        observation_state["selected_modules"] = list(decision.selected_modules)
        observation_state["routing_status"] = (
            "selected"
            if decision.modules
            else (
                "previously_disclosed"
                if decision.skipped_previous_modules
                else "unmatched"
            )
        )
    if not decision.modules:
        return 0
    for module in decision.modules:
        materializer.expose_module(result, module.module_id)
    modules = router.disclose(decision)
    disclosed.extend(module.module_id for module in modules)
    recorder.emit(
        EventType.DISCLOSURE,
        feedback_type=classification.primary_type.value,
        selected_modules=tuple(module.module_id for module in modules),
        previous_modules=tuple(decision.previous_modules),
        reason=decision.reason,
        estimated_tokens=sum(module.estimated_tokens for module in modules),
        source=classification.source.value,
        progress_after_event=None,
        metadata={
            "phase": phase,
            "module_paths": [str(module.path) for module in modules],
            "disclosure_boundary": "attempt_boundary",
            "progress_evidence": None,
        },
    )
    return len(modules)


def _is_unroutable_pd_feedback(
    condition: Condition, observation_state: dict[str, object]
) -> bool:
    return (
        condition is Condition.PD_VISTA
        and observation_state.get("routing_status") == "unmatched"
    )


def _record_repair(recorder: TrialRecorder, next_attempt: int, disclosed: list[str]) -> None:
    recorder.emit(
        EventType.REPAIR,
        agent_used=False,
        repair_observed=None,
        progress_after_event=None,
        source="vista_adapter",
        metadata={
            "next_attempt": next_attempt,
            "disclosed_modules": list(disclosed),
            "repair_requested": True,
            "repair_evidence": None,
        },
    )


def _record_repair_observed(
    recorder: TrialRecorder,
    *,
    attempt: int,
    disclosed: list[str],
    artifact_changed: bool,
    feedback_changed: bool,
    repair_action_observed: bool = False,
) -> None:
    evidence = {
        "artifact_digest_changed": artifact_changed,
        "public_feedback_fingerprint_changed": feedback_changed,
        "repair_action_observed": repair_action_observed,
    }
    observed = any(evidence.values())
    recorder.emit(
        EventType.REPAIR,
        agent_used=True,
        repair_observed=observed,
        progress_after_event=observed,
        source="vista_adapter",
        metadata={
            "observed_on_attempt": attempt,
            "disclosed_modules": list(disclosed),
            "repair_requested": False,
            "repair_evidence": evidence,
        },
    )


def _run_command(
    command: str,
    cwd: Path,
    environment: dict[str, str],
    timeout_sec: int,
) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=environment,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout)
        stderr = _decode_output(exc.stderr)
        return CommandResult(
            stdout,
            stderr,
            124,
            timed_out=True,
            duration_ms=(time.monotonic() - started) * 1000,
        )
    return CommandResult(
        completed.stdout,
        completed.stderr,
        completed.returncode,
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _run_readiness_command(
    command: str,
    workspace: Path,
    environment: dict[str, str],
    timeout_sec: int,
) -> CommandResult:
    """Prewarm a command verifier without mutating the trial workspace.

    A readiness probe is infrastructure preparation, not a task attempt.  A
    verifier is allowed to install dependencies or create cache/state files
    while it warms up, but those side effects must not make the first real
    verifier call appear to have progressed the task.  The disposable copy is
    deliberately created by the Python adapter rather than by a shell delete
    or cleanup command.
    """

    with tempfile.TemporaryDirectory(prefix="vista-verifier-readiness-") as temp_dir:
        readiness_workspace = Path(temp_dir) / "workspace"
        shutil.copytree(workspace, readiness_workspace)
        readiness_environment = environment.copy()
        readiness_environment.update(
            {
                "VISTA_WORKSPACE": str(readiness_workspace),
                "VISTA_SKILL_DIR": str(readiness_workspace),
                "VISTA_SKILL_ENTRYPOINT": str(readiness_workspace / "SKILL.md"),
                "VISTA_VISIBLE_MODULES_DIR": str(readiness_workspace / "modules"),
                "VISTA_VERIFIER_PHASE": VerifierPhase.READINESS.value,
            }
        )
        return _run_command(
            command,
            readiness_workspace,
            readiness_environment,
            timeout_sec,
        )


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _public_artifact_snapshot(
    root: Path,
) -> tuple[str | None, list[dict[str, str | int]]]:
    """Capture the final public result tree for event-level auditability."""

    try:
        return public_artifact_digest(root), public_artifact_inventory(root)
    except OSError:
        return None, []


def _assert_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes {root}: {path}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--task-id", required=True)
    run.add_argument("--condition", required=True)
    run.add_argument("--trial-index", required=True, type=int)
    run.add_argument("--replicate-id", type=int)
    run.add_argument("--pair-id")
    run.add_argument("--randomization-id")
    run.add_argument("--seed", required=True, type=int)
    run.add_argument("--trial-id")
    run.add_argument("--task-manifest", required=True)
    run.add_argument("--condition-config", required=True)
    run.add_argument("--materialized-root", required=True)
    run.add_argument("--events-jsonl", required=True)
    run.add_argument("--image-manifest-path")
    run.add_argument(
        "--backend",
        choices=("commands", "deepseek-benchflow", "opencode-benchflow"),
        default=None,
        help="project-owned command loop, DeepSeek+BenchFlow, or OpenCode+BenchFlow",
    )
    run.add_argument("--agent-command")
    run.add_argument("--verifier-command")
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--no-progress-limit", type=int, default=2)
    run.add_argument("--agent-timeout-sec", type=int)
    run.add_argument("--verifier-timeout-sec", type=int)
    run.set_defaults(handler=run_trial)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
