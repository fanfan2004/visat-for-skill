from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_skillsbench_adapter import _fake_project

import vista.runtime.cli as cli
from vista.runtime.cli import CommandResult
from vista.runtime.task_metadata import SkillsBenchTaskMetadata


def test_real_skillsbench_task_budgets_are_loaded_and_capped() -> None:
    root = Path(__file__).resolve().parents[1] / "vendor" / "skillsbench" / "tasks"
    expected = {
        "python-scala-translation": (600, 600),
        "enterprise-information-search": (1800, 600),
        "data-to-d3": (1800, 240),
        "powerlifting-coef-calc": (600, 600),
        "organize-messy-files": (900, 900),
        "fix-build-agentops": (7200, 240),
    }

    for task_id, (agent_timeout, verifier_timeout) in expected.items():
        metadata = SkillsBenchTaskMetadata.load(root / task_id)
        assert metadata.agent_timeout_sec == agent_timeout
        assert metadata.verifier_timeout_sec == verifier_timeout
        assert metadata.cap_agent_timeout(1800) == min(agent_timeout, 1800)
        assert metadata.cap_verifier_timeout(900) == min(verifier_timeout, 900)
        assert metadata.digest
        assert metadata.network_mode == "public"


def test_task_metadata_requires_closed_front_matter(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    (task_root / "task.md").write_text("---\nagent:\n  timeout_sec: 10\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not closed"):
        SkillsBenchTaskMetadata.load(task_root)


def test_command_backend_caps_readiness_agent_and_verifier_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _task = _fake_project(tmp_path)
    task_root = project / "vendor" / "skillsbench" / "tasks" / "demo-task"
    (task_root / "task.md").write_text(
        """---
agent:
  timeout_sec: 11
verifier:
  timeout_sec: 7
sandbox:
  build_timeout_sec: 13
  network_mode: none
---
public task
""",
        encoding="utf-8",
    )
    tasks_dir = project / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "manifest.yaml").write_text(
        """tasks:
  - task_id: task-001
    source_task_path: vendor/skillsbench/tasks/demo-task
    source_skill_path: vendor/skillsbench/tasks/demo-task/environment/skills
    native_skill_path: vendor/skillsbench/tasks/demo-task/environment/skills
    pd_vista_skill_path: skills/pd-vista/task-001
""",
        encoding="utf-8",
    )
    conditions_dir = project / "conditions"
    conditions_dir.mkdir()
    (conditions_dir / "noskill.yaml").write_text("condition_id: noskill\n", encoding="utf-8")
    events_path = project / "data" / "raw" / "budget.jsonl"
    calls: list[tuple[str, int]] = []

    def fake_run_command(
        command: str, _cwd: Path, _environment: dict[str, str], timeout_sec: int
    ) -> CommandResult:
        calls.append((command, timeout_sec))
        return CommandResult("", "", 0)

    monkeypatch.setattr(cli, "_run_command", fake_run_command)
    args = SimpleNamespace(
        task_id="task-001",
        condition="noskill",
        trial_index=1,
        seed=1,
        trial_id="budget-trial",
        task_manifest=str(tasks_dir / "manifest.yaml"),
        condition_config=str(conditions_dir / "noskill.yaml"),
        materialized_root=str(project / "data" / "materialized"),
        events_jsonl=str(events_path),
        backend="commands",
        agent_command="agent",
        verifier_command="verifier",
        max_attempts=1,
        no_progress_limit=1,
        agent_timeout_sec=None,
        verifier_timeout_sec=None,
    )

    assert cli.run_trial(args) == 0
    assert calls == [("verifier", 7), ("agent", 11), ("verifier", 7)]
    trial_start = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
    assert trial_start["metadata"]["declared_verifier_timeout_sec"] == 7
    assert trial_start["metadata"]["effective_agent_timeout_sec"] == 11
    assert trial_start["metadata"]["effective_build_timeout_sec"] == 13
    assert trial_start["metadata"]["timeout_budget_source"] == "skillsbench_task_metadata_default"
    assert trial_start["metadata"]["sandbox_network_mode"] == "none"
