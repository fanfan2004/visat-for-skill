from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_skillsbench_adapter import _fake_project

from vista.audit import has_progress_signal
from vista.feedback import FeedbackParser
from vista.runtime.benchflow_backend import (
    _close_runtime_preserving_prebuilt_image,
    _prepare_skill_mount,
    _upload_new_disclosures,
)
from vista.runtime.cli import TrialRecorder
from vista.runtime.materialization import Condition, SkillMaterializer
from vista.runtime.opencode_backend import (
    OpenCodeConfig,
    _OpenCodeResult,
    _agent_terminal_reason,
    _parse_opencode_result,
    _record_disclosure_upload,
    _record_opencode_tool_output,
    _run_opencode,
    run_opencode_trial,
)
from vista.runtime.opencode_image import stage_task_image_context, tree_sha256
from vista.runtime.router import DisclosureRouter, SkillManifest
from vista.runtime.usage import UsageAccumulator
from vista.runtime.verifier import QUARANTINE_EXIT_CODE


def test_opencode_config_prefers_generic_vista_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISTA_API_KEY", "test-key")
    monkeypatch.setenv("VISTA_BASE_URL", "https://api.example.invalid/v1")
    monkeypatch.setenv("VISTA_MODEL", "cc-gpt-5.6-terra")
    monkeypatch.setenv("VISTA_PROVIDER_ID", "codexzh-openai-compatible")
    monkeypatch.setenv("VISTA_PROVIDER_NAME", "CodexZH OpenAI Compatible")
    monkeypatch.setenv("VISTA_ALLOWED_ACTUAL_MODELS", "gpt-5.6-terra")
    config = OpenCodeConfig.from_environment()
    assert config.provider_id == "codexzh-openai-compatible"
    assert config.model == "cc-gpt-5.6-terra"
    assert config.expected_actual_models == ("gpt-5.6-terra",)


@pytest.mark.parametrize(
    ("payload", "stderr", "expected_class", "expected_status"),
    [
        ({"data": {"statusCode": 401, "message": "invalid api key sk-secret"}}, "", "authentication_error", 401),
        ({"data": {"status": 404, "code": "model_not_found", "responseBody": {"error": {"message": "unknown model"}}}}, "", "model_not_found", 404),
        ({"data": {"statusCode": 400, "message": "provider configuration invalid"}}, "", "provider_config_error", 400),
        ({}, "HTTP 400 provider config rejected", "provider_config_error", 400),
    ],
)
def test_opencode_parser_sanitizes_nested_api_errors(
    payload: dict[str, object], stderr: str, expected_class: str, expected_status: int
) -> None:
    stream = json.dumps({"type": "error", "error": payload})
    result = _parse_opencode_result(SimpleNamespace(stdout="__VISTA_API_KEY_PRESENT__=1\n" + stream, stderr=stderr, return_code=1))
    assert result.api_key_present is True
    assert result.api_error_class == expected_class
    assert result.api_error_status_code == expected_status
    assert "sk-secret" not in (result.api_error_summary or "")


def test_api_failure_precedes_actual_model_validation() -> None:
    config = OpenCodeConfig("key", "https://example.invalid/v1", "cc-gpt-5.6-terra", expected_actual_models=("gpt-5.6-terra",))
    result = _OpenCodeResult(return_code=1, session_id=None, tool_calls=0, requests=0, input_tokens=0, output_tokens=0, total_tokens=0, stop_reason=None, final_text="", error="APIError", tool_outputs=(), api_error_class="bad_request")
    assert _agent_terminal_reason(result, config) == "agent_api_error"
from vista.telemetry import EventType, JsonlEventLog


def _event_stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def test_runtime_cleanup_preserves_prebuilt_image() -> None:
    class FakeEnvironment:
        def __init__(self) -> None:
            self._keep_containers = False
            self._started = True
            self.stop_calls: list[bool] = []

        async def stop(self, delete: bool) -> None:
            if not self._started:
                return
            self.stop_calls.append(delete)
            self._started = False

    class FakeRuntime:
        def __init__(self) -> None:
            environment = FakeEnvironment()
            self.rollout = SimpleNamespace(
                env=environment,
                _env=environment,
                _env_externally_owned=False,
            )

        @property
        def env(self) -> FakeEnvironment:
            return self.rollout.env

        async def close(self) -> None:
            if not self.rollout._env_externally_owned:
                await self.env.stop(delete=True)

    runtime = FakeRuntime()
    asyncio.run(_close_runtime_preserving_prebuilt_image(runtime))

    assert runtime.rollout._env_externally_owned is True
    assert runtime.env._keep_containers is False
    assert runtime.env.stop_calls == [False]


def test_opencode_backend_never_uses_destructive_runtime_close_directly() -> None:
    project = Path(__file__).resolve().parents[1]
    source = (project / "src" / "vista" / "runtime" / "opencode_backend.py").read_text(
        encoding="utf-8"
    )

    assert "await runtime.close()" not in source
    assert source.count("_close_runtime_preserving_prebuilt_image(runtime)") == 3


class _FakeOpenCodeEnv:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []
        self.uploads: list[tuple[Path, str]] = []
        self.visible_files: set[str] = set()
        self.upload_snapshots: list[set[str]] = []

    async def exec(self, command: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"command": command, **kwargs})
        if command.startswith("chmod "):
            return SimpleNamespace(stdout="", stderr="", return_code=0, timed_out=False)
        if command.startswith("find /vista-skill"):
            return SimpleNamespace(
                stdout="\n".join(sorted(self.visible_files)),
                stderr="",
                return_code=0,
                timed_out=False,
            )
        if command == "test ! -e /vista-skill":
            return SimpleNamespace(stdout="", stderr="", return_code=0, timed_out=False)
        if command.startswith("find ."):
            return SimpleNamespace(stdout="", stderr="", return_code=0, timed_out=False)
        return SimpleNamespace(
            stdout=self.outputs.pop(0),
            stderr="",
            return_code=0,
            timed_out=False,
        )

    async def upload_dir(self, source: Path, destination: str) -> None:
        self.uploads.append((Path(source), destination))
        snapshot: set[str] = set()
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            suffix = destination.removeprefix("/vista-skill").strip("/")
            visible = f"{suffix}/{relative}" if suffix else relative
            self.visible_files.add(visible)
            snapshot.add(visible)
        self.upload_snapshots.append(snapshot)


class _FakeRuntime:
    def __init__(self, env: _FakeOpenCodeEnv) -> None:
        self.env = env


def test_fake_container_enforces_noskill_and_native_visibility(tmp_path: Path) -> None:
    project, task = _fake_project(tmp_path)
    materializer = SkillMaterializer(project)
    no_skill = materializer.materialize(task, Condition.NOSKILL, trial_id="noskill")
    native = materializer.materialize(task, Condition.NATIVE, trial_id="native")
    env = _FakeOpenCodeEnv([])
    runtime = _FakeRuntime(env)

    asyncio.run(_prepare_skill_mount(runtime, no_skill, Condition.NOSKILL, project))
    assert "test ! -e /vista-skill" in {call["command"] for call in env.calls}

    asyncio.run(_prepare_skill_mount(runtime, native, Condition.NATIVE, project))
    assert env.visible_files == {"demo-skill/SKILL.md", "demo-skill/reference.md"}
    assert any(call["command"].startswith("chmod -R a-w") for call in env.calls)


def test_formal_randomization_metadata_does_not_claim_seed_control(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    recorder = TrialRecorder(
        log=JsonlEventLog(log_path, expected_trial_id="formal-trial"),
        trial_id="formal-trial",
        task_id="task-001",
        condition="pd-vista",
        trial_index=1,
        seed=20260805,
        replicate_id=2,
        pair_id="task-001-replicate-02",
        randomization_id="schedule-v1-latin-square-20260805",
    )

    recorder.emit(EventType.TRIAL_START, source="test")
    event = next(iter(JsonlEventLog(log_path).read_events()))

    assert event.seed is None
    assert event.metadata["replicate_id"] == 2
    assert event.metadata["pair_id"] == "task-001-replicate-02"
    assert event.metadata["randomization_id"] == "schedule-v1-latin-square-20260805"
    assert event.metadata["seed_controlled"] is False
    assert event.metadata["seed_claim"] == "none"


def test_opencode_fake_cli_continues_the_same_session_without_leaking_key() -> None:
    first_output = _event_stream(
        {
            "type": "step_start",
            "sessionID": "ses-fake-001",
            "modelID": "deepseek-v4-flash-2026-08",
            "providerID": "deepseek-official",
        },
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {
                    "input": {
                        "command": "grep -n test /vista-skill/modules/demo-skill/SKILL.md"
                    }
                },
            },
        },
        {
            "type": "tool_result",
            "part": {"state": {"output": "module text", "exitCode": 0}},
        },
        {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 10, "output": 4}}},
    )
    second_output = _event_stream(
        {"type": "step_start", "sessionID": "ses-fake-001"},
        {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 8, "output": 3}}},
    )
    key = "sk-" + "x" * 32
    env = _FakeOpenCodeEnv([first_output, second_output])
    runtime = _FakeRuntime(env)
    config = OpenCodeConfig(
        api_key=key,
        base_url="https://provider.invalid/v1",
        model="deepseek-v4-flash",
    )

    first = asyncio.run(
        _run_opencode(
            runtime,
            "/workspace",
            config,
            "initial prompt",
            session_id=None,
            timeout_sec=30,
        )
    )
    second = asyncio.run(
        _run_opencode(
            runtime,
            "/workspace",
            config,
            "repair prompt",
            session_id=first.session_id,
            timeout_sec=30,
        )
    )

    assert isinstance(first, _OpenCodeResult)
    assert first.session_id == "ses-fake-001"
    assert first.module_accesses == ("demo-skill",)
    assert first.tool_commands
    assert second.session_id == first.session_id
    assert "--session ses-fake-001" in str(env.calls[1]["command"])
    assert key not in str(env.calls[0]["command"])
    assert env.calls[0]["env"] == {"VISTA_API_KEY": key}
    assert first.actual_model_ids == ("deepseek-v4-flash-2026-08",)
    assert first.provider_ids == ("deepseek-official",)


def test_opencode_parser_adds_per_step_usage_and_not_max() -> None:
    result = _parse_opencode_result(
        SimpleNamespace(
            stdout=_event_stream(
                {"type": "step_start"},
                {"type": "step_finish", "part": {"tokens": {"input": 11, "output": 5}}},
                {"type": "step_start"},
                {"type": "step_finish", "part": {"tokens": {"input": 7, "output": 3}}},
            ),
            return_code=0,
        )
    )

    assert result.requests == 2
    assert result.input_tokens == 18
    assert result.output_tokens == 8
    assert result.total_tokens == 26

    usage = UsageAccumulator()
    usage.add(
        requests=result.requests,
        tool_calls=result.tool_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    usage.add(requests=1, tool_calls=2, total_tokens=9)
    usage.add(requests=1, input_tokens=2, output_tokens=3)
    usage.add(total_tokens=4)
    assert usage.snapshot() == {
        "requests": 4,
        "tool_calls": 2,
        "input_tokens": 20,
        "output_tokens": 24,
        "total_tokens": 44,
    }


def test_no_progress_guard_requires_new_auditable_signal() -> None:
    assert not has_progress_signal(
        artifact_before="artifact-a",
        artifact_after="artifact-a",
        previous_feedback_fingerprints=["same-feedback"],
        observed_feedback_fingerprints=["same-feedback"],
    )
    assert has_progress_signal(
        artifact_before="artifact-a",
        artifact_after="artifact-a",
        previous_feedback_fingerprints=["same-feedback"],
        observed_feedback_fingerprints=["new-feedback"],
    )


def test_fake_opencode_root_feedback_upload_and_next_turn_access(tmp_path: Path) -> None:
    project, task = _fake_project(tmp_path)
    materializer = SkillMaterializer(project)
    result = materializer.materialize(task, Condition.PD_VISTA, trial_id="fake-e2e")
    router = DisclosureRouter(
        SkillManifest.load(result.destination / "manifest.yaml", require_files=False)
    )
    env = _FakeOpenCodeEnv(
        [
            _event_stream(
                {"type": "step_start", "sessionID": "ses-e2e"},
                {
                    "type": "tool_result",
                    "part": {
                        "state": {
                            "output": "1 tests failed",
                            "exitCode": 1,
                        }
                    },
                },
            ),
            _event_stream(
                {"type": "step_start", "sessionID": "ses-e2e"},
                {
                    "type": "tool_use",
                    "part": {
                        "tool": "bash",
                        "state": {
                            "input": {
                                "command": "cat /vista-skill/modules/demo-skill/SKILL.md"
                            }
                        },
                    },
                },
            ),
        ]
    )
    runtime = _FakeRuntime(env)
    log_path = project / "data" / "raw" / "fake-e2e.jsonl"
    recorder = TrialRecorder(
        log=JsonlEventLog(log_path, expected_trial_id="fake-e2e"),
        trial_id="fake-e2e",
        task_id="task-001",
        condition="pd-vista",
        trial_index=1,
        seed=1,
    )
    disclosed: list[str] = []

    async def scenario() -> tuple[object, object]:
        await _prepare_skill_mount(runtime, result, Condition.PD_VISTA, project)
        first = await _run_opencode(
            runtime,
            "/workspace",
            OpenCodeConfig("sk-" + "y" * 32, "https://provider.invalid", "fake-model"),
            "initial",
            session_id=None,
            timeout_sec=30,
        )
        before = len(disclosed)
        _record_opencode_tool_output(
            recorder,
            FeedbackParser(),
            router,
            result,
            disclosed,
            first.tool_outputs[0],
        )
        uploaded = await _upload_new_disclosures(runtime, result, disclosed, before, project)
        _record_disclosure_upload(
            recorder,
            attempt=1,
            modules=uploaded,
            session_id=first.session_id,
        )
        second = await _run_opencode(
            runtime,
            "/workspace",
            OpenCodeConfig("sk-" + "y" * 32, "https://provider.invalid", "fake-model"),
            "repair",
            session_id=first.session_id,
            timeout_sec=30,
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert env.upload_snapshots[0] == {"SKILL.md"}
    assert env.uploads[1][1] == "/vista-skill/modules/demo-skill"
    assert disclosed == ["demo-skill"]
    assert first.session_id == "ses-e2e"
    assert second.session_id == first.session_id
    assert second.module_accesses == ("demo-skill",)
    confirmed = next(
        event
        for event in JsonlEventLog(log_path).read_events()
        if event.event_type is EventType.DISCLOSURE
        and event.metadata.get("upload_status") == "confirmed"
    )
    assert confirmed.progress_after_event is None
    assert confirmed.metadata["visible_before_next_attempt"] is True


def test_image_digest_mismatch_quarantines_before_agent_or_vista_loop(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "vendor" / "skillsbench" / "tasks" / "fake-task"
    (source / "environment").mkdir(parents=True)
    (source / "verifier").mkdir()
    (source / "task.md").write_text("fake task", encoding="utf-8")
    (source / "environment" / "Dockerfile").write_text(
        "FROM ubuntu:24.04\nWORKDIR /root\n", encoding="utf-8"
    )
    (source / "verifier" / "test.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"verified-opencode")
    import hashlib

    opencode_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    context = stage_task_image_context(
        source_task=source,
        project_root=tmp_path,
        task_id="task-001",
        build_id="trial-gate",
        artifact_path=artifact,
        opencode_digest=opencode_digest,
    )
    registry = tmp_path / "image-registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": {
                    "task-001": {
                        "status": "qualified",
                        "image_tag": "vista-task-001",
                        "image_digest": "sha256:" + "a" * 64,
                        "stage_root": str(context.stage_root),
                        "source_task_digest": tree_sha256(source),
                        "vendor_dockerfile_digest": context.vendor_dockerfile_digest,
                        "staged_dockerfile_digest": context.staged_dockerfile_digest,
                        "build_context_digest": context.build_context_digest,
                        "opencode_version": "1.18.13",
                        "opencode_binary_digest": "b" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeEnv:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def exec(self, command: str, **kwargs: object) -> SimpleNamespace:
            del kwargs
            self.calls.append(command)
            if command.startswith("id -u agent"):
                return SimpleNamespace(stdout="", stderr="", return_code=0, timed_out=False)
            if command == "pwd":
                return SimpleNamespace(stdout="/root\n", stderr="", return_code=0, timed_out=False)
            if "command -v opencode" in command:
                return SimpleNamespace(
                    stdout="/usr/local/bin/opencode\n1.18.13\n" + "a" * 64,
                    stderr="",
                    return_code=0,
                    timed_out=False,
                )
            raise AssertionError(f"unexpected command after identity gate: {command}")

    class FakeRuntime:
        def __init__(self) -> None:
            self.env = FakeEnv()
            self.rollout = SimpleNamespace()
            self.workspace = "/root"
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    runtime = FakeRuntime()
    created_configs: list[object] = []

    class FakeTaskRuntime:
        @classmethod
        async def create(cls, config: object) -> FakeRuntime:
            created_configs.append(config)
            return runtime

    benchflow = types.ModuleType("benchflow")
    rollout = types.ModuleType("benchflow.rollout")
    rollout.TaskRuntime = FakeTaskRuntime
    rollout.TaskRuntimeConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    benchflow.rollout = rollout
    monkeypatch.setitem(sys.modules, "benchflow", benchflow)
    monkeypatch.setitem(sys.modules, "benchflow.rollout", rollout)
    monkeypatch.setattr(
        "vista.runtime.opencode_backend._resolve_source_task", lambda _root, _task: source
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key-not-recorded")

    task = SimpleNamespace(
        task_id="task-001",
        source_task_path="vendor/skillsbench/tasks/fake-task",
    )
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    result = SimpleNamespace(destination=materialized, visible_root=materialized)
    log_path = tmp_path / "events.jsonl"
    recorder = TrialRecorder(
        log=JsonlEventLog(log_path, expected_trial_id="trial-gate"),
        trial_id="trial-gate",
        task_id="task-001",
        condition="noskill",
        trial_index=1,
        seed=None,
        randomization_id="qualification-gate",
    )
    args = SimpleNamespace(
        trial_id="trial-gate",
        build_timeout_sec=30,
        verifier_timeout_sec=30,
        seed=None,
        randomization_id="qualification-gate",
        image_manifest_path=str(registry),
    )

    exit_code = asyncio.run(
        run_opencode_trial(
            args,
            project_root=tmp_path,
            task=task,
            condition=Condition.NOSKILL,
            result=result,
            recorder=recorder,
            router=None,
        )
    )

    events = list(JsonlEventLog(log_path).read_events())
    event_types = [event.event_type for event in events]
    assert exit_code == QUARANTINE_EXIT_CODE
    assert runtime.closed is True
    assert created_configs[0].config_override == {
        "sandbox": {"docker_image": "vista-task-001"}
    }
    assert any("command -v opencode" in command for command in runtime.env.calls)
    assert not any(
        command.startswith("curl") or command.startswith("wget")
        for command in runtime.env.calls
    )
    assert EventType.AGENT_ATTEMPT not in event_types
    assert EventType.FEEDBACK not in event_types
    assert EventType.ROUTING not in event_types
    assert EventType.DISCLOSURE not in event_types
    assert EventType.REPAIR not in event_types
    trial_end = next(event for event in events if event.event_type is EventType.TRIAL_END)
    assert trial_end.metadata["agent_attempts"] == 0
    assert trial_end.failure_category == "infrastructure_error"
