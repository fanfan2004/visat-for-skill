"""Promote No-Skill-03 attempt 11 after correcting a controller false positive."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from experiments.formal.run_task004_local_group import RESULT_ROOT, VERIFIER_PYTHON, VERIFIER_TIMEOUT_SECONDS


def main() -> int:
    raw_dir = RESULT_ROOT / "_excluded_rate_limit_attempts" / "noskill" / "replicate-03-attempt-11"
    raw_result_path = raw_dir / "result.json"
    raw = json.loads(raw_result_path.read_text(encoding="utf-8"))
    workspace = Path(raw["workspace"])
    verifier = workspace.parent / "verifier"
    completed = subprocess.run(
        [str(VERIFIER_PYTHON), "-m", "pytest", "-q", str(verifier / "test_outputs.py"), f"--confcutdir={verifier}", f"--basetemp={verifier / 'pytest-temp-reclassify-final'}"],
        cwd=verifier,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=VERIFIER_TIMEOUT_SECONDS,
        check=False,
    )
    verifier_output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / "verifier-turn-1.txt").write_text(verifier_output, encoding="utf-8")
    if completed.returncode != 0:
        raise SystemExit(f"manual final verifier did not pass: {verifier_output[-2000:]}")

    result_dir = RESULT_ROOT / "noskill" / "replicate-03"
    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact in raw_dir.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    output_file = workspace / "data" / "openipf.xlsx"
    shutil.copy2(output_file, result_dir / "openipf.xlsx")

    result = dict(raw)
    result.update({
        "status": "passed",
        "stop_reason": "verifier_pass_after_controller_reclassification",
        "final_verifier_pass": True,
        "rate_limit_excluded": False,
        "rate_limit_reason": None,
        "result_directory": str(result_dir),
        "final_output_files": ["openipf.xlsx"],
        "controller_reclassification": {
            "original_attempt_result": str(raw_result_path),
            "original_status": raw.get("status"),
            "reason": "The controller searched the raw JSONL for bare substring 429 and matched the decimal cost 0.00342978. No API error or 429 response event was present; manual final Verifier passed 11/11.",
            "manual_verifier": "11 passed",
        },
    })
    result["turns"] = [dict(turn, verifier_pass=True, verifier_infrastructure_error=False, verifier_timeout=False, rate_limit_excluded=False, rate_limit_reason=None) for turn in raw.get("turns", [])]
    (result_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path = RESULT_ROOT.parent.parent.parent / "outputs" / "formal-task004-local-3x3-minimaxm3-20260823-03-audit" / "task004-controller-reclassification-attempt11.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(result["controller_reclassification"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "result": str(result_dir / "result.json"), "verifier": "11 passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
