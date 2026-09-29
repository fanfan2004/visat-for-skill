from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from experiments.formal import qualification


def test_qualification_command_uses_utf8_replacement_for_windows_output(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class Completed:
        returncode = 0
        stdout = "Docker ✓"
        stderr = ""

    def fake_run(*args: Any, **kwargs: Any) -> Completed:
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(qualification.subprocess, "run", fake_run)

    result = qualification.run_command(["fake-command"])

    assert result["return_code"] == 0
    assert result["stdout"] == "Docker ✓"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_qualification_child_environment_uses_project_owned_temp(monkeypatch: Any) -> None:
    monkeypatch.setenv("TEMP", r"C:\unavailable-user-temp")
    monkeypatch.setenv("TMP", r"C:\unavailable-user-temp")

    environment = qualification.child_environment()
    expected = (
        (qualification.REPO_ROOT / "data" / "tmp" / "qualification").resolve()
        if os.name == "nt"
        else Path("/tmp/vista-qualification").resolve()
    )

    assert Path(environment["TEMP"]).resolve() == expected
    assert Path(environment["TMP"]).resolve() == expected
    assert Path(environment["TMPDIR"]).resolve() == expected


def test_frozen_runtime_evidence_accepts_matching_qualified_manifest() -> None:
    binary = "b" * 64
    image = "sha256:" + "a" * 64
    manifest = {
        "runtime_identity": {
            "opencode": {"actual_version": "1.18.13", "binary_sha256": binary}
        },
        "docker": {
            "formal_task_images": {
                "status": "qualified",
                "image_digests": {"task-001": image},
            }
        },
    }
    build = {
        "opencode": {"version": "1.18.13", "actual_binary_sha256": binary},
        "tasks": [{"task_id": "task-001", "image_digest": image}],
    }
    readiness = {
        "tasks": [
            {
                "opencode": {
                    "actual_version": "1.18.13",
                    "binary_sha256": binary,
                }
            }
        ]
    }

    assert qualification.frozen_runtime_evidence_mismatches(
        manifest, build, readiness
    ) == []


def test_paid_qualification_gate_requires_explicit_approval() -> None:
    prerequisite_names = [
        "frozen_schedule",
        "task_condition_pd_manifest",
        "frozen_provenance_digest_check",
        "P0_disclosure_attempt_boundary",
        "P0_root_manifest_isolation",
        "P0_infrastructure_noise_isolation",
        "P0_5_verifier_evidence_channels",
        "P0_real_feedback_routing_coverage",
        "full_pytest_and_ruff",
        "wsl_docker_and_old_pilot_state",
        "vendor_clean_and_commits",
        "formal_run_guard_and_no_54_start",
        "build_time_opencode_task_images",
        "formal_runtime_identity_and_readiness",
    ]
    gates = {name: {"status": "GO"} for name in prerequisite_names}

    blocked = qualification.minimal_paid_qualification_approval_gate(gates, None)
    approved = qualification.minimal_paid_qualification_approval_gate(
        gates, "user-approved-formal-run"
    )

    assert blocked["status"] == "NO-GO"
    assert approved["status"] == "GO"
    assert approved["approval_id"] == "user-approved-formal-run"


def test_real_task_feedback_fixtures_are_routable_on_first_failure() -> None:
    result = qualification.validate_real_feedback_routing()

    assert result["task_count"] == 6
    assert result["routable_task_count"] == 6
    assert result["qualified_check_count"] >= 35
    assert result["unroutable_checks"] == []
    assert result["placeholder_feedback_count"] == 0
    assert all(case["selected_modules"] for case in result["cases"])
    assert all(len(case["selected_modules"]) == 1 for case in result["cases"])
