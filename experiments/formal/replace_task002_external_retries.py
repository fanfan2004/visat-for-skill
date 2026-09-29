"""Compose task-002's final audit by replacing only externally quarantined trials."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import experiments.formal.run_task002_local_group as controller


REPO = Path(__file__).resolve().parents[2]
ORIGINAL_ID = "formal-task002-local-3x3-minimaxm3-20260822-08"
RETRY_ID = "formal-task002-local-3x3-minimaxm3-20260823-11-retry-external"
FINAL_ID = "formal-task002-local-3x3-minimaxm3-20260823-11-replaced"
ORIGINAL = REPO / "data" / "raw" / ORIGINAL_ID
RETRY = REPO / "data" / "raw" / RETRY_ID
FINAL = REPO / "data" / "raw" / FINAL_ID
REPLACED = {("noskill", 3), ("native", 3), ("pd-vista", 3)}


def read_result(root: Path, condition: str, replicate: int) -> dict:
    return json.loads((root / condition / f"replicate-{replicate:02d}" / "result.json").read_text(encoding="utf-8"))


def main() -> int:
    if FINAL.exists():
        raise RuntimeError(f"refusing to overwrite {FINAL}")
    if not RETRY.is_dir():
        raise RuntimeError(f"missing retry results {RETRY}")
    shutil.copytree(ORIGINAL, FINAL)
    results = []
    for replicate, condition in controller.SCHEDULE:
        source = RETRY if (condition, replicate) in REPLACED else ORIGINAL
        if source is RETRY:
            target = FINAL / condition / f"replicate-{replicate:02d}"
            shutil.rmtree(target)
            shutil.copytree(source / condition / f"replicate-{replicate:02d}", target)
        results.append(read_result(source, condition, replicate))
    requested_model = json.loads((ORIGINAL / "group-summary.json").read_text(encoding="utf-8"))["requested_provider_model"]
    controller.RESULT_ROOT = FINAL
    audit = controller.write_posthoc_audit(results, requested_model)
    group = json.loads((ORIGINAL / "group-summary.json").read_text(encoding="utf-8"))
    group.update({
        "run_id": FINAL_ID,
        "completed_trials": len(results),
        "results": [{key: item[key] for key in ("condition", "replicate_id", "status", "final_verifier_pass", "session_id", "actual_model_ids", "actual_provider_ids", "usage", "initially_available_modules", "disclosed_modules", "accessed_modules_after_disclosure", "vista_metrics")} for item in results],
        "replacement_provenance": {
            "replaced_original_run": ORIGINAL_ID,
            "retry_run": RETRY_ID,
            "excluded_recovery_diagnostics": [
                "formal-task002-local-3x3-minimaxm3-20260822-09-retry-external",
                "formal-task002-local-3x3-minimaxm3-20260822-10-retry-external",
            ],
            "reason": "Three replicate-03 records were quarantined solely for retryable provider HTTP 429 rate limiting; rerun with fresh isolated workspace, OpenCode state, and session.",
            "replaced_trials": [{"condition": c, "replicate_id": r} for c, r in sorted(REPLACED)],
        },
    })
    summaries = {}
    for condition in controller.CONDITIONS:
        values = [item for item in results if item["condition"] == condition]
        summaries[condition] = {"planned_trials": 3, "completed_trials": len(values), "verifier_passes": sum(item["final_verifier_pass"] for item in values), "usage": {key: sum(item["usage"][key] for item in values) for key in ("requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens")}}
    group["condition_summaries"] = summaries
    (FINAL / "group-summary.json").write_text(json.dumps(group, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit["run_id"] = FINAL_ID
    audit["replacement_provenance"] = group["replacement_provenance"]
    (FINAL / "posthoc-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
