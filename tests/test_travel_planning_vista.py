from pathlib import Path

from experiments.formal import run_travel_planning_vista_group as experiment
from vista.feedback import FeedbackType
from vista.runtime import SkillManifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "pd-vista" / "task-007-travel-planning" / "manifest.yaml"


def _manifest() -> SkillManifest:
    return SkillManifest.load(MANIFEST, require_files=False)


def test_every_real_travel_feedback_category_has_a_trigger() -> None:
    manifest = _manifest()
    triggers = {trigger for module in manifest.modules for trigger in module.triggers}
    real_categories = {
        FeedbackType.INCOMPLETE_OUTPUT.value,
        FeedbackType.SCHEMA_ERROR.value,
        FeedbackType.WRONG_FORMAT.value,
        FeedbackType.RUNTIME_ERROR.value,
        FeedbackType.TEST_FAILURE.value,
        FeedbackType.GEOGRAPHIC_CONSTRAINT.value,
        FeedbackType.ACCOMMODATION_CONSTRAINT.value,
        FeedbackType.CUISINE_CONSTRAINT.value,
        FeedbackType.ATTRACTION_CONSTRAINT.value,
        FeedbackType.TRANSPORTATION_CONSTRAINT.value,
    }
    assert real_categories <= triggers


def test_domain_feedback_maps_to_exactly_one_expected_module() -> None:
    manifest = _manifest()
    expected = {
        "geographic_constraint": "search-cities",
        "accommodation_constraint": "search-accommodations",
        "cuisine_constraint": "search-restaurants",
        "attraction_constraint": "search-attractions",
        "transportation_constraint": "search-driving-distance",
        "flight_required": "search-flights",
    }
    for feedback, module_id in expected.items():
        matching = [module.module_id for module in manifest.modules if feedback in module.triggers]
        assert matching == [module_id]


def test_no_flight_transport_feedback_does_not_disclose_flight_module() -> None:
    manifest = _manifest()
    matching = [
        module.module_id
        for module in manifest.modules
        if FeedbackType.TRANSPORTATION_CONSTRAINT.value in module.triggers
    ]
    assert matching == ["search-driving-distance"]


def test_three_condition_schedule_has_nine_unique_balanced_cells() -> None:
    assert experiment.CONDITIONS == ("noskill", "native", "pd-vista")
    assert len(experiment.SCHEDULE) == 9
    assert len(set(experiment.SCHEDULE)) == 9
    for condition in experiment.CONDITIONS:
        assert sum(value == condition for _, value in experiment.SCHEDULE) == 3
    for replicate in experiment.REPLICATES:
        observed = {
            condition
            for item_replicate, condition in experiment.SCHEDULE
            if item_replicate == replicate
        }
        assert observed == set(experiment.CONDITIONS)


def test_current_opencode_config_is_resolved_without_persisting_a_key(
    tmp_path: Path, monkeypatch,
) -> None:
    config = tmp_path / "opencode.json"
    config.write_text(
        """{
  "share": "disabled",
  "permission": {"external_directory": "deny"},
  "model": "minimax/MiniMax-M3",
  "provider": {
    "minimax": {
      "options": {"apiKey": "{env:MINIMAX_API_KEY}"},
      "models": {"MiniMax-M3": {"name": "MiniMax M3"}}
    }
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VISTA_OPENCODE_CONFIG", str(config))
    path, model, key_name = experiment.current_opencode_config()
    assert path == config
    assert model == "minimax/MiniMax-M3"
    assert key_name == "MINIMAX_API_KEY"


def test_current_opencode_config_accepts_authenticated_builtin_provider(
    tmp_path: Path, monkeypatch,
) -> None:
    config = tmp_path / "opencode.json"
    config.write_text(
        """{
  "share": "disabled",
  "permission": {"external_directory": "deny"},
  "model": "minimax-cn/MiniMax-M3"
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VISTA_OPENCODE_CONFIG", str(config))
    path, model, key_name = experiment.current_opencode_config()
    assert path == config
    assert model == "minimax-cn/MiniMax-M3"
    assert key_name is None


def test_prepare_trial_materializes_exact_condition_boundaries(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(experiment, "LOCAL_ROOT", tmp_path / "runs")
    monkeypatch.setattr(experiment, "RUN_PREFIX", "test-three-condition")

    workspaces = {}
    router_sources = {}
    for condition in experiment.CONDITIONS:
        _, workspace, _, router_source = experiment.prepare_trial(condition, 1)
        workspaces[condition] = workspace
        router_sources[condition] = router_source

    noskill_entries = {path.name for path in (workspaces["noskill"] / "skills").iterdir()}
    assert noskill_entries == set()
    assert not (workspaces["noskill"] / "VISTA.md").exists()

    native_entries = {path.name for path in (workspaces["native"] / "skills").iterdir()}
    assert native_entries == set(experiment.MODULE_IDS) | {"data"}
    assert not (workspaces["native"] / "VISTA.md").exists()
    for module_id in experiment.MODULE_IDS:
        source = experiment.TASK_SOURCE / "environment" / "skills" / module_id / "SKILL.md"
        copied = workspaces["native"] / "skills" / module_id / "SKILL.md"
        assert copied.read_bytes() == source.read_bytes()

    vista_entries = {path.name for path in (workspaces["pd-vista"] / "skills").iterdir()}
    assert vista_entries == {"data"}
    assert (workspaces["pd-vista"] / "VISTA.md").is_file()
    assert (router_sources["pd-vista"] / "manifest.yaml").is_file()
    assert set(
        path.name for path in (router_sources["pd-vista"] / "modules").iterdir()
    ) == set(experiment.MODULE_IDS)
