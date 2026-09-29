from __future__ import annotations

import json

import pytest

from vista.feedback import (
    FeedbackObservation,
    FeedbackParser,
    FeedbackSource,
    FeedbackType,
)


def test_classifies_agent_visible_schema_and_assertion_feedback() -> None:
    parser = FeedbackParser()
    result = parser.parse(
        FeedbackObservation(
            source=FeedbackSource.TEST_OUTPUT,
            text="AssertionError: JSON schema validation error: 'name' is a required property",
            exit_code=1,
        ),
        trial_id="trial-1",
    )

    assert result.primary_type is FeedbackType.SCHEMA_ERROR
    assert result.feedback_types == (
        FeedbackType.SCHEMA_ERROR,
        FeedbackType.ASSERTION_ERROR,
    )
    assert result.source is FeedbackSource.TEST_OUTPUT


def test_repeated_failure_is_scoped_to_trial() -> None:
    parser = FeedbackParser()
    observation = FeedbackObservation(
        source=FeedbackSource.SHELL_STDERR,
        text="2 tests failed in 1.2s",
        exit_code=1,
    )

    first = parser.parse(observation, trial_id="trial-a")
    second = parser.parse(observation, trial_id="trial-a")
    other_trial = parser.parse(observation, trial_id="trial-b")

    assert FeedbackType.REPEATED_FAILURE not in first.feedback_types
    assert FeedbackType.REPEATED_FAILURE in second.feedback_types
    assert FeedbackType.REPEATED_FAILURE not in other_trial.feedback_types


def test_rejects_non_observable_feedback_source() -> None:
    with pytest.raises(ValueError, match="agent-observable"):
        FeedbackObservation(  # type: ignore[arg-type]
            source="hidden_verifier",
            text="expected answer",
        )


def test_structured_verifier_feedback_uses_declared_public_categories() -> None:
    parser = FeedbackParser()
    text = json.dumps(
        {
            "schema_version": 1,
            "kind": "vista_public_verifier_feedback",
            "outcome": "fail",
            "feedback_types": ["schema_error", "test_failure"],
            "failing_checks": ["test_answer_structure_and_values"],
            "summary": "Verifier reported 1 failing check.",
        }
    )

    result = parser.parse(
        FeedbackObservation(
            source=FeedbackSource.TEST_OUTPUT,
            text=text,
            exit_code=1,
        ),
        trial_id="structured",
    )

    assert result.primary_type is FeedbackType.SCHEMA_ERROR
    assert result.feedback_types == (
        FeedbackType.SCHEMA_ERROR,
        FeedbackType.TEST_FAILURE,
    )


def test_structured_travel_domain_feedback_types_are_accepted() -> None:
    parser = FeedbackParser()
    values = (
        "geographic_constraint",
        "accommodation_constraint",
        "cuisine_constraint",
        "attraction_constraint",
        "transportation_constraint",
        "flight_required",
    )
    for value in values:
        text = json.dumps(
            {
                "schema_version": 1,
                "kind": "vista_public_verifier_feedback",
                "outcome": "fail",
                "feedback_types": [value],
                "summary": "A public task constraint failed.",
            }
        )
        result = parser.parse(
            FeedbackObservation(
                source=FeedbackSource.TEST_OUTPUT,
                text=text,
                exit_code=1,
            ),
            trial_id=f"travel-{value}",
        )
        assert result.feedback_types == (FeedbackType(value),)
