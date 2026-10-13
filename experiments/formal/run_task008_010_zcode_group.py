"""Run the ZCode extension tasks locally with ZCode in three conditions and three replicates.

Covers task-002 (enterprise-information-search), task-008 (adaptive-cruise-control),
task-009 (r2r-mpc-control), task-010 (econ-detrending-correlation), task-011
(sec-financial-report), task-012 (invoice-fraud-detection) and task-013
(financial-modeling-qa) with the ``glm53-flash`` ZCode backend.
Each replicate is one headless ZCode session driven by the same turn loop as
``run_task001_local_group.py``; the local verifier is a path-rewritten copy of
the SkillsBench ``test_outputs.py`` so every check stays byte-identical and
only the ``/root`` prefix differs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.runtime import DisclosureRouter, SkillManifest
from vista.runtime.zcode_local_backend import (
    LocalGateError,
    command_for_turn,
    current_zcode_model_config,
    freeze_launcher,
    model_io_rollout_path,
    parse_model_io_rollout,
    rollout_usage_summary,
    turn_environment,
)


REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
VENDOR_TASKS = REPO / "vendor" / "skillsbench" / "tasks"
TOOLS_VENV_BIN = REPO / ".venv" / "bin"
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv" / "bin" / "python")
).expanduser().absolute()
LOCAL_ROOT = Path(
    os.environ.get(
        "VISTA_EXPERIMENT_ROOT", Path.home() / "Library" / "Caches" / "VistaExperiments"
    )
).expanduser().resolve()
MAX_TURNS = 6
MODEL_TURN_TIMEOUT_SECONDS = 1800
VERIFIER_TIMEOUT_SECONDS = 900
CONDITIONS = ("noskill", "native", "pd-vista", "pd-self")
REPLICATES = (1, 2, 3, 4, 5)
DEFAULT_REPLICATES = (1, 2, 3)
EXTENDED_REPLICATES = (1, 2, 3, 4, 5)


def schedule_for(task_id: str) -> tuple[tuple[int, str], ...]:
    # 评审后：task-011/012 因验证器 bug 全部作废重跑，且 pd-vista/pd-self 需 ≥5 复现
    reps = EXTENDED_REPLICATES if task_id in {"task-011", "task-012"} else DEFAULT_REPLICATES
    return tuple((rep, condition) for rep in reps for condition in CONDITIONS)


# 失败层签名：从验证器 traceback 提取任务专属反馈类型，模块触发词按失败层区分，
# 不再依赖 8 个通用触发词的固定顺序披露（评审意见 1/三-1）
FAILURE_LAYER_RULES: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "task-011": (
        # q4 管理人名单（名称列表/精确比对层）
        (("q4_answer", ".lower() ==", "ans.lower"), "13f_name_list_mismatch"),
        # q1 数值偏差 / q2 q3 精确值（13F 聚合层）
        (("q1_answer", "q2_answer", "q3_answer"), "13f_value_mismatch"),
        # answers.json 缺失
        (("os.path.isfile", "no such file", "not found"), "13f_missing_output"),
    ),
    "task-012": (
        # 漏报 / 数量不符（发票内容抽取层）
        (("missed fraud", "number of fraud cases"), "fraud_missed"),
        # 误报（把干净发票标记为欺诈，供应商对照层）
        (("false positives",), "fraud_false_positive"),
        # 字段级不符（reason/vendor/iban/po/amount 模糊比对层）
        (("reason mismatch", "vendor name mismatch", "iban mismatch",
          "po number mismatch", "amount mismatch"), "fraud_field_mismatch"),
        # fraud_report.json 缺失
        (("os.path.exists", "no such file", "not found"), "missing_output_file"),
    ),
}


def failure_layer_signatures(task_id: str, verifier_output: str) -> list[str]:
    text = verifier_output.casefold()
    signatures: list[str] = []
    for keywords, signature in FAILURE_LAYER_RULES.get(task_id, ()):
        if any(keyword in text for keyword in keywords) and signature not in signatures:
            signatures.append(signature)
    return signatures

TASKS: dict[str, dict[str, Any]] = {
    "task-008": {
        "benchmark": "adaptive-cruise-control",
        "inputs": ["vehicle_params.yaml", "sensor_data.csv"],
        "output_files": [
            "pid_controller.py", "acc_system.py", "tuning_results.yaml",
            "simulation_results.csv", "acc_report.md",
        ],
    },
    "task-009": {
        "benchmark": "r2r-mpc-control",
        "inputs": ["r2r_simulator.py", "system_config.json"],
        "output_files": ["controller_params.json", "control_log.json"],
    },
    "task-010": {
        "benchmark": "econ-detrending-correlation",
        "inputs": ["CPI.xlsx", "ERP-2025-table10.xls", "ERP-2025-table12.xls"],
        "output_files": ["answer.txt"],
    },
    "task-002": {
        "benchmark": "enterprise-information-search",
        "inputs": ["DATA", "question.txt"],
        "output_files": ["answer.json"],
    },
    "task-011": {
        "benchmark": "sec-financial-report",
        "inputs": ["13f-2025-q2.zip", "13f-2025-q3.zip"],
        "output_files": ["answers.json"],
        "unzip": [
            {"zip": "13f-2025-q2.zip", "dest": ".", "move_top_to": "2025-q2"},
            {"zip": "13f-2025-q3.zip", "dest": "2025-q3"},
        ],
    },
    "task-012": {
        "benchmark": "invoice-fraud-detection",
        "inputs": ["invoices.pdf", "vendors.xlsx", "purchase_orders.csv"],
        "output_files": ["fraud_report.json"],
    },
    "task-013": {
        "benchmark": "financial-modeling-qa",
        "inputs": ["data.xlsx", "background.pdf"],
        "output_files": ["answer.txt"],
    },
}

COMMON_PROMPT = """Solve the isolated {benchmark} task in this working directory. \
Work only inside this directory and do not access parent or external directories. \
First read task.md and the supplied input files: {inputs}. \
Write the required output files declared in task.md to this directory: {outputs}. \
Use the local python interpreter for any simulation or analysis you need to run. \
Hidden verifier tests are run only by the controller after each turn and may return \
a cleaned public diagnostic for repair."""
INITIAL_PROMPTS = {
    "noskill": " No task Skill is available in this condition.",
    "native": " All original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
    "pd-vista": " Read VISTA.md. Original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose one module under ./modules/<module-id>/SKILL.md.",
    "pd-self": " Read VISTA.md. The original guidance modules are available under ./modules/<module-id>/SKILL.md; decide for yourself which modules to read and use. There is no disclosure step.",
}


def task_paths(task_id: str) -> tuple[Path, Path, Path, Path]:
    spec = TASKS[task_id]
    source = VENDOR_TASKS / spec["benchmark"]
    return (
        source,
        source / "environment",
        source / "environment" / "skills",
        REPO / "skills" / "pd-vista" / task_id,
    )


def result_root(task_id: str) -> Path:
    run_prefix = os.environ.get(
        "VISTA_RUN_PREFIX",
        f"formal-{task_id}-zcode-local-glm53-{datetime.now().strftime('%Y%m%d-%H')}",
    )
    return REPO / "data" / "raw" / run_prefix


def tool_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(TOOLS_VENV_BIN) + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


def clone_copy(src: Path, dst: Path) -> None:
    # APFS clonefile（cp -c）零额外空间；跨卷或失败时回退真实拷贝
    try:
        subprocess.run(
            ["cp", "-cR", str(src), str(dst)], check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def dependency_readiness(task_id: str) -> dict[str, Any]:
    source, environment, native_source, vista_source = task_paths(task_id)
    spec = TASKS[task_id]
    checks: dict[str, Any] = {
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "task_md": (source / "task.md").is_file(),
        "verifier_source": (source / "verifier" / "test_outputs.py").is_file(),
        "inputs_present": {
            name: (environment / name).exists() for name in spec["inputs"]
        },
        "vista_manifest": (vista_source / "manifest.yaml").is_file(),
        "vista_root_skill": (vista_source / "SKILL.md").is_file(),
        "native_modules": sorted(
            item.parent.name for item in native_source.glob("*/SKILL.md")
        ),
    }
    try:
        completed = subprocess.run(
            [str(VERIFIER_PYTHON), "-c",
             "import pytest, numpy, pandas, yaml, rapidfuzz, openpyxl; print('ok')"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        checks["verifier_imports"] = completed.returncode == 0
    except Exception as exc:
        checks["verifier_imports"] = False
        checks["verifier_import_error"] = str(exc)
    checks["ready"] = all(
        value if isinstance(value, bool) else bool(value)
        for key, value in checks.items()
        if key not in {"native_modules"}
    ) and bool(checks["native_modules"])
    return checks


def write_local_verifier(source: Path, verifier: Path, workspace: Path) -> Path:
    """Copy the SkillsBench verifier with its /root prefix rewritten to the workspace.

    Every check stays byte-identical; only the host base path differs.
    """

    text = (source / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    rewritten = text.replace("/root", str(workspace)).replace(
        '"/verifier/', f'"{verifier}/'
    )
    path = verifier / "test_outputs_local.py"
    path.write_text(rewritten, encoding="utf-8")
    # 验证器伴随数据（expected_output.json / ground_truth.json）必须随
    # test_outputs.py 一起进试验目录，否则质量/内容检查每轮 FileNotFoundError
    # （此前批次 24 个试验因此作废）
    for item in sorted((source / "verifier").iterdir()):
        if item.is_file() and item.name not in {"test_outputs.py", "test.sh"}:
            shutil.copy2(item, verifier / item.name)
    return path


def prepare_trial(task_id: str, condition: str, replicate: int) -> dict[str, Path]:
    spec = TASKS[task_id]
    source, environment, native_source, vista_source = task_paths(task_id)
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    base = LOCAL_ROOT / f"{result_root(task_id).name}-{condition}-replicate-{replicate:02d}"
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace = base / "workspace"
    # 验证器目录移出试验 base（此前是 workspace 的兄弟目录，agent 用 yolo 权限
    # 可以扫到并自建 ground truth 自证通过）；藏进 LOCAL_ROOT 的隐藏目录
    verifier = LOCAL_ROOT / ".verifiers" / base.name
    router_source = base / "router-source"
    storage = base / "zcode-storage"
    for directory in (workspace, verifier, router_source, storage):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "modules").mkdir(exist_ok=True)
    shutil.copy2(source / "task.md", workspace / "task.md")
    for name in spec["inputs"]:
        item = environment / name
        if item.is_dir():
            shutil.copytree(item, workspace / name)
        else:
            clone_copy(item, workspace / name)
    for step in spec.get("unzip", []):
        # 大输入只解压一次进共享缓存，各试验用 APFS clone 进工作区，
        # 避免 12 试验 × ~800MB 把磁盘写满
        zip_path = environment / step["zip"]
        cache = LOCAL_ROOT / ".shared-inputs" / task_id / zip_path.stem
        marker = cache / ".complete"
        if not marker.exists():
            cache.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(cache)
            marker.write_text("ok", encoding="utf-8")
        payload = cache
        if step.get("move_top_to"):
            # zip 内部顶目录名（如 01JUN2025-31AUG2025_form13f）与
            # move_top_to 目标名（如 2025-q2）不同，取缓存里唯一的顶层目录
            tops = [p for p in cache.iterdir() if p.name != ".complete"]
            if len(tops) == 1 and tops[0].is_dir():
                payload = tops[0]
        target = workspace / (step.get("move_top_to") or step["dest"])
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(payload.iterdir()):
            if entry.name.startswith("."):
                continue
            clone_copy(entry, target / entry.name)

    module_ids = [item.parent.name for item in sorted(native_source.glob("*/SKILL.md"))]
    if condition == "native":
        for module_id in module_ids:
            shutil.copytree(native_source / module_id, workspace / "modules" / module_id)
    elif condition == "pd-vista":
        shutil.copy2(vista_source / "SKILL.md", workspace / "VISTA.md")
        shutil.copy2(vista_source / "manifest.yaml", router_source / "manifest.yaml")
        for module_id in module_ids:
            shutil.copytree(native_source / module_id, router_source / "modules" / module_id)
    elif condition == "pd-self":
        shutil.copy2(vista_source / "SKILL.md", workspace / "VISTA.md")
        for module_id in module_ids:
            shutil.copytree(native_source / module_id, workspace / "modules" / module_id)

    write_local_verifier(source, verifier, workspace)
    return {
        "base": base, "workspace": workspace, "verifier": verifier,
        "router_source": router_source, "storage": storage,
        "module_ids": module_ids,
    }


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "no module named", "modulenotfounderror", "failed to import",
        "error while loading conftest", "collection error", "connection timed out",
    ))


def public_diagnostic(output: str, *, limit: int = 5000) -> str:
    """Keep agent-observable failure lines while removing host-specific absolute paths
    and any verifier/ground-truth path fragments (oracle-location leakage)."""

    selected: list[str] = []
    markers = ("FAILED", "ERROR", "assert", "assertion", "not found", "missing",
               "exit code", "passed", "failed")
    for raw in output.splitlines():
        if not any(marker in raw for marker in markers):
            continue
        line = re.sub(r"/Users/[^\s:]+", "<local-path>", raw)
        # 脱敏验证器数据路径片段：泄露 verifier/expected_output.json、
        # ground_truth.json 等位置会让 agent 定位并自证通过
        line = re.sub(r"[\w./-]*(?:verifier|ground_truth|expected_output)[\w./-]*",
                      "<verifier-data>", line)
        line = line.strip()
        if line and line not in selected:
            selected.append(line)
    diagnostic = "\n".join(selected[:40])
    return (diagnostic or "The local verifier run failed.")[:limit]


def run_verifier(workspace: Path, verifier: Path, turn: int) -> tuple[bool, str, bool]:
    command = [
        str(VERIFIER_PYTHON), "-m", "pytest",
        str(verifier / "test_outputs_local.py"),
        "-rA", "-v", "--tb=short", "-p", "no:cacheprovider",
    ]
    try:
        completed = subprocess.run(
            command, cwd=workspace, env=tool_environment(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=VERIFIER_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
        return False, output, False
    output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = verifier_infrastructure_failure(output) and completed.returncode != 0
    return completed.returncode == 0 and not infra, output, infra


def parse_zcode_turn(stdout: str) -> dict[str, Any]:
    """Parse one headless --json result into session/turn/response evidence."""

    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("zcode --json output carries no JSON object")
    payload = json.loads(text[start:])
    for key in ("sessionId", "turnId", "usage"):
        if key not in payload:
            raise RuntimeError(f"zcode --json output is missing {key!r}")
    return payload


def turn_records(storage: Path, session_id: str, turn_id: str) -> list[dict[str, Any]]:
    path = model_io_rollout_path(storage, session_id)
    if not path.is_file():
        return []
    return [
        record for record in parse_model_io_rollout_raw(path)
        if record.get("turnId") == turn_id
    ]


def parse_model_io_rollout_raw(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def module_accesses(records: list[dict[str, Any]], module_ids: list[str]) -> list[str]:
    accessed: list[str] = []
    for record in records:
        for tool_call in ((record.get("response") or {}).get("toolCalls") or []):
            rendered = json.dumps(tool_call.get("input") or {}, ensure_ascii=False)
            for module_id in module_ids:
                if f"modules/{module_id}/SKILL.md" in rendered and module_id not in accessed:
                    accessed.append(module_id)
    return accessed


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    destination = workspace / "modules" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(router_source / "modules" / module_id, destination)
    return destination / "SKILL.md"


def run_replicate(task_id: str, condition: str, replicate: int, *, model: str) -> dict[str, Any]:
    paths = prepare_trial(task_id, condition, replicate)
    base: Path = paths["base"]
    workspace: Path = paths["workspace"]
    verifier: Path = paths["verifier"]
    router_source: Path = paths["router_source"]
    storage: Path = paths["storage"]
    module_ids: list[str] = paths["module_ids"]

    parser = FeedbackParser()
    router = (
        DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml"))
        if condition == "pd-vista" else None
    )
    disclosed: list[str] = []
    accessed_after_disclosure: list[str] = []
    session_id: str | None = None
    prompt = COMMON_PROMPT.format(
        benchmark=TASKS[task_id]["benchmark"],
        inputs=", ".join(TASKS[task_id]["inputs"]),
        outputs=", ".join(f"./{name}" for name in TASKS[task_id]["output_files"]),
    ) + INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    final_pass = False
    pass_turn: int | None = None
    stop_reason = "max_turns"
    trigger_opportunities = routable_feedback = unroutable_feedback = 0
    disclosure_count = access_rounds = 0
    last_disclosure_turn: int | None = None
    access_after_last_disclosure = False
    stalled_rounds = 0

    process_env = tool_environment()
    process_env.update(turn_environment(base))

    for turn in range(1, MAX_TURNS + 1):
        command = command_for_turn(workspace=workspace, prompt=prompt, session_id=session_id)
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=workspace, env=process_env, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=MODEL_TURN_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True

            def decoded(value: str | bytes | None) -> str:
                if value is None:
                    return ""
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return value

            completed = subprocess.CompletedProcess(
                command, 124, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr)
            )
        (base / f"turn-{turn}.jsonl").write_text(completed.stdout or "", encoding="utf-8")
        (base / f"turn-{turn}.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")

        records: list[dict[str, Any]] = []
        turn_payload: dict[str, Any] = {}
        if not timed_out and (completed.stdout or "").strip():
            try:
                turn_payload = parse_zcode_turn(completed.stdout or "")
                observed = turn_payload["sessionId"]
                if session_id is not None and observed != session_id:
                    stop_reason = "session_identity_changed"
                    break
                session_id = observed
                records = turn_records(storage, session_id, turn_payload["turnId"])
            except (RuntimeError, json.JSONDecodeError):
                turn_payload = {}
        tool_uses = sum(len((r.get("response") or {}).get("toolCalls") or []) for r in records)
        accessed_now = module_accesses(records, module_ids)
        if condition == "pd-vista":
            accessed_now = [module for module in accessed_now if module in disclosed]
        if accessed_now:
            access_rounds += 1
            accessed_after_disclosure.extend(accessed_now)
            if last_disclosure_turn is not None and turn == last_disclosure_turn + 1:
                access_after_last_disclosure = True
        usage_summary = rollout_usage_summary(parse_model_io_rollout(
            model_io_rollout_path(storage, session_id)
        )) if session_id and model_io_rollout_path(storage, session_id).is_file() else {}

        verifier_pass, verifier_output, verifier_infra = run_verifier(workspace, verifier, turn)
        turns.append({
            "turn": turn, "return_code": completed.returncode,
            "model_turn_timeout": timed_out,
            "zcode_turn_id": turn_payload.get("turnId"),
            "requests": len(records), "tool_uses": tool_uses,
            "accessed_modules": accessed_now,
            "verifier_pass": verifier_pass,
            "verifier_infrastructure_error": verifier_infra,
        })
        print(
            f"task={task_id} condition={condition} replicate={replicate} turn={turn} "
            f"requests={len(records)} tools={tool_uses} "
            f"accessed={accessed_now} verifier_pass={verifier_pass} "
            f"model_timeout={timed_out}", flush=True,
        )
        if verifier_pass:
            final_pass = True
            pass_turn = turn
            stop_reason = (
                "verifier_pass_after_model_timeout"
                if any(item["model_turn_timeout"] for item in turns)
                else "verifier_pass"
            )
            break
        if verifier_infra:
            stop_reason = "verifier_infrastructure_error"
            break
        if timed_out and not turn_payload:
            stop_reason = "model_turn_timeout"
            break

        trigger_opportunities += 1
        classification = parser.parse(
            FeedbackObservation(
                source=FeedbackSource.TEST_OUTPUT, text=verifier_output, exit_code=1
            ),
            trial_id=f"{base.name}-public",
        )
        feedback_types = list(classification.trigger_names)
        if not feedback_types or feedback_types == ["unknown"]:
            feedback_types = ["command_failure"]
        signatures = failure_layer_signatures(task_id, verifier_output)
        for signature in signatures:
            if signature not in feedback_types:
                feedback_types.append(signature)
        newly_disclosed: list[str] = []
        diagnostic = public_diagnostic(verifier_output)
        if condition == "pd-vista":
            assert router is not None
            payload = json.dumps({
                "schema_version": 1, "kind": "vista_public_verifier_feedback",
                "outcome": "fail", "feedback_types": feedback_types,
                "summary": diagnostic,
            })
            routed = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{base.name}",
            )
            # 失败层签名以字符串透传进路由；选不出新模块（无匹配或已披露完）
            # 不披露，记为 unroutable
            decision = router.route(
                (*routed.trigger_names, *signatures), previous_modules=disclosed
            )
            if decision.modules:
                routable_feedback += 1
            else:
                unroutable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
                last_disclosure_turn = turn
                access_after_last_disclosure = False
        # 停滞检测：签名变化（agent 改出了新失败层）、新披露或读取模块都算进展；
        # 披露开始后连续 2 轮无进展提前停止，不再空转到 MAX_TURNS
        made_progress = bool(
            newly_disclosed or accessed_now or classification.occurrence == 1
        )
        stalled_rounds = 0 if made_progress else stalled_rounds + 1
        if condition == "pd-vista" and disclosed and stalled_rounds >= 2:
            stop_reason = "stalled_after_full_disclosure"
            break
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(
                f"./modules/{module}/SKILL.md" for module in newly_disclosed
            ) + ". Read it before repairing."
        elif condition == "pd-self":
            instruction = "The guidance modules are already available under ./modules/; decide which are relevant and read them before repairing."
        elif condition == "native":
            instruction = "Use the original modules already available under ./modules/."
        elif disclosed:
            # 模块披露完后再失败：点名重读已披露模块，而不是只让它继续修
            instruction = (
                "The verifier still fails. Re-examine the already disclosed modules "
                "under ./modules/ (" + ", ".join(disclosed) + ") and repair the "
                "outputs accordingly."
            )
        else:
            instruction = "No task Skill is available; repair from the public failure category."
        prompt = (
            f"The public verifier check failed with {', '.join(feedback_types)}. "
            f"Public diagnostic:\n{diagnostic}\n{instruction} "
            "Continue in the same session, work only inside this directory, and repair "
            f"the required outputs: {', '.join('./' + name for name in TASKS[task_id]['output_files'])}."
        )

    result_dir = result_root(task_id) / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True, exist_ok=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for name in TASKS[task_id]["output_files"]:
        if (workspace / name).is_file():
            shutil.copy2(workspace / name, result_dir / name)

    usage = usage_summary or {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                              "cache_read_tokens": 0, "input_plus_output_tokens": 0}
    # 评审意见 4/三-4：干净闭环收紧为「披露后下一轮有模块读取 + gap ≤ 1」
    disclosure_gap = (
        pass_turn - last_disclosure_turn
        if final_pass and last_disclosure_turn is not None else None
    )
    clean_closure = int(
        condition == "pd-vista" and final_pass and disclosure_count > 0
        and access_after_last_disclosure and disclosure_gap is not None
        and disclosure_gap <= 1
    )
    quarantined = stop_reason in {
        "provider_policy_rejection", "api_error", "session_identity_changed",
        "verifier_infrastructure_error", "model_turn_timeout",
    }
    status = (
        "passed_with_clean_vista_feedback_loop" if clean_closure
        else "passed" if final_pass and condition != "pd-vista"
        else "passed_without_clean_vista_feedback_loop" if final_pass
        else f"quarantined_{stop_reason}" if quarantined
        else f"failed_{stop_reason}"
    )
    result = {
        "schema_version": 1,
        "run_id": f"{result_root(task_id).name}-{condition}-replicate-{replicate:02d}",
        "task_id": task_id, "condition": condition, "replicate_id": replicate,
        "backend": "zcode-local-minimal-dependencies", "docker_used": False,
        "requested_provider_model": model, "session_id": session_id,
        "turns": turns, "usage": usage,
        "initially_available_modules": module_ids if condition in ("native", "pd-self") else [],
        "disclosed_modules": disclosed,
        "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)),
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities,
            "routable_feedback": routable_feedback,
            "unroutable_feedback": unroutable_feedback,
            "successful_disclosures": disclosure_count,
            "next_round_module_access": access_rounds,
            "disclosure_to_pass_gap": disclosure_gap,
            "access_after_last_disclosure": access_after_last_disclosure,
            "first_turn_pass": bool(turns and turns[0]["verifier_pass"]),
            "clean_complete_closure": clean_closure,
        },
        "final_verifier_pass": final_pass, "stop_reason": stop_reason,
        "status": status, "workspace": str(workspace),
    }
    (result_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def run_group(task_id: str, *, model: str, jobs: int = 1, max_quarantine_break: bool = True) -> int:
    readiness = dependency_readiness(task_id)
    if not readiness["ready"]:
        raise RuntimeError(f"{task_id} local dependencies are not ready: {json.dumps(readiness, ensure_ascii=False)}")
    root = result_root(task_id)
    if not root.exists():
        root.mkdir(parents=True)
        (root / "readiness.json").write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    pending: list[tuple[int, str]] = []
    results: list[dict[str, Any]] = []
    schedule = schedule_for(task_id)
    for replicate, condition in schedule:
        existing = root / condition / f"replicate-{replicate:02d}" / "result.json"
        if existing.is_file():
            results.append(json.loads(existing.read_text(encoding="utf-8")))
        else:
            pending.append((replicate, condition))

    if jobs > 1:
        # Each trial owns an isolated workspace, verifier and ZCODE_STORAGE_DIR,
        # so trials are independent; the only shared limit is API concurrency.
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(run_replicate, task_id, condition, replicate, model=model):
                    (replicate, condition)
                for replicate, condition in pending
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"trial done: {result['run_id']} status={result['status']}", flush=True)
    else:
        for replicate, condition in pending:
            result = run_replicate(task_id, condition, replicate, model=model)
            results.append(result)
            if max_quarantine_break and result["status"].startswith("quarantined_"):
                break
    condition_summaries = {}
    for condition in CONDITIONS:
        values = [result for result in results if result["condition"] == condition]
        planned = sum(1 for _, scheduled in schedule if scheduled == condition)
        condition_summaries[condition] = {
            "planned_trials": planned, "completed_trials": len(values),
            "verifier_passes": sum(result["final_verifier_pass"] for result in values),
            "usage": {
                key: sum(result["usage"].get(key, 0) for result in values)
                for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens")
            },
        }
    summary = {
        "schema_version": 1,
        "protocol": (
            f"{task_id}-zcode-local-glm53-4cond-x"
            f"{len(schedule) // len(CONDITIONS)}"
        ),
        "task_id": task_id,
        "benchmark_task_id": TASKS[task_id]["benchmark"],
        "conditions": list(CONDITIONS),
        "replicates_per_condition": len(schedule) // len(CONDITIONS),
        "planned_trials": len(schedule), "completed_trials": len(results),
        "requested_provider_model": model,
        "backend": "zcode-local-minimal-dependencies",
        "condition_summaries": condition_summaries,
        "results": [
            {
                "condition": result["condition"], "replicate_id": result["replicate_id"],
                "status": result["status"], "final_verifier_pass": result["final_verifier_pass"],
                "session_id": result["session_id"], "usage": result["usage"],
                "disclosed_modules": result["disclosed_modules"],
                "accessed_modules_after_disclosure": result["accessed_modules_after_disclosure"],
                "vista_metrics": result["vista_metrics"],
            }
            for result in results
        ],
    }
    (root / "group-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if len(results) == len(schedule) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="run readiness and preflight gates only, no model call")
    parser.add_argument("--condition", choices=CONDITIONS, default=None,
                        help="run a single replicate instead of the full 3x3 group")
    parser.add_argument("--replicate", type=int, choices=REPLICATES, default=None)
    parser.add_argument("--jobs", type=int, default=1,
                        help="run pending trials concurrently (isolated workspaces; "
                             "bounded by API concurrency limits)")
    args = parser.parse_args(argv)

    model_config = current_zcode_model_config()
    last_error: Exception | None = None
    launcher = None
    for attempt in range(3):
        try:
            launcher = freeze_launcher()
            break
        except LocalGateError as exc:
            last_error = exc
            print(f"launcher gate attempt {attempt + 1} failed: {exc}", flush=True)
            time.sleep(15)
    if launcher is None:
        raise SystemExit(f"launcher gate failed after retries: {last_error}")
    print(f"zcode launcher ok: {launcher.version} | model: {model_config['model_id']}", flush=True)
    if args.dry_run:
        readiness = dependency_readiness(args.task)
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0 if readiness["ready"] else 1
    if args.condition or args.replicate:
        if not (args.condition and args.replicate):
            parser.error("--condition and --replicate must be set together")
        result = run_replicate(args.task, args.condition, args.replicate,
                               model=model_config["model_id"])
        return 0 if result["final_verifier_pass"] else 1
    return run_group(args.task, model=model_config["model_id"], jobs=args.jobs)


if __name__ == "__main__":
    raise SystemExit(main())
