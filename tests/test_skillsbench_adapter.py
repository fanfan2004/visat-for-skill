from __future__ import annotations

from pathlib import Path

from vista.adapters.preflight import run_preflight
from vista.runtime import Condition, SkillMaterializer, TaskSpec


def _fake_project(tmp_path: Path) -> tuple[Path, TaskSpec]:
    project = tmp_path
    task_root = project / "vendor" / "skillsbench" / "tasks" / "demo-task"
    source_root = task_root / "environment" / "skills"
    skill = source_root / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("native instructions", encoding="utf-8")
    (skill / "reference.md").write_text("native reference", encoding="utf-8")
    for relative in (
        "task.md",
        "environment/Dockerfile",
        "oracle/solve.sh",
        "verifier/test.sh",
    ):
        path = task_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")

    pd_root = project / "skills" / "pd-vista" / "task-001"
    pd_root.mkdir(parents=True)
    (pd_root / "SKILL.md").write_text("root", encoding="utf-8")
    (pd_root / "manifest.yaml").write_text(
        """schema_version: 1
source_content_equivalence: byte-for-byte-under-modules
modules:
  - id: demo-skill
    path: modules/demo-skill/SKILL.md
    triggers: [test_failure]
""",
        encoding="utf-8",
    )
    (pd_root / "source.yaml").write_text(
        "equivalence: byte-for-byte-under-modules\n",
        encoding="utf-8",
    )
    task = TaskSpec(
        task_id="task-001",
        native_skill_path="vendor/skillsbench/tasks/demo-task/environment/skills",
        pd_vista_skill_path="skills/pd-vista/task-001",
        source_task_path="vendor/skillsbench/tasks/demo-task",
        source_skill_path="vendor/skillsbench/tasks/demo-task/environment/skills",
    )
    return project, task


def test_materializes_native_and_pd_vista_per_trial(tmp_path: Path) -> None:
    project, task = _fake_project(tmp_path)
    materializer = SkillMaterializer(project)

    native = materializer.materialize(task, Condition.NATIVE, trial_id="trial-001")
    pd_vista = materializer.materialize(task, Condition.PD_VISTA, trial_id="trial-001")

    assert native.destination == project / "data/materialized/native/task-001/trial-001"
    assert pd_vista.destination == project / "data/materialized/pd-vista/task-001/trial-001"
    assert (native.destination / "demo-skill/reference.md").read_text() == "native reference"
    assert (pd_vista.destination / "SKILL.md").read_text() == "root"
    assert not (pd_vista.destination / "modules").exists()
    materializer.expose_module(pd_vista, "demo-skill")
    assert (
        pd_vista.destination / "modules/demo-skill/reference.md"
    ).read_text() == "native reference"
    assert (pd_vista.destination / "materialization.json").is_file()


def test_real_pilot_preflight_is_read_only() -> None:
    project_root = Path(__file__).resolve().parents[1]
    report = run_preflight(project_root)

    assert report.ok, report.errors
    assert report.expected_trial_count == 54
    assert len(report.tasks) == 6
