from __future__ import annotations

import json
from pathlib import Path

import pytest

from vista.runtime.opencode_local_backend import (
    BINARY,
    LAUNCHER,
    MODEL,
    REPO_ROOT,
    LocalGateError,
    command_for_turn,
    isolated_environment,
    write_provider_config,
)


def test_command_requires_the_frozen_absolute_host_launcher(tmp_path: Path) -> None:
    workspace = tmp_path / "隔离工作区"
    command = command_for_turn(workspace=workspace, prompt="hello", session_id="ses-001")
    assert command[0] == str(LAUNCHER)
    assert "--pure" in command and "--auto" in command
    assert "--format" in command and "--model" in command
    assert "--attach" not in command and Path(command[0]).is_absolute()
    assert command[command.index("--model") + 1] == "openai/gpt-5.6-terra"
    assert MODEL == "openai/gpt-5.6-terra"
    assert command[-3:] == ["--session", "ses-001", "hello"]


def test_command_accepts_the_model_from_the_current_opencode_config(tmp_path: Path) -> None:
    command = command_for_turn(
        workspace=tmp_path / "isolated", prompt="hello", model="minimax/MiniMax-M3"
    )
    assert command[command.index("--model") + 1] == "minimax/MiniMax-M3"


@pytest.mark.parametrize("workspace", [REPO_ROOT / "bad", Path.cwd()])
def test_command_rejects_workspace_inside_repo(workspace: Path) -> None:
    with pytest.raises(LocalGateError):
        command_for_turn(workspace=workspace, prompt="hello")


def test_task_environment_paths_are_exclusive(tmp_path: Path) -> None:
    first, second = isolated_environment(tmp_path / "task-001"), isolated_environment(tmp_path / "task-002")
    assert set(first) == {"OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"}
    assert not set(first.values()) & set(second.values())


def test_provider_config_uses_environment_key_reference_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-never-persisted")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    path = write_provider_config(tmp_path / "task-001")
    text = path.read_text(encoding="utf-8")
    assert "secret-never-persisted" not in text
    assert "{env:OPENAI_API_KEY}" in text
    config = json.loads(text)
    assert config["share"] == "disabled"
    assert config["permission"]["external_directory"] == "deny"
    assert set(config["provider"]) == {"openai"}
    provider = config["provider"]["openai"]
    assert provider["npm"] == "@ai-sdk/openai"
    assert provider["options"]["setCachekey"] is True
    assert provider["models"] == {
        "gpt-5.6-terra": {"name": "gpt-5.6-terra"}
    }
    assert "openai-compatible" not in text
    assert "cc-gpt-5.6-terra" not in text


def test_nonfrozen_launchers_are_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from vista.runtime import opencode_local_backend as local
    fake = tmp_path / ("opencode.cmd" if LAUNCHER.suffix == ".cmd" else "opencode")
    fake.write_text("@echo off", encoding="utf-8")
    with pytest.raises(LocalGateError):
        local.freeze_launcher(launcher=fake, binary=BINARY)
