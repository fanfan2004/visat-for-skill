from __future__ import annotations

import json
from pathlib import Path

import pytest

from vista.runtime.zcode_local_backend import (
    ELECTRON_BINARY,
    EXPECTED_MODEL,
    MODEL,
    PROVIDER_ID,
    REPO_ROOT,
    ZCODE_SCRIPT,
    LocalGateError,
    command_for_turn,
    current_zcode_model_config,
    isolated_environment,
    model_io_rollout_path,
    parse_model_io_rollout,
    rollout_model_identity,
    rollout_usage_summary,
    turn_environment,
)


def test_command_requires_the_frozen_absolute_host_launcher(tmp_path: Path) -> None:
    workspace = tmp_path / "隔离工作区"
    command = command_for_turn(workspace=workspace, prompt="hello", session_id="sess_001")
    assert command[0] == str(ELECTRON_BINARY)
    assert command[1] == str(ZCODE_SCRIPT)
    assert "-p" in command and "--cwd" in command and "--json" in command
    assert "--mode" in command and command[command.index("--mode") + 1] == "yolo"
    assert "--attach" not in command and Path(command[0]).is_absolute()
    assert command[command.index("--cwd") + 1] == str(workspace.resolve())
    assert "--resume" in command and command.count("hello") == 1


def test_prompt_is_last_without_a_session(tmp_path: Path) -> None:
    command = command_for_turn(workspace=tmp_path / "isolated", prompt="hello")
    assert "--resume" not in command
    assert command[command.index("-p") + 1] == "hello"


@pytest.mark.parametrize("workspace", [REPO_ROOT / "bad", Path.cwd()])
def test_command_rejects_workspace_inside_repo(workspace: Path) -> None:
    with pytest.raises(LocalGateError):
        command_for_turn(workspace=workspace, prompt="hello")


def test_turn_environment_is_node_mode_with_task_scoped_storage(tmp_path: Path) -> None:
    environment = turn_environment(tmp_path / "task-008")
    assert environment["ELECTRON_RUN_AS_NODE"] == "1"
    storage = environment["ZCODE_STORAGE_DIR"]
    assert storage.endswith("zcode-storage") and Path(storage).is_absolute()


def test_task_environment_paths_are_exclusive(tmp_path: Path) -> None:
    first = isolated_environment(tmp_path / "task-008")
    second = isolated_environment(tmp_path / "task-009")
    assert set(first) == {"ZCODE_STORAGE_DIR"}
    assert first["ZCODE_STORAGE_DIR"] != second["ZCODE_STORAGE_DIR"]


def _write_rollout(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _rollout_row(model_id: str = EXPECTED_MODEL, provider_id: str = PROVIDER_ID) -> dict:
    return {
        "completedAt": "2026-09-29T10:32:05.333Z",
        "durationMs": 3381,
        "requestId": "9dd91027-0f07-4cba-88b5-7881afe0501b",
        "attempt": 1,
        "model": {"modelId": model_id, "providerId": provider_id},
        "response": {
            "usage": {
                "inputTokens": 17703,
                "outputTokens": 33,
                "totalTokens": 17736,
                "cacheReadTokens": 0,
                "reasoningTokens": 8,
            },
        },
    }


def test_model_io_rollout_path_is_session_scoped(tmp_path: Path) -> None:
    path = model_io_rollout_path(tmp_path / "zcode-storage", "sess_abc")
    assert path == tmp_path / "zcode-storage" / "cli" / "rollout" / "model-io-sess_abc.jsonl"


def test_rollout_parsing_aggregates_additive_usage(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    rows = [_rollout_row(), _rollout_row()]
    rows[1]["response"] = {"usage": {"inputTokens": 100, "outputTokens": 7, "cacheReadTokens": 50, "reasoningTokens": 0}}
    _write_rollout(path, rows)
    records = parse_model_io_rollout(path)
    assert len(records) == 2
    assert records[0]["model_id"] == EXPECTED_MODEL
    summary = rollout_usage_summary(records)
    assert summary["requests"] == 2
    assert summary["input_tokens"] == 17703 + 100
    assert summary["output_tokens"] == 33 + 7
    assert summary["cache_read_tokens"] == 50
    assert summary["input_plus_output_tokens"] == summary["input_tokens"] + summary["output_tokens"]


def test_rollout_model_identity_passes_on_the_frozen_model(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    _write_rollout(path, [_rollout_row(), _rollout_row()])
    records = parse_model_io_rollout(path)
    assert rollout_model_identity(records) == (PROVIDER_ID, EXPECTED_MODEL)


@pytest.mark.parametrize("model_id", ["glm53-other", "minimax-cn/MiniMax-M3"])
def test_rollout_model_identity_rejects_a_drifted_model(tmp_path: Path, model_id: str) -> None:
    path = tmp_path / "rollout.jsonl"
    rows = [_rollout_row(model_id=model_id), _rollout_row()]
    _write_rollout(path, rows)
    with pytest.raises(LocalGateError):
        rollout_model_identity(parse_model_io_rollout(path))


def test_malformed_rollout_lines_are_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(LocalGateError):
        parse_model_io_rollout(path)


def _provider_config(model_id: str = EXPECTED_MODEL, provider_id: str = PROVIDER_ID) -> dict:
    return {
        "schemaVersion": 1,
        "config": {
            "providerOrder": [provider_id],
            "providerConfigRules": {
                "providerRules": [
                    {
                        "providerId": provider_id,
                        "providerName": "新供应商",
                        "config": {
                            "access": {"type": "api-key", "apiKey": "{env:SECRET}"},
                            "personalModelIds": [model_id],
                            "modelOrder": [model_id],
                        },
                    }
                ]
            },
        },
    }


def test_current_model_config_proves_the_frozen_host_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "provider_config.json"
    config_path.write_text(json.dumps(_provider_config()), encoding="utf-8")
    monkeypatch.setenv("VISTA_ZCODE_PROVIDER_CONFIG", str(config_path))
    resolved = current_zcode_model_config()
    assert resolved["provider_id"] == PROVIDER_ID
    assert resolved["model_id"] == EXPECTED_MODEL
    assert resolved["config_path"] == str(config_path)
    assert "hunnu" not in json.dumps(resolved)


@pytest.mark.parametrize("model_id", ["other-model"])
def test_current_model_config_rejects_a_drifted_host_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_id: str) -> None:
    config_path = tmp_path / "provider_config.json"
    config_path.write_text(json.dumps(_provider_config(model_id=model_id)), encoding="utf-8")
    monkeypatch.setenv("VISTA_ZCODE_PROVIDER_CONFIG", str(config_path))
    with pytest.raises(LocalGateError):
        current_zcode_model_config()


def test_current_model_config_requires_the_host_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISTA_ZCODE_PROVIDER_CONFIG", str(tmp_path / "absent.json"))
    with pytest.raises(LocalGateError):
        current_zcode_model_config()


def test_nonfrozen_launchers_are_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from vista.runtime import zcode_local_backend as local
    with pytest.raises(LocalGateError):
        local.freeze_launcher(electron=tmp_path / "NotZCode", script=ZCODE_SCRIPT)
