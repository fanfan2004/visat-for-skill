from __future__ import annotations

import pytest

from vista.audit import cap_public, redact_secret


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "DEEPSEEK_API_KEY=super-secret-value",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN PRIVATE KEY-----private-material-----END PRIVATE KEY-----",
    ],
)
def test_redacts_runtime_secrets_through_one_audit_policy(sensitive_value: str) -> None:
    result = redact_secret(sensitive_value)

    assert result is not None
    assert "super-secret-value" not in result
    assert "abcdefghijklmnopqrstuvwxyz" not in result
    assert "private-material" not in result
    assert "<redacted-secret>" in result


def test_caps_only_after_redacting_public_output() -> None:
    value = "DEEPSEEK_API_KEY=super-secret-value " + "x" * 40

    result = cap_public(value, limit=30)

    assert result is not None
    assert "super-secret-value" not in result
    assert result.endswith("\n[output truncated]")
