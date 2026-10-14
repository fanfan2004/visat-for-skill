"""The PD multi-turn control receives feedback without runtime skill disclosure."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import run_sj4_zcode_group as runner


def test_pd_m_resumes_with_masked_feedback_and_no_disclosure(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    paths = {
        name: tmp_path / name
        for name in ("base", "workspace", "verifier", "router_source", "storage")
    }
    for path in paths.values():
        path.mkdir()
    prompts: list[tuple[str, str | None]] = []
    turns = iter(("turn-1", "turn-2"))

    def command_for_turn(*, workspace: Path, prompt: str,
                         session_id: str | None) -> list[str]:
        prompts.append((prompt, session_id))
        return ["mock-zcode"]

    def parse_turn(_: str) -> dict[str, str]:
        return {"sessionId": "same-session", "turnId": next(turns)}

    with (
        patch.object(runner, "prepare_trial", return_value=paths),
        patch.object(runner, "result_root", return_value=batch),
        patch.object(runner, "command_for_turn", side_effect=command_for_turn),
        patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
            ["mock-zcode"], 0, "{}", "")),
        patch.object(runner, "parse_zcode_turn", side_effect=parse_turn),
        patch.object(runner, "turn_records", return_value=[]),
        patch.object(runner, "run_verifier", side_effect=[
            (False, "E   assert 3457 == 2451", False, 0.1),
            (True, "2 passed", False, 0.1),
        ]),
        patch.object(runner, "rollout_agent_seconds", return_value=1.0),
    ):
        result = runner.run_episode("task-011", "pd-m", 1, model="glm53-flash")

    assert len(prompts) == 2
    assert prompts[0][0].endswith(runner.INITIAL_PROMPTS["pd"])
    assert prompts[0][1] is None
    assert prompts[1][1] == "same-session"
    assert "<actual>" in prompts[1][0] and "<expected>" in prompts[1][0]
    assert "2451" not in prompts[1][0]
    assert result["initially_available_modules"] == ["skill/SKILL.md"]
    assert result["disclosed_modules"] == []
    assert result["vista_metrics"]["successful_disclosures"] == 0
    assert result["vista_metrics"]["first_turn_pass"] is False
    assert result["final_verifier_pass"] is True
    assert len(result["turns"]) == 2
