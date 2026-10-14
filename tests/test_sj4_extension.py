"""Extension regression checks without model calls or benchmark solver runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import run_sj4_zcode_group as runner
from sj4_extension_tasks import TASKS
from sj4_resource_guard import configure, slots


@pytest.mark.parametrize("condition", ["noskill-multi", "pd-m", "vista"])
def test_multiturn_controls_share_feedback_and_stop_budget(tmp_path, condition):
    paths = {name: tmp_path / name
             for name in ("base", "workspace", "verifier", "router_source", "storage")}
    for path in paths.values():
        path.mkdir()
    import shutil

    artifact = runner.ARTIFACT_ROOT / "task-014"
    shutil.copytree(artifact / "pd", paths["router_source"] / "pd")
    shutil.copy2(artifact / "manifest.yaml", paths["router_source"] / "manifest.yaml")
    prompts = []

    def command_for_turn(**kw):
        prompts.append(kw)
        return ["mock-zcode"]

    counter = iter(range(1, 10))
    failure = ("FAILED hidden.py::TestOutputs::test_output_limits_and_offline_zeroes "
               "- AssertionError: generator SECRET expected 1234567, got 98765\n")
    with (
        patch.object(runner, "RESOURCE_POLICY", {}),
        patch.object(runner, "prepare_trial", return_value=paths),
        patch.object(runner, "result_root", return_value=tmp_path / "batch"),
        patch.object(runner, "command_for_turn", side_effect=command_for_turn),
        patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
            ["mock-zcode"], 0, "{}", "")),
        patch.object(runner, "parse_zcode_turn", side_effect=lambda _: {
            "sessionId": "same-session", "turnId": f"turn-{next(counter)}"}),
        patch.object(runner, "turn_records", return_value=[]),
        patch.object(runner, "run_verifier", return_value=(False, failure, False, 0.01)),
        patch.object(runner, "rollout_agent_seconds", return_value=1.0),
    ):
        result = runner.run_episode("task-014", condition, 1, model="glm53-flash")
    assert len(prompts) == 3  # first signature, then two unchanged signatures
    assert result["stop_reason"] == "stalled_after_feedback"
    for prompt in prompts[1:]:
        assert prompt["session_id"] == "same-session"
        assert "1234567" not in prompt["prompt"] and "SECRET" not in prompt["prompt"]
        assert "test_output_limits_and_offline_zeroes" in prompt["prompt"]
    if condition == "noskill-multi":
        assert result["disclosed_modules"] == []
        assert result["initially_available_modules"] == []
        assert not (paths["workspace"] / "skill").exists()
    if condition == "vista":
        assert result["disclosed_modules"] == ["unit-commitment-operating-rules"]
        assert prompts[0]["prompt"].endswith(runner.INITIAL_PROMPTS["noskill"])


def test_feedback_never_exposes_parametrized_expected_values():
    output = ("FAILED hidden.py::test_budget_values[F8-4639-FY2019 SpaceOps] - "
              "AssertionError: expected 4639, got 5000\n"
              "E assert actual == 4639\n")
    diagnostic = runner.extension_diagnostic("task-015", output)
    assert "test_budget_values" in diagnostic
    assert all(value not in diagnostic for value in ("F8", "4639", "5000", "FY2019"))
    assert runner.failure_layer_signatures("task-015", output) == ["xlsx_value_or_consistency"]


def test_verifier_timeout_is_infrastructure_and_decodes_partial_bytes(tmp_path):
    exc = subprocess.TimeoutExpired("pytest", 1, output=b"partial output", stderr=b"partial error")
    with patch.object(runner.subprocess, "run", side_effect=exc):
        passed, output, infra, _ = runner._run_verifier_command([], tmp_path, tmp_path, 1)
    assert not passed and infra and "partial output" in output


def test_host_slot_survives_holder_exception_and_rejects_quota_mismatch(tmp_path):
    configure(tmp_path, verifier_workers=1, solver_workers=2, numeric_threads=1)
    with pytest.raises(ValueError, match="resource limits differ"):
        configure(tmp_path, verifier_workers=2, solver_workers=2, numeric_threads=1)
    with pytest.raises(RuntimeError):
        with slots(tmp_path, "test", 1):
            raise RuntimeError("simulated holder failure")
    with slots(tmp_path, "test", 1) as waited:
        assert waited < 1


def test_slots_serialize_independent_processes(tmp_path):
    script = """
import json,sys,time
from pathlib import Path
from sj4_resource_guard import slots
with slots(Path(sys.argv[1]), 'cross-process', 1):
    started=time.time()
    time.sleep(0.3)
    print(json.dumps([started,time.time()]))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(runner.__file__).parent)
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True) for _ in range(3)]
    intervals = []
    for process in processes:
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        intervals.append(json.loads(out))
    intervals.sort()
    assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:]))


def test_python_hook_enforces_solver_policy_and_isolates_libreoffice(tmp_path):
    guard_site = Path(runner.__file__).parent / "sj4_guard_site"
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(guard_site) + os.pathsep + str(guard_site.parent),
                "VISTA_RESOURCE_GUARD_ENABLED": "1", "VISTA_RESOURCE_GUARD_ROOT": str(tmp_path),
                "VISTA_NUMERIC_THREADS": "1", "VISTA_SOLVER_WORKERS": "2",
                "VISTA_LO_PROFILE": str(tmp_path / "lo"),
                "VISTA_SOLVER_LOG": str(tmp_path / "solver.jsonl")})
    script = """
import os
from scipy.optimize import milp
assert milp.__module__ == 'sj4_resource_guard'
result=milp([1.0],integrality=[1], options={'threads':8,'time_limit':1})
assert result.success
assert os.path.expanduser('~/Library/Application Support/LibreOffice/4/user/basic/Standard') == os.environ['VISTA_LO_PROFILE']+'/user/basic/Standard'
print('ok')
"""
    completed = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "solver.jsonl").is_file()


def test_artifact_evidence_matches_packaged_files():
    # No local vendor checkout is needed: test artifact and copied support hashes.
    import hashlib

    for task_id in TASKS:
        root = runner.ARTIFACT_ROOT / task_id
        report = json.loads((root / "construction-evidence.json").read_text(encoding="utf-8"))
        for name, digest in report["artifact_files"].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        assert len((root / "pd/SKILL.md").read_text(encoding="utf-8").splitlines()) <= 150
    root = runner.ARTIFACT_ROOT / "task-015"
    report = json.loads((root / "construction-evidence.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((root / "pd/xlsx/recalc.py").read_bytes()).hexdigest() == report["source_files"]["xlsx/recalc.py"]


@pytest.mark.parametrize("condition", ["noskill-multi", "pd-m"])
def test_trial_materialization_uses_pinned_inputs_and_correct_skill_availability(tmp_path, condition):
    with (patch.object(runner, "LOCAL_ROOT", tmp_path / "cache"),
          patch.object(runner, "result_root", return_value=tmp_path / "batch")):
        paths = runner.prepare_trial("task-014", condition, 1)
    workspace = paths["workspace"]
    source, _ = runner.task_paths("task-014")
    assert (workspace / "network.json").read_bytes() == (source / "environment/network.json").read_bytes()
    assert "/root/" not in (workspace / "task.md").read_text(encoding="utf-8")
    assert not (workspace / "test_outputs_local.py").exists()
    assert (workspace / "skill").exists() == (condition == "pd-m")
    if condition == "pd-m":
        assert len(list((workspace / "skill/references").glob("*.md"))) == 3


def test_resume_archives_partial_workspace_verifier_and_output_together(tmp_path):
    cache = tmp_path / "cache"
    batch = tmp_path / "batch"
    name = "batch-noskill-multi-replicate-01"
    base = cache / name
    verifier = cache / ".verifiers" / name
    raw = batch / "noskill-multi/replicate-01"
    for path in (base, verifier, raw):
        path.mkdir(parents=True)
    # Interrupted before any headless receipt: this still must not reuse the workspace.
    (base / "input.json").write_text("partial")
    (verifier / "verifier-turn-6.txt").write_text("old hidden failure")
    (raw / "prompt-turn-1.txt").write_text("partial prompt")
    with (patch.object(runner, "LOCAL_ROOT", cache),
          patch.object(runner, "result_root", return_value=batch)):
        runner.archive_partial_base("task-014", "noskill-multi", 1)
    assert not base.exists() and not verifier.exists() and not raw.exists()
    assert len(list(cache.glob(name + "-abandoned-*/input.json"))) == 1
    assert len(list((cache / ".verifiers").glob(name + "-abandoned-*/verifier-turn-6.txt"))) == 1
    assert len(list((batch / "_interrupted").glob("*/prompt-turn-1.txt"))) == 1
