"""Zero-model macOS readiness check for the travel-planning VISTA experiment."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from vista.runtime.opencode_local_backend import EXPECTED_VERSION, freeze_launcher
from experiments.formal.run_travel_planning_vista_group import (
    CONDITIONS,
    MODULE_IDS,
    REPLICATES,
    SCHEDULE,
    current_opencode_config,
)


REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
TASK = REPO / "vendor" / "skillsbench" / "tasks" / "travel-planning"
VISTA = REPO / "skills" / "pd-vista" / "task-007-travel-planning"


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    checks: dict[str, object] = {
        "platform_is_macos": sys.platform == "darwin",
        "python_version": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "repo_root": str(REPO),
        "repo_root_exists": REPO.is_dir(),
        "packages": {
            name: package_version(name)
            for name in ("PyYAML", "pytest", "pandas", "numpy", "requests")
        },
    }
    required_paths = (
        TASK / "task.md",
        TASK / "environment" / "data",
        TASK / "environment" / "skills",
        TASK / "verifier" / "test_outputs.py",
        VISTA / "SKILL.md",
        VISTA / "manifest.yaml",
        REPO / "experiments" / "formal" / "run_travel_planning_vista_group.py",
        *(TASK / "environment" / "skills" / module / "SKILL.md" for module in MODULE_IDS),
    )
    checks["required_paths"] = {str(path.relative_to(REPO)): path.exists() for path in required_paths}

    try:
        config_path, model, api_key_env = current_opencode_config()
        key_present = bool(os.environ.get(api_key_env)) if api_key_env else None
        checks["opencode_config"] = {
            "ready": api_key_env is None or key_present,
            "path": str(config_path),
            "model": model,
            "api_key_environment_variable": api_key_env,
            "api_key_present": key_present,
            "credential_source": "environment" if api_key_env else "opencode_auth_store",
        }
    except Exception as exc:
        checks["opencode_config"] = {"ready": False, "error": str(exc)}

    checks["experiment_matrix"] = {
        "conditions": list(CONDITIONS),
        "replicates": list(REPLICATES),
        "planned_trials": len(SCHEDULE),
        "unique_cells": len(set(SCHEDULE)),
        "ready": len(SCHEDULE) == 9 and len(set(SCHEDULE)) == 9,
    }

    try:
        evidence = freeze_launcher()
        checks["opencode"] = {
            "ready": True,
            "version": evidence.version,
            "expected_version": EXPECTED_VERSION,
            "launcher": evidence.launcher,
            "binary_sha256": evidence.binary_sha256,
        }
    except Exception as exc:
        checks["opencode"] = {"ready": False, "error": str(exc)}

    try:
        with tempfile.TemporaryDirectory(prefix="vista-symlink-check-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            checks["directory_symlink_supported"] = link.is_dir()
    except OSError as exc:
        checks["directory_symlink_supported"] = False
        checks["directory_symlink_error"] = str(exc)

    verifier_env = os.environ.copy()
    verifier_env["PYTHONUTF8"] = "1"
    verifier_env["PYTHONIOENCODING"] = "utf-8"
    verifier = TASK / "verifier" / "test_outputs.py"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(verifier),
         f"--confcutdir={verifier.parent}"],
        cwd=verifier.parent,
        env=verifier_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    verifier_output = (completed.stdout or "") + (completed.stderr or "")
    checks["verifier"] = {
        "ready": completed.returncode == 0 and "10 tests collected" in verifier_output,
        "return_code": completed.returncode,
        "ten_tests_collected": "10 tests collected" in verifier_output,
    }

    package_ready = all(checks["packages"].values())
    paths_ready = all(checks["required_paths"].values())
    opencode_ready = bool(checks["opencode"].get("ready"))
    config_ready = bool(checks["opencode_config"].get("ready"))
    matrix_ready = bool(checks["experiment_matrix"].get("ready"))
    checks["ready_for_health_check"] = all((
        checks["platform_is_macos"], checks["python_supported"], package_ready,
        paths_ready, opencode_ready, checks["directory_symlink_supported"],
        checks["verifier"]["ready"], config_ready, matrix_ready,
    ))
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ready_for_health_check"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
