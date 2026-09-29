from experiments.formal import vista_v4_preflight

ROOT = vista_v4_preflight.REPO_ROOT


def test_v4_schedule_uses_new_six_trial_namespace() -> None:
    result = vista_v4_preflight.validate_schedule()

    assert result["run_id"] == "formal-vista-v4-20260813"
    assert result["protocol_version"] == 4
    assert result["trial_count"] == 6
    assert result["conditions"] == ["pd-vista"]


def test_v4_qualifies_every_production_verifier_check() -> None:
    result = vista_v4_preflight.validate_real_feedback_routing()

    assert result["qualified_check_count"] >= 35
    assert result["unroutable_checks"] == []
    assert all(len(case["selected_modules"]) == 1 for case in result["cases"])


def test_v4_runner_is_locked_and_does_not_build_or_pull() -> None:
    runner = (ROOT / "experiments" / "formal" / "run_vista_v4.ps1").read_text(
        encoding="utf-8"
    )

    assert '"-RunMode", "vista-formal"' in runner
    assert '"-Conditions", "pd-vista"' in runner
    assert "ConfirmSixVistaTrials" in runner
    assert "formal-vista-v4-20260813" in runner
    assert "noskill" not in runner
    assert "native" not in runner
    assert "docker build" not in runner
    assert "docker pull" not in runner


def test_v4_provenance_includes_manifests_and_fail_fast_code() -> None:
    result = vista_v4_preflight.validate_provenance()

    assert result["file_count"] >= 24
    assert "skills/pd-vista/task-002/manifest.yaml" in result["files"]
    assert "src/vista/runtime/opencode_backend.py" in result["files"]


def test_v4_composite_registry_contains_six_qualified_images() -> None:
    result = vista_v4_preflight.validate_existing_images()

    assert result["qualified_task_count"] == 6
    assert result["image_build_requested"] is False
