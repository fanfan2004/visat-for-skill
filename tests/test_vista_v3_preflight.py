from experiments.formal import vista_v3_preflight

ROOT = vista_v3_preflight.REPO_ROOT


def test_vista_v3_schedule_is_six_pd_only_trials() -> None:
    result = vista_v3_preflight.validate_schedule()

    assert result["run_id"] == "formal-vista-v3-20260812"
    assert result["trial_count"] == 6
    assert result["conditions"] == ["pd-vista"]
    assert result["task_ids"] == [f"task-{index:03d}" for index in range(1, 7)]
    schedule = vista_v3_preflight._load_yaml(vista_v3_preflight.SCHEDULE_PATH)[
        "schedule"
    ]
    for entry in schedule:
        assert entry["trial_id"] == (
            f"{entry['subrun_id']}-{entry['task_id']}-pd-vista-001"
        )


def test_vista_v3_agent_roots_hide_manifest_and_module_ids() -> None:
    result = vista_v3_preflight.validate_agent_visibility()

    assert result["task_count"] == 6
    assert result["initial_visible_files"] == ["SKILL.md"]
    assert result["routing_manifest"] == "host_only"


def test_vista_v3_real_feedback_routes_exactly_one_module_per_task() -> None:
    result = vista_v3_preflight.validate_feedback_routing()

    assert result["routable_task_count"] == 6
    assert all(len(case["selected_modules"]) == 1 for case in result["cases"])


def test_vista_v3_uses_existing_image_registry_without_build_commands() -> None:
    result = vista_v3_preflight.validate_existing_images()
    source = vista_v3_preflight.__file__
    text = open(source, encoding="utf-8").read()

    assert result["qualified_task_count"] == 6
    assert result["image_build_requested"] is False
    assert "docker build" not in text
    assert "docker compose up" not in text


def test_vista_v3_runner_is_pd_only_and_requires_explicit_confirmation() -> None:
    runner = ROOT / "experiments" / "formal" / "run_vista_v3.ps1"
    text = runner.read_text(encoding="utf-8")

    assert '"-Conditions", "pd-vista"' in text
    assert '"-Trials", "1"' in text
    assert '"-RunMode", "vista-formal"' in text
    assert "ConfirmSixVistaTrials" in text
    assert "Image build/pull: forbidden" in text
    assert "noskill" not in text
    assert "native" not in text
    assert "docker build" not in text
    assert "docker pull" not in text


def test_vista_v3_preflight_fingerprints_protocol_and_runtime() -> None:
    result = vista_v3_preflight.validate_provenance_inputs()

    assert result["algorithm"] == "sha256"
    assert result["file_count"] >= 15
    assert "docs/formal_experiment_protocol.md" in result["files"]
    assert "experiments/formal/run_vista_v3.ps1" in result["files"]
    assert "src/vista/runtime/router.py" in result["files"]
