from __future__ import annotations

import json
from pathlib import Path

import pytest

from vista.telemetry import EventLogError, EventRecord, EventType, JsonlEventLog


def test_appends_independent_replayable_jsonl_events(tmp_path: Path) -> None:
    log = JsonlEventLog.for_trial(tmp_path, "task-001-pd-vista-002")
    feedback = EventRecord(
        trial_id="task-001-pd-vista-002",
        step=11,
        event_type=EventType.FEEDBACK,
        feedback_type="schema_error",
        source="test_output",
    )
    disclosure = EventRecord(
        trial_id="task-001-pd-vista-002",
        step=12,
        event_type=EventType.DISCLOSURE,
        feedback_type="schema_error",
        selected_modules=("output-contract", "repair"),
        reason="schema_error matched output-contract",
        estimated_tokens=1180,
        previous_modules=("validation",),
        progress_after_event=True,
    )

    log.append(feedback)
    log.append(disclosure)

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["selected_modules"] == ["output-contract", "repair"]
    assert list(log.read_events()) == [feedback, disclosure]


def test_rejects_non_increasing_steps_and_cross_trial_writes(tmp_path: Path) -> None:
    log = JsonlEventLog.for_trial(tmp_path, "trial-1")
    log.append(EventRecord(trial_id="trial-1", step=2, event_type=EventType.FEEDBACK))

    with pytest.raises(EventLogError, match="step must increase"):
        log.append(EventRecord(trial_id="trial-1", step=2, event_type=EventType.ROUTING))
    with pytest.raises(EventLogError, match="does not match"):
        log.append(EventRecord(trial_id="trial-2", step=3, event_type=EventType.ROUTING))


def test_rejects_non_json_metadata() -> None:
    with pytest.raises(TypeError, match="JSON-serializable"):
        EventRecord(
            trial_id="trial-1",
            step=0,
            event_type=EventType.FEEDBACK,
            metadata={"bad": object()},
        )


def test_redacts_secret_metadata_before_jsonl_persistence(tmp_path: Path) -> None:
    api_key = "sk-" + "x" * 32
    log = JsonlEventLog.for_trial(tmp_path, "trial-secret")
    event = EventRecord(
        trial_id="trial-secret",
        step=0,
        event_type=EventType.AGENT_ACTION,
        metadata={
            "api_key": api_key,
            "nested": {
                "authorization": f"Bearer {'y' * 32}",
                "command": f"echo {api_key}",
            },
        },
    )

    log.append(event)
    serialized = log.path.read_text(encoding="utf-8")

    assert api_key not in serialized
    assert "<redacted-secret>" in serialized
    assert event.metadata["api_key"] == "<redacted-secret>"
