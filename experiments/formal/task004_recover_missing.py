"""Recover only task-004 cells left without a formal result after 429 quarantine."""

from __future__ import annotations

import json
import os

from experiments.formal.run_task004_local_group import (
    RUN_PREFIX,
    VERIFIER_PYTHON,
    current_opencode_config,
    load_latest_result,
    run_replicate,
)


MISSING = ((3, "pd-vista"), (3, "noskill"), (3, "native"))
ATTEMPT = int(os.environ.get("VISTA_RECOVERY_ATTEMPT", "5"))


def main() -> int:
    config_path, requested_model, key_env = current_opencode_config()
    values = []
    for replicate, condition in MISSING:
        existing = load_latest_result(condition, replicate)
        if existing is not None and existing.get("final_verifier_pass") and not str(existing.get("status", "")).startswith("quarantined_"):
            values.append({
                "condition": condition,
                "replicate_id": replicate,
                "attempt": existing.get("attempt"),
                "status": "existing_passed",
                "stop_reason": existing.get("stop_reason"),
                "final_verifier_pass": True,
                "session_id": existing.get("session_id"),
            })
            continue
        value = run_replicate(condition, replicate, ATTEMPT, config_path=config_path, requested_model=requested_model)
        values.append({
            "condition": condition,
            "replicate_id": replicate,
            "attempt": ATTEMPT,
            "status": value.get("status"),
            "stop_reason": value.get("stop_reason"),
            "final_verifier_pass": value.get("final_verifier_pass"),
            "session_id": value.get("session_id"),
        })
    print(json.dumps({"run_id": RUN_PREFIX, "recovery": values}, ensure_ascii=False))
    return 0 if all(item["final_verifier_pass"] for item in values) else 1


if __name__ == "__main__":
    raise SystemExit(main())
