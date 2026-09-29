from __future__ import annotations

import asyncio
from types import SimpleNamespace

from vista.runtime.opencode_image import (
    OpenCodeIdentityMismatch,
    probe_opencode_identity,
    task_image_config_override,
)


class _ProbeEnv:
    def __init__(self, result: SimpleNamespace) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def exec(self, command: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"command": command, **kwargs})
        return self.result


class _Runtime:
    def __init__(self, env: _ProbeEnv) -> None:
        self.env = env


def test_runtime_probe_rejects_digest_mismatch_without_an_installer_command() -> None:
    env = _ProbeEnv(
        SimpleNamespace(
            stdout="/usr/local/bin/opencode\n1.18.13\n" + "a" * 64 + "  /usr/local/bin/opencode\n",
            stderr="",
            return_code=0,
            timed_out=False,
        )
    )
    runtime = _Runtime(env)

    try:
        asyncio.run(
            probe_opencode_identity(
                runtime,
                workspace="/workspace",
                expected_version="1.18.13",
                expected_digest="b" * 64,
            )
        )
    except OpenCodeIdentityMismatch as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("a mismatched preinstalled binary must quarantine")

    assert len(env.calls) == 1
    command = str(env.calls[0]["command"])
    assert "command -v opencode" in command
    assert "opencode --version" in command
    assert "sha256sum /usr/local/bin/opencode" in command
    assert "curl" not in command
    assert "wget" not in command


def test_runtime_probe_rejects_version_mismatch_before_agent_execution() -> None:
    env = _ProbeEnv(
        SimpleNamespace(
            stdout="/usr/local/bin/opencode\n1.18.12\n"
            + "b" * 64
            + "  /usr/local/bin/opencode\n",
            stderr="",
            return_code=0,
            timed_out=False,
        )
    )

    try:
        asyncio.run(
            probe_opencode_identity(
                _Runtime(env),
                workspace="/workspace",
                expected_version="1.18.13",
                expected_digest="b" * 64,
            )
        )
    except OpenCodeIdentityMismatch as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("a mismatched preinstalled version must quarantine")

    assert len(env.calls) == 1


def test_task_image_override_selects_prebuilt_immutable_image() -> None:
    assert task_image_config_override("vista-task-001@sha256:" + "c" * 64) == {
        "sandbox": {"docker_image": "vista-task-001@sha256:" + "c" * 64}
    }
