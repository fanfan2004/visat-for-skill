"""Run the task-002 ``pd-self`` condition: PD-format skill, self-served module reads.

The agent receives the PD-VISTA root skill (``VISTA.md``) plus all guidance modules
upfront under ``./modules/`` and decides on its own which modules to read; the
controller runs no feedback-driven disclosure routing. Results go to a separate
result root so the completed three-condition batch stays untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import run_task008_010_zcode_group as base


BASE_PROTOCOL_ROOT_KEY = "base_batch_reference"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="task-002")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--base-root", default=None,
                        help="result root of the completed three-condition batch to reference")
    args = parser.parse_args(argv)

    task_id = args.task
    os.environ["VISTA_RUN_PREFIX"] = (
        f"formal-{task_id}-zcode-local-3x3-glm53-pdself-{datetime.now().strftime('%Y%m%d-%H')}"
    )
    root = base.result_root(task_id)

    model_config = base.current_zcode_model_config()
    launcher = None
    last_error = None
    for attempt in range(3):
        try:
            launcher = base.freeze_launcher()
            break
        except base.LocalGateError as exc:
            last_error = exc
            print(f"launcher gate attempt {attempt + 1} failed: {exc}", flush=True)
            time.sleep(15)
    if launcher is None:
        raise SystemExit(f"launcher gate failed after retries: {last_error}")
    print(f"zcode launcher ok: {launcher.version} | model: {model_config['model_id']}", flush=True)

    readiness = base.dependency_readiness(task_id)
    if not readiness["ready"]:
        raise RuntimeError(f"{task_id} local dependencies are not ready: {json.dumps(readiness, ensure_ascii=False)}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pending = []
    results = []
    for replicate in (1, 2, 3):
        existing = root / "pd-self" / f"replicate-{replicate:02d}" / "result.json"
        if existing.is_file():
            results.append(json.loads(existing.read_text(encoding="utf-8")))
        else:
            pending.append(replicate)

    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(base.run_replicate, task_id, "pd-self", replicate,
                            model=model_config["model_id"]): replicate
                for replicate in pending
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"trial done: {result['run_id']} status={result['status']}", flush=True)
    else:
        for replicate in pending:
            result = base.run_replicate(task_id, "pd-self", replicate,
                                        model=model_config["model_id"])
            results.append(result)

    usage_keys = ("requests", "input_tokens", "output_tokens", "cache_read_tokens")
    pdself = [result for result in results if result["condition"] == "pd-self"]
    condition_summaries = {
        "pd-self": {
            "planned_trials": 3, "completed_trials": len(pdself),
            "verifier_passes": sum(result["final_verifier_pass"] for result in pdself),
            "usage": {
                key: sum(result["usage"].get(key, 0) for result in pdself) for key in usage_keys
            },
        }
    }
    summary = {
        "schema_version": 1,
        "protocol": f"{task_id}-zcode-local-glm53-pd-self-3x3",
        "task_id": task_id,
        "benchmark_task_id": base.TASKS[task_id]["benchmark"],
        "condition_semantics": (
            "pd-self = PD-format root skill (VISTA.md) plus all guidance modules visible "
            "upfront; the agent self-selects which modules to read; no feedback-driven "
            "disclosure routing. Public verifier diagnostics in the repair loop are "
            "identical to all other conditions."
        ),
        "requested_provider_model": model_config["model_id"],
        "backend": "zcode-local-minimal-dependencies",
        "condition_summaries": condition_summaries,
        BASE_PROTOCOL_ROOT_KEY: args.base_root,
        "results": [
            {
                "condition": result["condition"], "replicate_id": result["replicate_id"],
                "status": result["status"], "final_verifier_pass": result["final_verifier_pass"],
                "session_id": result["session_id"], "usage": result["usage"],
                "initially_available_modules": result["initially_available_modules"],
                "disclosed_modules": result["disclosed_modules"],
                "accessed_modules_after_disclosure": result["accessed_modules_after_disclosure"],
                "vista_metrics": result["vista_metrics"],
            }
            for result in results
        ],
    }
    if args.base_root:
        base_summary_path = Path(args.base_root) / "group-summary.json"
        if base_summary_path.is_file():
            summary["base_batch_condition_summaries"] = json.loads(
                base_summary_path.read_text(encoding="utf-8")
            )["condition_summaries"]
    (root / "group-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if len(pdself) == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
