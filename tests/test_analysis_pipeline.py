from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import aggregate_results  # noqa: E402
import calculate_metrics  # noqa: E402
from _common import read_jsonl  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_manifest_trigger_match_uses_real_task_module_ids() -> None:
    trigger_map = aggregate_results.routing_trigger_map("task-001")

    assert "python-scala-collections" in trigger_map["test_failure"]
    assert aggregate_results.disclosure_matches(
        {
            "feedback_type": "test_failure",
            "selected_modules": ["python-scala-collections"],
            "metadata": {},
        },
        trigger_map,
    ) is True
    assert aggregate_results.disclosure_matches(
        {
            "feedback_type": "test_failure",
            "selected_modules": ["repair"],
            "metadata": {},
        },
        trigger_map,
    ) is False


def test_usage_total_sums_attempts_and_ignores_final_max_shortcut() -> None:
    events = [
        {"event_type": "agent_action", "input_tokens": 10, "output_tokens": 4},
        {"event_type": "agent_action", "input_tokens": 20, "output_tokens": 8},
        {"event_type": "trial_end", "input_tokens": 30, "output_tokens": 12},
    ]

    assert aggregate_results.usage_total(events, "input_tokens") == 30
    assert aggregate_results.usage_total(events, "output_tokens") == 12


def test_final_artifact_snapshot_is_read_from_trial_end_metadata(tmp_path: Path) -> None:
    normalized = aggregate_results.normalize_event(
        {
            "event_id": "final",
            "trial_id": "formal-task-001-noskill-001",
            "event_type": "trial_end",
            "metadata": {
                "evidence_channel": "public_output",
                "artifact_digest": "sha256-public-result",
                "artifact_inventory": [
                    {"path": "answer.txt", "bytes": 6, "sha256": "digest"}
                ],
            },
        },
        tmp_path / "source.jsonl",
        1,
    )

    assert normalized["artifact_digest"] == "sha256-public-result"
    assert normalized["evidence_channel"] == "public_output"
    assert normalized["artifact_inventory"][0]["path"] == "answer.txt"


def test_resume_selection_keeps_one_completed_record_and_quarantines_infra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = tmp_path / "raw"
    input_dir.mkdir()
    incomplete = input_dir / "source.jsonl"
    completed = input_dir / "resume.jsonl"
    common = {
        "trial_id": "formal-task-001-pd-vista-001",
        "task_id": "task-001",
        "condition": "pd-vista",
        "trial_index": 1,
        "seed": 1,
    }
    _write_jsonl(
        incomplete,
        [
            {**common, "event_id": "old-start", "step": 0, "event_type": "trial_start"},
            {**common, "event_id": "old-attempt", "step": 1, "event_type": "agent_attempt"},
        ],
    )
    _write_jsonl(
        completed,
        [
            {**common, "event_id": "new-start", "step": 0, "event_type": "trial_start"},
            {
                **common,
                "event_id": "new-end",
                "step": 1,
                "event_type": "trial_end",
                "strict_pass": False,
                "metadata": {"trial_status": "fail"},
            },
        ],
    )
    output_root = tmp_path / "derived"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aggregate_results.py",
            "--input-dir",
            str(input_dir),
            "--output-root",
            str(output_root),
            "--analysis-id",
            "resume-test",
        ],
    )

    assert aggregate_results.main() == 0
    trials = read_jsonl(output_root / "resume-test" / "trials.jsonl")
    assert len(trials) == 1
    assert trials[0]["status"] == "fail"
    assert trials[0]["included_in_analysis"] is True
    assert trials[0]["candidate_count_for_key"] == 2
    assert trials[0]["duplicate_candidate_count"] == 1
    assert len(trials[0]["related_source_files"]) == 2

    infra = input_dir / "infra.jsonl"
    _write_jsonl(
        infra,
        [
            {
                **common,
                "trial_id": "formal-task-001-pd-vista-002",
                "trial_index": 2,
                "event_id": "infra-end",
                "step": 0,
                "event_type": "trial_end",
                "failure_category": "infrastructure_error",
                "metadata": {
                    "trial_status": "quarantined",
                    "infrastructure_class": "setup_timeout",
                },
            }
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aggregate_results.py",
            "--input-dir",
            str(input_dir),
            "--output-root",
            str(output_root),
            "--analysis-id",
            "infra-test",
        ],
    )
    assert aggregate_results.main() == 0
    infra_trials = read_jsonl(output_root / "infra-test" / "trials.jsonl")
    quarantined = next(row for row in infra_trials if row["replicate_id"] == 2)
    assert quarantined["inclusion_status"] == "quarantined"
    assert quarantined["included_in_analysis"] is False
    assert quarantined["task_failure"] is False


def test_vista_funnel_requires_confirmed_next_attempt_access_and_clean_sequence() -> None:
    events = [
        {
            "event_type": "agent_attempt",
            "step": 1,
            "metadata": {"attempt_index": 1},
        },
        {
            "event_type": "verifier_result",
            "step": 2,
            "strict_pass": False,
            "metadata": {"verifier_phase": "soft_verify", "attempt_index": 1},
        },
        {
            "event_type": "feedback",
            "step": 3,
            "feedback_type": "schema_error",
        },
        {
            "event_type": "routing",
            "step": 4,
            "selected_modules": ["d3-visualization"],
        },
        {
            "event_type": "disclosure",
            "step": 5,
            "selected_modules": ["d3-visualization"],
            "metadata": {"upload_status": "confirmed", "attempt_index": 1},
        },
        {
            "event_type": "module_access",
            "step": 6,
            "module_id": "d3-visualization",
            "metadata": {"attempt_index": 2, "disclosed_before_access": True},
        },
    ]

    funnel = aggregate_results.vista_funnel(events)

    assert funnel == {
        "vista_trigger_opportunity": True,
        "vista_routable_feedback": True,
        "vista_successful_disclosure": True,
        "vista_next_attempt_access": True,
        "vista_clean_complete_loop": True,
    }


def test_navigation_rows_separate_dynamic_selection_from_delayed_disclosure() -> None:
    trials = [
        {
            "condition": "PD-VISTA",
            "included_in_analysis": True,
            "vista_study_role": "dynamic_selection",
            "vista_trigger_opportunity": True,
            "vista_routable_feedback": True,
            "vista_successful_disclosure": True,
            "vista_next_attempt_access": True,
            "vista_clean_complete_loop": True,
        },
        {
            "condition": "PD-VISTA",
            "included_in_analysis": True,
            "vista_study_role": "delayed_disclosure",
            "vista_trigger_opportunity": True,
            "vista_routable_feedback": True,
            "vista_successful_disclosure": False,
            "vista_next_attempt_access": False,
            "vista_clean_complete_loop": False,
        },
    ]

    row = calculate_metrics.navigation_rows(trials)[0]

    assert row["dynamic_selection_trials"] == 1
    assert row["delayed_disclosure_trials"] == 1
    assert row["clean_complete_loop_rate"] == 0.5
