"""One-call, excluded provider-availability diagnostic for task-004."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from experiments.formal.run_task004_local_group import (
    db_usage,
    event_summary,
    link_opencode_auth_store,
    parse_events,
)
from experiments.formal.run_travel_planning_vista_group import current_opencode_config
from vista.runtime.opencode_local_backend import command_for_turn, freeze_launcher


RUN_ID = os.environ.get(
    "VISTA_PROVIDER_DIAGNOSTIC_ID",
    "formal-task004-local-3x3-minimaxm3-20260823-03-provider-diagnostic",
)
ROOT = Path(os.environ.get("VISTA_EXPERIMENT_ROOT", f"/private/tmp/vista-{RUN_ID}")).resolve()
AUDIT = Path(__file__).resolve().parents[2] / "outputs" / f"{RUN_ID}-audit"


def main() -> int:
    if ROOT.exists():
        raise RuntimeError(f"refusing to reuse diagnostic root: {ROOT}")
    workspace = ROOT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config_path, requested_model, key_env = current_opencode_config()
    launcher = freeze_launcher()
    link_opencode_auth_store(ROOT)
    env = os.environ.copy()
    env["OPENCODE_CONFIG"] = str(config_path)
    env.pop("OPENCODE_CONFIG_DIR", None)
    env["XDG_CONFIG_HOME"] = str(ROOT / "opencode-state" / "config")
    env["XDG_DATA_HOME"] = str(ROOT / "opencode-state" / "data")
    env["XDG_CACHE_HOME"] = str(ROOT / "opencode-state" / "cache")
    command = command_for_turn(
        workspace=workspace,
        prompt="Provider availability diagnostic only. Work only in this empty directory. Do not create or edit files. Reply with READY if the configured OpenCode provider is reachable.",
        session_id=None,
        model=requested_model,
    )
    try:
        completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(command, 124, stdout=exc.stdout or "", stderr=exc.stderr or "")
        timed_out = True
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    summary = event_summary(parse_events(stdout), ())
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "turn-1.jsonl").write_text(stdout, encoding="utf-8")
    (AUDIT / "turn-1.stderr.txt").write_text(stderr, encoding="utf-8")
    record = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "batch_type": "provider_availability_diagnostic",
        "excluded_from_formal_statistics": True,
        "requested_provider_model": requested_model,
        "config_path": str(config_path),
        "api_key_environment_variable": key_env,
        "launcher": {"path": launcher.launcher, "version": launcher.version, "binary_sha256": launcher.binary_sha256},
        "return_code": completed.returncode,
        "timed_out": timed_out,
        "event_summary": summary,
        "stdout_tail": stdout[-3000:],
        "stderr_tail": stderr[-3000:],
        "opencode_db": db_usage(str(workspace)),
        "docker_used": False,
        "skillbench_execution_route_used": False,
        "status": "provider_reachable" if completed.returncode == 0 and not summary["errors"] else "provider_unavailable",
    }
    (AUDIT / "diagnostic.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": RUN_ID, "status": record["status"], "requests": summary["requests"], "errors": summary["errors"]}, ensure_ascii=False))
    return 0 if record["status"] == "provider_reachable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
