from pathlib import Path

from experiments.formal import run_task001_local_group as experiment


def test_task001_schedule_is_balanced_three_by_three() -> None:
    assert len(experiment.SCHEDULE) == 9
    assert len(set(experiment.SCHEDULE)) == 9
    for condition in experiment.CONDITIONS:
        assert sum(value == condition for _, value in experiment.SCHEDULE) == 3
    for replicate in experiment.REPLICATES:
        assert {
            condition
            for value, condition in experiment.SCHEDULE
            if value == replicate
        } == set(experiment.CONDITIONS)


def test_task001_condition_materialization_boundaries(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(experiment, "LOCAL_ROOT", tmp_path / "runs")
    monkeypatch.setattr(experiment, "RUN_PREFIX", "task001-test")
    values = {
        condition: experiment.prepare_trial(condition, 1)
        for condition in experiment.CONDITIONS
    }

    noskill = values["noskill"][1]
    assert list((noskill / "modules").iterdir()) == []
    assert not (noskill / "VISTA.md").exists()

    native = values["native"][1]
    assert {path.name for path in (native / "modules").iterdir()} == set(
        experiment.MODULE_IDS
    )
    for module in experiment.MODULE_IDS:
        assert (native / "modules" / module / "SKILL.md").read_bytes() == (
            experiment.NATIVE_SOURCE / module / "SKILL.md"
        ).read_bytes()

    vista = values["pd-vista"][1]
    router = values["pd-vista"][3]
    assert list((vista / "modules").iterdir()) == []
    assert (vista / "VISTA.md").is_file()
    assert {path.name for path in (router / "modules").iterdir()} == set(
        experiment.MODULE_IDS
    )
    assert (vista / "Tokenizer.py").is_file()
    assert (vista / "compile.sh").is_file()


def test_public_diagnostic_keeps_errors_and_removes_host_paths() -> None:
    output = """
[error] /Users/alice/private/trial/Test.scala:12: not found: type TokenContainer
irrelevant verbose line
Tests passed: 0/0
Overall Score: 20/25
Status: NEEDS IMPROVEMENT
"""
    diagnostic = experiment.public_diagnostic(output)
    assert "TokenContainer" in diagnostic
    assert "Tests passed: 0/0" in diagnostic
    assert "alice" not in diagnostic
    assert "irrelevant" not in diagnostic
