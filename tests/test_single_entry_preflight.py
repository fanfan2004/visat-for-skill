from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "formal" / "build_task_images.ps1"
FORMAL_RUNNER = Path(__file__).resolve().parents[1] / "experiments" / "pilot" / "run.ps1"
FORMAL_GROUP_RUNNER = (
    Path(__file__).resolve().parents[1] / "experiments" / "formal" / "run_task_groups.ps1"
)
MOCK_FORMAL_RUNNER = Path(__file__).resolve().parent / "fixtures" / "mock_formal_runner.ps1"


def powershell_path(path: Path) -> str:
    if shutil.which("wslpath") is None:
        return str(path)
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def test_single_entry_script_is_project_scoped_and_model_free() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"D:\\vista"' in text
    assert '"1.18.13"' in text
    assert (
        "https://github.com/anomalyco/opencode/releases/download/"
        "v1.18.13/opencode-linux-x64.tar.gz"
    ) in text
    assert "8d500b20fed2d26e537e221895b1a575476571b4f0089bb29fb13eeb8eb9e937" in text
    assert "https://github.com/anomalyco/opencode/releases/expanded_assets/v1.18.13" in text
    assert "https://github.com/anomalyco/opencode/attestations/38785922/download" in text
    assert '"--opencode-expected-source-sha256"' in text
    assert 'source_digest_kind = "archive_sha256"' in text
    for task_id in ("task-001", "task-002", "task-003", "task-004", "task-005", "task-006"):
        assert f'"{task_id}"' in text
    assert "model_calls = 0" in text
    assert "formal_54_trial_started = $false" in text
    assert "Refusing to overwrite" in text
    assert '"cp"' in text
    assert '"ps"' in text
    assert "Invoke-WebRequest" in text

    forbidden = (
        "docker stop",
        "docker rm",
        "docker kill",
        "Remove-Item -Recurse",
        "Remove-Item -Path",
        "rm -rf",
        "opencode run",
        "run.ps1",
        "-Backend",
    )
    for marker in forbidden:
        assert marker not in text


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def test_single_entry_script_has_no_powershell_parse_errors() -> None:
    command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{powershell_path(SCRIPT)}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=SCRIPT.parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def run_formal_dry_run(*task_ids: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            powershell_path(FORMAL_RUNNER),
            "-RunMode",
            "formal",
            "-ConfirmFormalRun",
            "-RunId",
            "formal-test-dry-run",
            "-Backend",
            "opencode-benchflow",
            "-TaskId",
            ",".join(task_ids),
            "-DryRun",
        ],
        cwd=FORMAL_RUNNER.parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def test_formal_runner_accepts_one_frozen_task_in_dry_run() -> None:
    result = run_formal_dry_run("task-002")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Formal matrix: 1 task / 9 trials" in result.stdout
    assert result.stdout.count("--task-id task-002") == 9
    assert "--task-id task-001" not in result.stdout
    assert "--image-manifest-path" in result.stdout
    assert "opencode-build-formal-sbt-fix-20260810-image-registry.json" in result.stdout
    positions = [result.stdout.index(f"--seed {seed}") for seed in range(20260814, 20260823)]
    assert positions == sorted(positions)
    assert "DryRun complete" in result.stdout


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def test_formal_runner_rejects_unknown_or_multiple_tasks() -> None:
    unknown = run_formal_dry_run("task-999")
    multiple = run_formal_dry_run("task-001", "task-002")

    assert unknown.returncode != 0
    assert "must be one of the frozen schedule tasks" in unknown.stderr
    assert multiple.returncode != 0
    assert "requires exactly one -TaskId" in multiple.stderr


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def test_formal_group_runner_continues_after_a_failed_group() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            powershell_path(FORMAL_GROUP_RUNNER),
            "-RunnerPath",
            powershell_path(MOCK_FORMAL_RUNNER),
            "-RunIdPrefix",
            "formal-test-groups",
        ],
        cwd=FORMAL_GROUP_RUNNER.parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "MOCK_STARTED:task-003" in result.stdout
    assert "task-002: FAILED" in result.stdout
    assert "task-006: SUCCESS" in result.stdout


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell is required")
def test_formal_scripts_have_no_powershell_parse_errors() -> None:
    for script in (FORMAL_RUNNER, FORMAL_GROUP_RUNNER):
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{powershell_path(script)}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            cwd=script.parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_formal_runner_continues_only_complete_task_failures() -> None:
    text = FORMAL_RUNNER.read_text(encoding="utf-8")

    assert '$trialExitCode -eq 1 -and $terminalStatus -eq "fail"' in text
    assert "valid task failure recorded; continuing formal matrix" in text
    assert 'if ($trialExitCode -ne 0)' in text
    assert "Formal run forbids -ResumeExisting" in text
