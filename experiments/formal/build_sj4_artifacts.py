"""Build the sj4 flat / single-file / pd artifacts with the glm53-flash construction model.

sj4 设计第四节的构造链（manual: reports/experiment-sj4-protocol-prompt.md）：
- flat:   modules bundle --flatten-source--> 每模块一个压平 SKILL.md
- single: modules bundle --single-file--> 任务级单文件 SKILL.md（全部模块合并）
- pd:     single-file --progressive-disclosure--> 入口 SKILL.md + references/（按原模块组织）

构造模型 glm53-flash（用户确认代跑，构造质量作为混淆源如实记录）。
每个构造步骤由一个 headless ZCode 会话完成；校验用 SkillJuror 的
behavior-unit-diff.mjs / pd-path-hygiene.mjs（ELECTRON_RUN_AS_NODE 电子二进制当 node 跑），
构造 agent 在会话内自己跑校验并修复到通过；控制器再复核一次。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from vista.runtime.zcode_local_backend import (
    command_for_turn,
    current_zcode_model_config,
    freeze_launcher,
    model_io_rollout_path,
    turn_environment,
)

REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
VENDOR_TASKS = REPO / "vendor" / "skillsbench" / "tasks"
VENDOR_TRANSFORMS = REPO / "vendor" / "skill-juror" / "skill-for-skill" / "transform-skills"
VENDOR_SCRIPTS = REPO / "vendor" / "skill-juror" / "scripts"
CONSTRUCTION_ROOT = Path(
    os.environ.get("VISTA_CONSTRUCTION_ROOT",
                   Path.home() / "Library" / "Caches" / "VistaExperiments" / ".construction")
).expanduser().resolve()
ARTIFACT_ROOT = REPO / "experiments" / "artifacts" / "sj4"
MODEL_TURN_TIMEOUT_SECONDS = 1800
CONSTRUCTION_ATTEMPTS = 4

# electron 二进制当 node 跑校验脚本（本机无独立 node）
NODE = "/Applications/ZCode.app/Contents/MacOS/ZCode"
NODE_ENV = {"ELECTRON_RUN_AS_NODE": "1"}

TASKS: dict[str, dict[str, str]] = {
    "task-011": {"benchmark": "sec-financial-report"},
    "task-012": {"benchmark": "invoice-fraud-detection"},
}

# pd 参考文件按原模块组织，路由把失败层签名映射到参考文件（与旧 pd-vista manifest 同源）
SIGNATURE_MODULES: dict[str, dict[str, dict[str, list[str]]]] = {
    "task-011": {
        "13f-analyzer": {"triggers": ["13f_value_mismatch", "13f_missing_output"]},
        "fuzzy-name-search": {"triggers": ["13f_name_list_mismatch"]},
    },
    "task-012": {
        "fuzzy-match": {"triggers": ["fraud_field_mismatch"]},
        "pdf": {"triggers": ["fraud_missed", "missing_output_file"]},
        "xlsx": {"triggers": ["fraud_false_positive"]},
    },
}

TRANSFORMS = {
    "flat": {
        "instructions": "transform-flatten-source.md",
        "source": "source-skills",
        "output": "output/flat",
        "alias": "origin-flat",
        "prompt": (
            "Execute the flatten-source transformation described in "
            "./transform-flatten-source.md. Source skills directory: ./source-skills. "
            "Flatten-source bundle output root: ./output/flat (the flatten-source variant). "
            "Produce one output skill root per direct source skill root under ./source-skills, "
            "keeping the source skill root names exactly. In this environment node is available "
            "as: ELECTRON_RUN_AS_NODE=1 {node} <script> — the behavior diff tool is at "
            "./tools/behavior-unit-diff.mjs. After writing the output, run "
            "`ELECTRON_RUN_AS_NODE=1 {node} ./tools/behavior-unit-diff.mjs --source "
            "./source-skills --candidate ./output/flat --out ./diff-flat --fail-on-risk` "
            "and fix the output until the diff reports risk=high issues are eliminated. "
            "The final output must contain only SKILL.md inside each skill root."
        ),
        "validate": ("diff", "source-skills", "output/flat", "diff-flat"),
    },
    "single-file": {
        "instructions": "transform-single-file.md",
        "source": "source-skills",
        "output": "output/single-file",
        "alias": "baseline",
        "prompt": (
            "Execute the single-file transformation described in ./transform-single-file.md. "
            "Source skills directory: ./source-skills. Single-file output root: "
            "./output/single-file (the single-file variant): exactly one SKILL.md that merges "
            "all source skills into distinct capability sections. In this environment node is "
            "available as: ELECTRON_RUN_AS_NODE=1 {node} <script> — the behavior diff tool is "
            "at ./tools/behavior-unit-diff.mjs. After writing the output, run "
            "`ELECTRON_RUN_AS_NODE=1 {node} ./tools/behavior-unit-diff.mjs --source "
            "./source-skills --candidate ./output/single-file --out ./diff-single "
            "--fail-on-risk` and fix the output until risk=high issues are eliminated. "
            "The final output must contain only SKILL.md."
        ),
        "validate": ("diff", "source-skills", "output/single-file", "diff-single"),
    },
    "pd": {
        "instructions": "transform-pd.md",
        "source": "output/single-file",
        "output": "output/pd",
        "alias": "pd",
        "prompt": (
            "Execute the progressive-disclosure transformation described in "
            "./transform-pd.md. Source single-file directory: ./output/single-file. "
            "Progressive-disclosure output root: ./output/pd (the pd variant). "
            "In addition to the transform rules, impose this project-specific structure: "
            "the references/ directory must contain exactly one markdown file per source "
            "skill module, named after the source skill root (e.g. 13f-analyzer.md), so "
            "downstream failure-layer routing can map each module deterministically. "
            "Support files that commands or imports depend on must be recreated under the "
            "output root at their relative paths. In this environment node is available as: "
            "ELECTRON_RUN_AS_NODE=1 {node} <script> — the behavior diff tool is at "
            "./tools/behavior-unit-diff.mjs and the path hygiene tool at "
            "./tools/pd-path-hygiene.mjs. After writing the output, run "
            "`ELECTRON_RUN_AS_NODE=1 {node} ./tools/behavior-unit-diff.mjs --source "
            "./output/single-file --candidate ./output/pd --out ./diff-pd --fail-on-risk` "
            "and `ELECTRON_RUN_AS_NODE=1 {node} ./tools/pd-path-hygiene.mjs --root "
            "./output/pd --out ./hygiene-pd --fail-on-risk` and fix the output until both "
            "pass with no risk=high findings."
        ),
        "validate": ("diff", "output/single-file", "output/pd", "diff-pd"),
        "hygiene": ("output/pd", "hygiene-pd"),
    },
}


def clone_copy(src: Path, dst: Path) -> None:
    try:
        subprocess.run(["cp", "-cR", str(src), str(dst)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def node_command(script: Path, *args: str) -> list[str]:
    return [NODE, str(script), *args]


def run_validation(kind: str, source: Path, candidate: Path, out: Path) -> tuple[bool, str]:
    out.mkdir(parents=True, exist_ok=True)
    if kind == "diff":
        command = node_command(
            VENDOR_SCRIPTS / "behavior-unit-diff.mjs",
            "--source", str(source), "--candidate", str(candidate),
            "--out", str(out), "--fail-on-risk",
        )
    else:
        command = node_command(
            VENDOR_SCRIPTS / "pd-path-hygiene.mjs",
            "--root", str(candidate), "--out", str(out), "--fail-on-risk",
        )
    env = {**os.environ, **NODE_ENV}
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, check=False, env=env,
    )
    report = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, report


def run_construction_turn(ws: Path, prompt: str, session_id: str | None) -> dict:
    command = command_for_turn(workspace=ws, prompt=prompt, session_id=session_id)
    env = {**os.environ}
    env.update(turn_environment(ws))
    completed = subprocess.run(
        command, cwd=ws, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=MODEL_TURN_TIMEOUT_SECONDS, check=False,
    )
    (ws / "construction-last.jsonl").write_text(completed.stdout or "", encoding="utf-8")
    (ws / "construction-last.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
    text = (completed.stdout or "").strip()
    start = text.find("{")
    payload = json.loads(text[start:]) if start >= 0 else {}
    return {"session_id": payload.get("sessionId"), "return_code": completed.returncode}


def output_ok(step: str, ws: Path, task_id: str) -> bool:
    out = ws / TRANSFORMS[step]["output"]
    if not out.is_dir():
        return False
    if step == "flat":
        roots = [item for item in out.iterdir() if item.is_dir()]
        if not roots:
            return False
        return all(
            (root / "SKILL.md").is_file()
            and not [p for p in root.iterdir() if p.name != "SKILL.md"]
            for root in roots
        )
    if step == "single-file":
        return (out / "SKILL.md").is_file() and not [
            p for p in out.iterdir() if p.name != "SKILL.md"
        ]
    if step == "pd":
        return (out / "SKILL.md").is_file() and (out / "references").is_dir() and any(
            (out / "references").glob("*.md")
        )
    return False


def build_step(task_id: str, step: str) -> dict:
    spec = TRANSFORMS[step]
    ws = CONSTRUCTION_ROOT / "sj4" / task_id
    prompt = spec["prompt"].format(node=NODE)
    session_id: str | None = None
    attempts: list[dict] = []
    for attempt in range(1, CONSTRUCTION_ATTEMPTS + 1):
        if attempt == 1:
            turn_prompt = prompt
        else:
            # 校验失败后续跑：把最新 diff/hygiene 报告给构造 agent 修
            reports = []
            for report_dir in ws.glob("diff-*"), ws.glob("hygiene-*"):
                for report in report_dir:
                    if report.is_dir() and (report / "report.md").is_file():
                        reports.append(str(report / "report.md"))
            fix_target = f"./{spec['output']}"
            turn_prompt = (
                f"The controller re-ran the validation for {fix_target} and it still "
                "reports risk findings. Read the latest reports at: "
                + (", ".join(reports) if reports else "the diff/hygiene output directories")
                + f". Repair {fix_target} in place following ./transform-{step}.md "
                "(do not start over from scratch), then re-run the same validation "
                "commands and fix until no risk=high findings remain."
            )
        turn = run_construction_turn(ws, turn_prompt, session_id)
        session_id = turn["session_id"] or session_id
        diff_ok, diff_report = run_validation(*step_validation_args(spec, ws))
        ok = output_ok(step, ws, task_id) and diff_ok
        hygiene_ok, hygiene_report = True, ""
        if ok and "hygiene" in spec:
            root_arg, out_arg = spec["hygiene"]
            hygiene_ok, hygiene_report = run_validation(
                "hygiene", ws, ws / root_arg, ws / out_arg
            )
            ok = ok and hygiene_ok
        attempts.append({
            "attempt": attempt, "turn": turn, "output_ok": output_ok(step, ws, task_id),
            "diff_ok": diff_ok, "hygiene_ok": hygiene_ok,
        })
        print(f"[{task_id}/{step}] attempt {attempt}: output_ok={output_ok(step, ws, task_id)} "
              f"diff_ok={diff_ok} hygiene_ok={hygiene_ok}", flush=True)
        (ws / f"validation-{step}.json").write_text(json.dumps({
            "diff_ok": diff_ok, "hygiene_ok": hygiene_ok,
            "diff_report": diff_report[-3000:], "hygiene_report": hygiene_report[-3000:],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if ok:
            return {"ok": True, "attempts": attempts}
        time.sleep(5)
    return {"ok": False, "attempts": attempts}


def step_validation_args(spec: dict, ws: Path) -> tuple[str, Path, Path, Path]:
    kind, source, candidate, out = spec["validate"]
    return kind, ws / source, ws / candidate, ws / out


def build_task(task_id: str) -> dict:
    spec = TASKS[task_id]
    benchmark = spec["benchmark"]
    ws = CONSTRUCTION_ROOT / "sj4" / task_id
    ws.mkdir(parents=True, exist_ok=True)
    # 构造工作区：源 bundle（只读来源 clone）、transform 指令、校验工具
    if not (ws / "source-skills").exists():
        clone_copy(VENDOR_TASKS / benchmark / "environment" / "skills", ws / "source-skills")
    shutil.copy2(VENDOR_TRANSFORMS / "skill-for-skill-flatten-source" / "SKILL.md",
                 ws / "transform-flatten-source.md")
    shutil.copy2(VENDOR_TRANSFORMS / "skill-for-skill-single-file" / "SKILL.md",
                 ws / "transform-single-file.md")
    shutil.copy2(VENDOR_TRANSFORMS / "skill-for-skill-progressive-disclosure" / "SKILL.md",
                 ws / "transform-pd.md")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "tools").mkdir(exist_ok=True)
    shutil.copy2(VENDOR_SCRIPTS / "behavior-unit-diff.mjs", ws / "tools" / "behavior-unit-diff.mjs")
    shutil.copy2(VENDOR_SCRIPTS / "pd-path-hygiene.mjs", ws / "tools" / "pd-path-hygiene.mjs")

    results: dict[str, dict] = {}
    for step in ("flat", "single-file", "pd"):
        results[step] = build_step(task_id, step)
        if not results[step]["ok"]:
            return {"task_id": task_id, "ok": False, "steps": results}

    # 校验通过的工件拷进仓库（可提交、可复现）
    for step in ("flat", "single-file", "pd"):
        destination = ARTIFACT_ROOT / task_id / step
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone_copy(ws / TRANSFORMS[step]["output"], destination)

    # sj4 vista manifest：失败层签名 → pd 参考文件（与 pd 条件同源）
    references = sorted((ARTIFACT_ROOT / task_id / "pd" / "references").glob("*.md"))
    manifest = {
        "schema_version": 1,
        "construction_model": "glm53-flash",
        "source_content_equivalence": "behavior-unit-diff-low-risk",
        "modules": [
            {
                "id": ref.stem,
                "path": f"pd/references/{ref.name}",
                "triggers": SIGNATURE_MODULES[task_id].get(ref.stem, {}).get(
                    "triggers", ["unknown"]
                ),
            }
            for ref in references
        ],
    }
    import yaml
    manifest_path = ARTIFACT_ROOT / task_id / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")
    return {"task_id": task_id, "ok": True, "steps": results, "manifest": str(manifest_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), default=None,
                        help="build one task's artifacts (default: all)")
    args = parser.parse_args(argv)

    model_config = current_zcode_model_config()
    launcher = freeze_launcher()
    print(f"construction model: {model_config['provider_id']}/{model_config['model_id']} | "
          f"launcher {launcher.version}", flush=True)

    tasks = [args.task] if args.task else sorted(TASKS)
    report: dict[str, dict] = {}
    for task_id in tasks:
        print(f"=== building {task_id} ===", flush=True)
        report[task_id] = build_task(task_id)
        if not report[task_id]["ok"]:
            print(f"{task_id} construction failed validation", flush=True)
            return 1
    summary_path = ARTIFACT_ROOT / "construction-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(),
        "construction_model": f"{model_config['provider_id']}/{model_config['model_id']}",
        "tasks": report,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
