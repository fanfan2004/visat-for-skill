"""Small DeepSeek tool-calling agent used by the project-owned pilot adapter.

This module deliberately uses the official OpenAI-compatible HTTP endpoint and
keeps the sandbox tool on the host side.  The model never receives verifier
paths, oracle files, or the API key.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vista.audit import redact_secret

_PROTECTED_PATH_PATTERN = re.compile(
    r"(?:^|[\s'\"`])/(?:verifier|oracle|solution|logs/verifier)(?:[/\s'\"`]|$)|"
    r"\b(?:ground_truth|expected_answer|reward\.txt)\b",
    re.IGNORECASE,
)


class DeepSeekAgentError(RuntimeError):
    """A non-secret, user-actionable agent adapter failure."""


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    request_timeout_sec: int = 180
    max_tool_rounds: int = 48

    @classmethod
    def from_environment(cls) -> DeepSeekConfig:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekAgentError(
                "DEEPSEEK_API_KEY is not set in the Windows user environment"
            )
        base_url = (
            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
            or "https://api.deepseek.com"
        ).rstrip("/")
        model = (
            os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
            or "deepseek-v4-flash"
        )
        # Accept the project-wide OpenAI-style spelling without sending the
        # provider prefix to DeepSeek's official endpoint.
        if model.startswith("openai/"):
            model = model.removeprefix("openai/")
        return cls(api_key=api_key, base_url=base_url, model=model)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    command: str
    return_code: int
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        output = [f"exit_code: {self.return_code}"]
        if self.stdout:
            output.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            output.append(f"stderr:\n{self.stderr}")
        return "\n\n".join(output)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    stop_reason: str
    tool_calls: int
    requests: int
    usage: Mapping[str, int] = field(default_factory=dict)
    final_text: str = ""
    timed_out: bool = False


def bash_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run one shell command in the task workspace. Use it to inspect public "
                "task inputs, implement the requested artifact, and run public checks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_sec": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 180,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }


class DeepSeekClient:
    """Minimal synchronous HTTP client for DeepSeek Chat Completions."""

    def __init__(self, config: DeepSeekConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], Mapping[str, int]]:
        body = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "thinking": {"type": "disabled"},
            "temperature": 0,
        }
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.request_timeout_sec
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Do not include the response body: providers occasionally echo
            # request metadata, and the pilot event contract must stay secret-free.
            raise DeepSeekAgentError(
                f"DeepSeek API returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DeepSeekAgentError(f"DeepSeek API network error: {exc.reason}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeepSeekAgentError("DeepSeek API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DeepSeekAgentError("DeepSeek API response was not an object")
        if "error" in payload:
            raise DeepSeekAgentError("DeepSeek API returned an error response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise DeepSeekAgentError("DeepSeek API response contained no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise DeepSeekAgentError("DeepSeek API response contained no assistant message")
        usage = payload.get("usage")
        return message, _normalise_usage(usage)


class DeepSeekBashAgent:
    """A bounded, persistent chat session with one sandbox bash tool."""

    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        system_prompt: str,
        user_prompt: str,
        bash: Callable[[str, int], Awaitable[tuple[int, str, str]]],
        on_tool_result: Callable[[ToolResult], Awaitable[None]],
    ) -> None:
        self.config = config
        self.client = DeepSeekClient(config)
        self.bash = bash
        self.on_tool_result = on_tool_result
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @property
    def usage(self) -> Mapping[str, int]:
        return dict(self._usage)

    def add_user_feedback(self, feedback: str) -> None:
        self.messages.append({"role": "user", "content": feedback})

    async def run(self) -> AgentRunResult:
        tool_calls = 0
        requests = 0
        final_text = ""
        for _ in range(self.config.max_tool_rounds):
            message, usage = self.client.complete(self.messages, tools=[bash_tool_schema()])
            requests += 1
            _add_usage(self._usage, usage)
            assistant = _assistant_message(message)
            self.messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not isinstance(calls, list) or not calls:
                final_text = str(assistant.get("content") or "")
                return AgentRunResult(
                    stop_reason="stop",
                    tool_calls=tool_calls,
                    requests=requests,
                    usage=self.usage,
                    final_text=final_text,
                )
            for call in calls:
                result = await self._execute_tool_call(call)
                tool_calls += 1
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": _cap_text(result.text, 30000),
                    }
                )
                await self.on_tool_result(result)
        return AgentRunResult(
            stop_reason="tool_round_limit",
            tool_calls=tool_calls,
            requests=requests,
            usage=self.usage,
            final_text=final_text,
            timed_out=True,
        )

    async def _execute_tool_call(self, call: Any) -> ToolResult:
        if not isinstance(call, dict):
            return ToolResult("unknown", "", 2, "", "invalid tool call")
        call_id = str(call.get("id") or "unknown")
        function = call.get("function")
        if not isinstance(function, dict) or function.get("name") != "bash":
            return ToolResult(call_id, "", 2, "", "unsupported tool")
        raw_arguments = function.get("arguments", "{}")
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
        except json.JSONDecodeError:
            return ToolResult(call_id, "", 2, "", "invalid bash arguments JSON")
        if not isinstance(arguments, dict) or not isinstance(arguments.get("command"), str):
            return ToolResult(call_id, "", 2, "", "bash requires a string command")
        command = arguments["command"].strip()
        if not command:
            return ToolResult(call_id, command, 2, "", "bash command is empty")
        if _PROTECTED_PATH_PATTERN.search(command):
            return ToolResult(
                call_id,
                command,
                126,
                "",
                "policy denied: protected verifier/oracle path",
            )
        requested_timeout = arguments.get("timeout_sec", 60)
        try:
            timeout_sec = max(1, min(180, int(requested_timeout)))
        except (TypeError, ValueError):
            timeout_sec = 60
        return_code, stdout, stderr = await self.bash(command, timeout_sec)
        return ToolResult(call_id, command, return_code, stdout, stderr)


def _assistant_message(message: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    return result


def _normalise_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        raw = value.get(key)
        if isinstance(raw, int | float) and raw >= 0:
            result[key] = int(raw)
    return result


def _add_usage(target: dict[str, int], value: Mapping[str, int]) -> None:
    for key in target:
        target[key] += int(value.get(key, 0))


def _cap_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[output truncated by VISTA adapter]"


__all__ = [
    "AgentRunResult",
    "DeepSeekAgentError",
    "DeepSeekBashAgent",
    "DeepSeekConfig",
    "ToolResult",
    "bash_tool_schema",
    "redact_secret",
]
