from __future__ import annotations

from pathlib import Path

import pytest

from vista.runtime import (
    Condition,
    MaterializationError,
    MaterializationExistsError,
    SkillMaterializer,
    TaskManifest,
    TaskSpec,
)


def test_materializes_conditions_without_cross_contamination(tmp_path: Path) -> None:
    native = tmp_path / "skills" / "native" / "task-001" / "source-skill"
    pd_vista = tmp_path / "skills" / "pd-vista" / "task-001"
    native.mkdir(parents=True)
    pd_vista.mkdir(parents=True)
    (native / "SKILL.md").write_text("native", encoding="utf-8")
    (pd_vista / "SKILL.md").write_text("root only", encoding="utf-8")
    (pd_vista / "manifest.yaml").write_text(
        "schema_version: 1\nmodules: []\n",
        encoding="utf-8",
    )
    task = TaskSpec(
        task_id="task-001",
        native_skill_path="skills/native/task-001/source-skill",
        pd_vista_skill_path="skills/pd-vista/task-001",
    )
    materializer = SkillMaterializer(tmp_path)

    no_skill = materializer.materialize(task, Condition.NOSKILL)
    native_result = materializer.materialize(task, Condition.NATIVE)
    pd_result = materializer.materialize(task, Condition.PD_VISTA)

    assert list(no_skill.destination.iterdir()) == []
    assert no_skill.visible_root == no_skill.destination
    assert (native_result.destination / "SKILL.md").read_text(encoding="utf-8") == "native"
    assert not (native_result.destination / "modules").exists()
    assert (pd_result.destination / "SKILL.md").read_text(encoding="utf-8") == "root only"
    assert pd_result.visible_root is not None
    assert {
        path.relative_to(pd_result.visible_root).as_posix()
        for path in pd_result.visible_root.rglob("*")
        if path.is_file()
    } == {"SKILL.md"}
    assert not (pd_result.visible_root / "manifest.yaml").exists()
    assert not (pd_result.visible_root / "modules").exists()
    assert native_result.destination != pd_result.destination

    with pytest.raises(MaterializationExistsError, match="overwrite"):
        materializer.materialize(task, Condition.NATIVE)


def test_rejects_skill_source_outside_task_condition_root(tmp_path: Path) -> None:
    outside = tmp_path / "vendor" / "skillsbench"
    outside.mkdir(parents=True)
    task = TaskSpec(
        task_id="task-001",
        native_skill_path="vendor/skillsbench",
        pd_vista_skill_path=None,
    )

    with pytest.raises(MaterializationError, match="escapes"):
        SkillMaterializer(tmp_path).materialize(task, "native")


def test_loads_supported_task_manifest_shapes(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        """tasks:
  - task_id: task-001
    native_skill_path: skills/native/task-001/source-skill
    pd_vista_skill_path: skills/pd-vista/task-001
    enabled: true
""",
        encoding="utf-8",
    )

    manifest = TaskManifest.load(path)

    assert manifest.get("task-001").enabled is True
