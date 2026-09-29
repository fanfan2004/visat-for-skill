from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from test_skillsbench_adapter import _fake_project

from vista.audit import public_artifact_digest
from vista.runtime.cli import run_trial
from vista.telemetry import EventType


def _write_loop_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project, _task = _fake_project(tmp_path)
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
    (conditions_dir / "pd-vista.yaml").write_text("condition_id: pd-vista\n", encoding="utf-8")
    agent_script = project / "fake_agent.py"
    agent_script.write_text(
        """from pathlib import Path
import os
workspace = Path(os.environ["VISTA_WORKSPACE"])
assert os.environ["VISTA_SOURCE_TASK_DIR"] == ""
state = workspace / "agent_attempts.txt"
attempt = int(state.read_text() or "0") if state.exists() else 0
state.write_text(str(attempt + 1))
if attempt == 0:
    assert not (workspace / "modules").exists()
    print("AssertionError: 1 test failed")
    raise SystemExit(1)
assert (workspace / "modules" / "demo-skill" / "SKILL.md").is_file()
(workspace / "answer.txt").write_text("fixed")
print("1 passed")
""",
        encoding="utf-8",
    )
    verifier_script = project / "fake_verifier.py"
    verifier_script.write_text(
        """from pathlib import Path
import os
workspace = Path(os.environ["VISTA_WORKSPACE"])
if (workspace / "answer.txt").is_file():
    raise SystemExit(0)
print("test failed")
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    return project, agent_script, verifier_script


def test_fake_agent_completes_feedback_disclosure_repair_loop(tmp_path: Path) -> None:
    project, agent_script, verifier_script = _write_loop_project(tmp_path)
    events_path = project / "data" / "raw" / "loop.jsonl"
    args = SimpleNamespace(
        task_id="task-001",
        condition="pd-vista",
        trial_index=1,
        seed=7,
        trial_id="loop-trial",
        task_manifest=str(project / "tasks" / "manifest.yaml"),
        condition_config=str(project / "conditions" / "pd-vista.yaml"),
        materialized_root=str(project / "data" / "materialized"),
        events_jsonl=str(events_path),
        agent_command=f'"{sys.executable}" "{agent_script}"',
        verifier_command=f'"{sys.executable}" "{verifier_script}"',
        max_attempts=3,
        no_progress_limit=2,
        agent_timeout_sec=30,
        verifier_timeout_sec=30,
    )

    assert run_trial(args) == 0
    materialized = project / "data" / "materialized" / "pd-vista" / "task-001" / "loop-trial"
    assert (materialized / "agent_attempts.txt").read_text() == "2"
    assert not (materialized / ".pd-vista-sources").exists()
    assert (materialized / "modules" / "demo-skill" / "SKILL.md").is_file()

    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    event_types = [event["event_type"] for event in events]
    assert event_types.count(EventType.AGENT_ATTEMPT.value) == 2
    assert event_types.count(EventType.DISCLOSURE.value) == 1
    assert event_types.count(EventType.REPAIR.value) == 2
    assert event_types[-1] == EventType.TRIAL_END.value
    assert events[-1]["strict_pass"] is True
    final_metadata = events[-1]["metadata"]
    assert final_metadata["artifact_digest"] == public_artifact_digest(materialized)
    assert {
        item["path"] for item in final_metadata["artifact_inventory"]
    } == {"agent_attempts.txt", "answer.txt"}


def test_verifier_public_feedback_restarts_agent_until_pass(tmp_path: Path) -> None:
    project, agent_script, verifier_script = _write_loop_project(tmp_path)
    verifier_script.write_text(
        """from pathlib import Path
import os
workspace = Path(os.environ["VISTA_WORKSPACE"])
state = workspace / "verifier_attempts.txt"
attempt = int(state.read_text() or "0") if state.exists() else 0
state.write_text(str(attempt + 1))
if attempt == 0:
    print("AssertionError: verifier test failed")
    raise SystemExit(1)
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    events_path = project / "data" / "raw" / "verifier-loop.jsonl"
    args = SimpleNamespace(
        task_id="task-001",
        condition="pd-vista",
        trial_index=1,
        seed=8,
        trial_id="verifier-loop",
        task_manifest=str(project / "tasks" / "manifest.yaml"),
        condition_config=str(project / "conditions" / "pd-vista.yaml"),
        materialized_root=str(project / "data" / "materialized"),
        events_jsonl=str(events_path),
        agent_command=f'"{sys.executable}" "{agent_script}"',
        verifier_command=f'"{sys.executable}" "{verifier_script}"',
        max_attempts=3,
        no_progress_limit=2,
        agent_timeout_sec=30,
        verifier_timeout_sec=30,
    )

    assert run_trial(args) == 0
    materialized = project / "data" / "materialized" / "pd-vista" / "task-001" / "verifier-loop"
    assert (materialized / "agent_attempts.txt").read_text() == "3"
    assert (materialized / "verifier_attempts.txt").read_text() == "2"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event["event_type"] == EventType.AGENT_ATTEMPT.value for event in events) == 3
    assert any(
        event["event_type"] == EventType.FEEDBACK.value
        and event["metadata"].get("phase") == "verifier_public_stdout"
        for event in events
    )
    observed_repairs = [
        event
        for event in events
        if event["event_type"] == EventType.REPAIR.value
        and event.get("repair_observed") is not None
    ]
    assert observed_repairs
    assert any(event["repair_observed"] is True for event in observed_repairs)
    assert events[-1]["strict_pass"] is True
