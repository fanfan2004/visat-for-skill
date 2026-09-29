from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import vista.runtime.cli as cli
from vista.feedback import FeedbackParser, FeedbackSource
from vista.runtime.benchflow_backend import _prepare_skill_mount
from vista.runtime.cli import (
    TrialRecorder,
    _is_unroutable_pd_feedback,
    _process_observation,
    _record_repair,
)
from vista.runtime.materialization import (
    Condition,
    MaterializationResult,
    TaskManifest,
    TaskSpec,
)
from vista.runtime.opencode_backend import _parse_opencode_result
from vista.runtime.router import DisclosureRouter, SkillManifest
from vista.telemetry import EventType, JsonlEventLog


class _RecordingEnv:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []

    async def upload_dir(self, source: Path, destination: str) -> None:
        self.uploads.append((Path(source), destination))


class _RecordingRuntime:
    def __init__(self) -> None:
        self.env = _RecordingEnv()


def test_pd_vista_initial_mount_hides_routing_manifest(tmp_path: Path) -> None:
    destination = tmp_path / "materialized"
    destination.mkdir()
    (destination / "SKILL.md").write_text("root", encoding="utf-8")
    (destination / "manifest.yaml").write_text("schema_version: 1\nmodules: []\n", encoding="utf-8")
    result = MaterializationResult(
        task_id="task-001",
        condition=Condition.PD_VISTA,
        destination=destination,
        source=None,
        files=(Path("SKILL.md"), Path("manifest.yaml")),
        hidden_source=tmp_path / "hidden",
    )
    runtime = _RecordingRuntime()

    asyncio.run(_prepare_skill_mount(runtime, result, Condition.PD_VISTA, tmp_path))

    assert len(runtime.env.uploads) == 1
    visible_root = runtime.env.uploads[0][0]
    assert {path.name for path in visible_root.iterdir()} == {"SKILL.md"}


def test_infrastructure_verifier_timeout_is_quarantined_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    manifest_path = tasks_dir / "manifest.yaml"
    manifest_path.write_text("tasks: []\n", encoding="utf-8")
    condition_path = tmp_path / "conditions.yaml"
    condition_path.write_text("condition_id: noskill\n", encoding="utf-8")
    events_path = tmp_path / "data" / "raw" / "infra-timeout.jsonl"
    task = TaskSpec(task_id="task-001", native_skill_path=None, pd_vista_skill_path=None)
    materialized = tmp_path / "materialized"

    class FakeMaterializer:
        def __init__(self, _root: Path) -> None:
            pass

        def materialize(
            self, _task: TaskSpec, _condition: Condition, *, trial_id: str
        ) -> MaterializationResult:
            destination = materialized / trial_id
            destination.mkdir(parents=True)
            return MaterializationResult(
                task_id="task-001",
                condition=Condition.NOSKILL,
                destination=destination,
                source=None,
                files=(),
            )

    calls = {"agent": 0, "verifier": 0}

    def fake_run_command(command: str, *_args: object, **_kwargs: object) -> cli.CommandResult:
        if command == "agent":
            calls["agent"] += 1
            return cli.CommandResult("", "", 0)
        calls["verifier"] += 1
        return cli.CommandResult(
            "soft verifier timed out after 600.0s", "", 124, timed_out=True
        )

    monkeypatch.setattr(cli.TaskManifest, "load", lambda _path: TaskManifest((task,)))
    monkeypatch.setattr(cli, "SkillMaterializer", FakeMaterializer)
    monkeypatch.setattr(cli, "_run_command", fake_run_command)

    args = SimpleNamespace(
        task_id="task-001",
        condition="noskill",
        trial_index=1,
        seed=7,
        trial_id="infra-timeout",
        task_manifest=str(manifest_path),
        condition_config=str(condition_path),
        materialized_root=str(tmp_path / "data" / "materialized"),
        events_jsonl=str(events_path),
        backend="commands",
        agent_command="agent",
        verifier_command="verifier",
        max_attempts=3,
        no_progress_limit=2,
        agent_timeout_sec=2,
        verifier_timeout_sec=2,
    )

    assert cli.run_trial(args) != 0
    assert calls == {"agent": 0, "verifier": 1}
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert not any(event["event_type"] == EventType.FEEDBACK.value for event in events)
    assert not any(event["event_type"] == EventType.ROUTING.value for event in events)
    assert not any(event["event_type"] == EventType.REPAIR.value for event in events)
    assert not any(event["event_type"] == EventType.AGENT_ATTEMPT.value for event in events)
    verifier_events = [
        event for event in events if event["event_type"] == EventType.VERIFIER_RESULT.value
    ]
    assert verifier_events[0]["failure_category"] == "infrastructure_error"
    assert verifier_events[0]["metadata"]["infrastructure_class"] == "setup_timeout"
    assert events[-1]["metadata"]["trial_status"] == "quarantined"


def test_disclosure_and_repair_do_not_claim_unobserved_progress(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """schema_version: 1
modules:
  - id: module-a
    path: module-a/SKILL.md
    triggers: [test_failure]
""",
        encoding="utf-8",
    )
    (tmp_path / "module-a").mkdir()
    (tmp_path / "module-a" / "SKILL.md").write_text("module", encoding="utf-8")
    log_path = tmp_path / "events.jsonl"
    recorder = TrialRecorder(
        log=JsonlEventLog(log_path, expected_trial_id="trial"),
        trial_id="trial",
        task_id="task-001",
        condition="pd-vista",
        trial_index=1,
        seed=1,
    )
    result = MaterializationResult(
        task_id="task-001",
        condition=Condition.PD_VISTA,
        destination=tmp_path / "destination",
        source=None,
        files=(),
        hidden_source=tmp_path / "hidden",
    )

    class FakeMaterializer:
        def expose_module(self, _result: MaterializationResult, module_id: str) -> Path:
            return tmp_path / module_id

    router = DisclosureRouter(SkillManifest.load(manifest_path))
    parser = FeedbackParser()
    disclosed: list[str] = []

    _process_observation(
        recorder,
        parser,
        router,
        FakeMaterializer(),
        result,
        disclosed,
        FeedbackSource.TEST_OUTPUT,
        "AssertionError: test failed",
        1,
        "fake_public_check",
    )
    _record_repair(recorder, 2, disclosed)

    events = list(JsonlEventLog(log_path).read_events())
    disclosure = next(event for event in events if event.event_type is EventType.DISCLOSURE)
    repair = next(event for event in events if event.event_type is EventType.REPAIR)
    assert disclosure.progress_after_event is None
    assert repair.repair_observed is None
    assert repair.progress_after_event is None


def test_unmatched_pd_feedback_requires_quarantine_before_repair(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """schema_version: 1
modules:
  - id: module-a
    path: module-a/SKILL.md
    triggers: [schema_error]
""",
        encoding="utf-8",
    )
    (tmp_path / "module-a").mkdir()
    (tmp_path / "module-a" / "SKILL.md").write_text("module", encoding="utf-8")
    log_path = tmp_path / "unmatched.jsonl"
    recorder = TrialRecorder(
        log=JsonlEventLog(log_path, expected_trial_id="unmatched"),
        trial_id="unmatched",
        task_id="task-002",
        condition="pd-vista",
        trial_index=1,
        seed=None,
    )
    result = MaterializationResult(
        task_id="task-002",
        condition=Condition.PD_VISTA,
        destination=tmp_path / "destination",
        source=None,
        files=(),
        hidden_source=tmp_path / "hidden",
    )

    class FakeMaterializer:
        def expose_module(self, _result: MaterializationResult, module_id: str) -> Path:
            return tmp_path / module_id

    state: dict[str, object] = {"fingerprints": []}
    _process_observation(
        recorder,
        FeedbackParser(),
        DisclosureRouter(SkillManifest.load(manifest_path)),
        FakeMaterializer(),
        result,
        [],
        FeedbackSource.TEST_OUTPUT,
        json.dumps(
            {
                "kind": "vista_public_verifier_feedback",
                "feedback_types": ["test_failure"],
                "failing_checks": ["test_tokens_efficient"],
            }
        ),
        1,
        "verifier_public_feedback",
        observation_state=state,
    )

    assert state["routing_status"] == "unmatched"
    assert _is_unroutable_pd_feedback(Condition.PD_VISTA, state)
    events = list(JsonlEventLog(log_path).read_events())
    assert not any(event.event_type is EventType.DISCLOSURE for event in events)
    assert not any(event.event_type is EventType.REPAIR for event in events)


def test_opencode_parser_retains_module_access_evidence() -> None:
    fake_result = SimpleNamespace(
        stdout='\n'.join(
            [
                json.dumps(
                    {
                        "type": "tool_use",
                        "part": {
                            "tool": "bash",
                            "state": {
                                "input": {
                                    "command": "grep -n test /vista-skill/modules/module-a/SKILL.md"
                                }
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_result",
                        "part": {
                            "tool": "bash",
                            "state": {"status": "completed", "output": "module"},
                        },
                    }
                ),
            ]
        ),
        return_code=0,
        timed_out=False,
    )

    parsed = _parse_opencode_result(fake_result)

    assert parsed.module_accesses == ("module-a",)
