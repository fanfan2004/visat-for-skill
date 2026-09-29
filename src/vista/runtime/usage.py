"""Trial-scoped additive usage accounting."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class UsageAccumulator:
    requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(
        self,
        *,
        requests: int = 0,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> None:
        self.requests += max(0, int(requests or 0))
        self.tool_calls += max(0, int(tool_calls or 0))
        self.input_tokens += max(0, int(input_tokens or 0))
        self.output_tokens += max(0, int(output_tokens or 0))
        if total_tokens is not None and not (input_tokens or output_tokens):
            # Some adapters expose only a per-attempt total.  Add it without
            # ever taking max() across attempts; split counters remain zero.
            self.output_tokens += max(0, int(total_tokens))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def snapshot(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

    def add_result(self, result: Any) -> None:
        usage: Mapping[str, Any] = getattr(result, "usage", {}) or {}
        self.add(
            requests=getattr(result, "requests", 0),
            tool_calls=getattr(result, "tool_calls", 0),
            input_tokens=_as_int(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
            output_tokens=_as_int(
                usage.get("completion_tokens", usage.get("output_tokens", 0))
            ),
            total_tokens=(
                _as_int(usage.get("total_tokens"))
                if usage.get("total_tokens") is not None
                else None
            ),
        )


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = ["UsageAccumulator"]
