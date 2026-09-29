from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.runtime.router import DisclosureRouter, SkillManifest
from vista.runtime.verifier import (
    PublicVerifierFeedback,
    VerifierClass,
    VerifierPhase,
    build_public_verifier_feedback,
    classify_command_result,
    classify_verifier_result,
    collect_runtime_public_feedback,
    run_runtime_verifier,
)


def test_actual_task_002_token_check_routes_through_production_chain() -> None:
    ctrf = json.dumps(
        {
            "results": {
                "tests": [{"name": "test_tokens_efficient", "status": "failed"}]
            }
        }
    )
    feedback = build_public_verifier_feedback(ctrf_text=ctrf)
    classification = FeedbackParser().parse(
        FeedbackObservation(
            source=FeedbackSource.TEST_OUTPUT,
            text=feedback.to_text(),
            exit_code=1,
        ),
        trial_id="task-002-real-repro",
    )
    manifest = SkillManifest.load(
        Path(__file__).resolve().parents[1]
        / "skills"
        / "pd-vista"
        / "task-002"
        / "manifest.yaml",
        require_files=False,
    )
    decision = DisclosureRouter(manifest).route(classification)

    assert decision.selected_modules == ("enterprise-artifact-search",)


def test_ctrf_feedback_is_structured_and_does_not_expose_assertion_values() -> None:
    ctrf = """{
      "results": {
        "tests": [
          {
            "name": "test_answer_structure_and_values",
            "status": "failed",
            "message": "Expected [secret answer], got [agent answer]",
            "trace": "hidden assertion details"
          }
        ]
      }
    }"""

    feedback = build_public_verifier_feedback(ctrf_text=ctrf)

    assert isinstance(feedback, PublicVerifierFeedback)
    assert feedback.feedback_types == ("schema_error", "test_failure")
    assert feedback.failing_checks == ("test_answer_structure_and_values",)
    rendered = feedback.to_text()
    assert "secret answer" not in rendered
    assert "hidden assertion details" not in rendered


def test_non_ctrf_verifier_log_produces_first_failure_category() -> None:
    feedback = build_public_verifier_feedback(
        output_log="[error] Compilation failed: not found: type TokenType"
    )

    assert feedback.feedback_types == ("compilation_error", "test_failure")
    assert feedback.failing_checks == ("verifier_output_log",)


def test_runtime_feedback_collection_reads_sanitized_ctrf() -> None:
    class FakeEnv:
        async def exec(self, command: str, **_kwargs: object) -> object:
            assert "/logs/verifier/ctrf.json" in command
            return SimpleNamespace(
                stdout=(
                    '{"results":{"tests":[{"name":"suite.py::test_build_success[secret]",'
                    '"status":"failed","message":"expected hidden value"}]}}'
                ),
                return_code=0,
                timed_out=False,
            )

    runtime = SimpleNamespace(env=FakeEnv())
    observation = classify_verifier_result(
        phase=VerifierPhase.SOFT,
        reward={"reward": 0.0},
        evidence_channel="structured",
        return_code=1,
    )

    feedback = asyncio.run(collect_runtime_public_feedback(runtime, observation))

    assert feedback is not None
    assert feedback.failing_checks == ("test_build_success[case]",)
    assert "secret" not in feedback.to_text()


def test_verifier_classes_keep_infrastructure_out_of_feedback() -> None:
    setup_timeout = classify_command_result(
        SimpleNamespace(
            stdout="soft verifier timed out after 600.0s",
            stderr="",
            returncode=124,
            timed_out=True,
            duration_ms=600_000,
        ),
        phase=VerifierPhase.READINESS,
    )
    network = classify_command_result(
        SimpleNamespace(
            stdout="",
            stderr="connection refused while starting verifier",
            returncode=1,
        ),
        phase=VerifierPhase.SOFT,
    )
    task_failure = classify_command_result(
        SimpleNamespace(
            stdout="AssertionError: expected output missing",
            stderr="",
            returncode=1,
        ),
        phase=VerifierPhase.SOFT,
    )

    assert setup_timeout.classification is VerifierClass.SETUP_TIMEOUT
    assert setup_timeout.to_metadata()["evidence_channel"] == "timeout"
    assert setup_timeout.quarantine is True
    assert setup_timeout.feedback_allowed is False
    assert network.classification is VerifierClass.NETWORK_ERROR
    assert network.to_metadata()["evidence_channel"] == "error"
    assert network.feedback_allowed is False
    assert task_failure.classification is VerifierClass.TASK_FAILURE
    assert task_failure.to_metadata()["evidence_channel"] == "public_output"
    assert task_failure.feedback_allowed is True


def test_readiness_dependency_missing_is_infrastructure() -> None:
    observation = classify_command_result(
        SimpleNamespace(
            stdout="ModuleNotFoundError: No module named 'pandas'",
            stderr="",
            returncode=1,
            timed_out=False,
        ),
        phase=VerifierPhase.READINESS,
    )

    assert observation.classification is VerifierClass.INFRASTRUCTURE_ERROR
    assert observation.quarantine is True
    assert observation.feedback_allowed is False


def test_soft_public_module_not_found_is_task_failure() -> None:
    observation = classify_command_result(
        SimpleNamespace(
            stdout="ModuleNotFoundError: produced app imports missing_module",
            stderr="",
            returncode=1,
            timed_out=False,
        ),
        phase=VerifierPhase.SOFT,
    )

    assert observation.classification is VerifierClass.TASK_FAILURE
    assert observation.quarantine is False
    assert observation.feedback_allowed is True


def test_final_public_connection_refused_is_task_failure() -> None:
    observation = classify_command_result(
        SimpleNamespace(
            stdout="AssertionError: produced app connection refused on its local port",
            stderr="",
            returncode=1,
            timed_out=False,
        ),
        phase=VerifierPhase.FINAL,
    )

    assert observation.classification is VerifierClass.TASK_FAILURE
    assert observation.quarantine is False
    assert observation.feedback_allowed is True


def test_runtime_harness_exception_in_error_channel_is_infrastructure() -> None:
    observation = classify_verifier_result(
        phase=VerifierPhase.SOFT,
        error="Docker harness exception: connection refused while exec-ing verifier",
        return_code=1,
    )

    assert observation.classification is VerifierClass.NETWORK_ERROR
    assert observation.quarantine is True
    assert observation.feedback_allowed is False


def test_runtime_timeout_is_infrastructure_even_without_timeout_text() -> None:
    observation = classify_verifier_result(
        phase=VerifierPhase.SOFT,
        public_output="public verifier output was truncated",
        return_code=124,
        timed_out=True,
        timeout_source="runtime.wait_for",
    )

    assert observation.classification is VerifierClass.VERIFIER_TIMEOUT
    assert observation.quarantine is True
    assert observation.timeout_source == "runtime.wait_for"
    assert observation.feedback_allowed is False


def test_runtime_verifier_timeout_records_phase_and_timeout_source() -> None:
    async def operation() -> object:
        await asyncio.sleep(0.05)
        return {"reward": 1.0}

    observation = asyncio.run(
        run_runtime_verifier(
            operation(),
            phase=VerifierPhase.SOFT,
            timeout_sec=0,
        )
    )

    assert observation.classification is VerifierClass.VERIFIER_TIMEOUT
    assert observation.timed_out is True
    assert observation.timeout_source == "runtime.wait_for"
    assert observation.feedback_allowed is False
