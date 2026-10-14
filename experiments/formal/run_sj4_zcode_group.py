"""Run the sj4 experiment: four conditions (noskill / flat / pd / vista) x 3 replicates.

sj4 设计（manual: reports/experiment-sj4-protocol-prompt.md）:
- noskill/flat/pd: 单回合单 episode——一个 headless ZCode 会话自由多步工作直到
  自己宣布完成，验证器在回合结束后跑一次，失败即停止，无反馈、无第二回合
  （等价 SkillJuror no-skill / origin-flat / progressive-disclosure）。
- vista: 唯一多回合组——失败 -> 脱敏公共诊断 + 失败层签名 -> 路由 -> 披露
  对应 PD 结构模块（与 pd 条件同源）-> 同 session resume 修复。

Runner 必改项（manual 第三节）全部落地:
1. public_diagnostic 对 pytest 断言行 E assert X == Y 两侧数值脱敏
   （教训: 20261013 noskill rep1 直接采纳诊断里泄露的标准答案 2451）。
2. 回合结构分条件: noskill/flat/pd 固定 1 回合，代码不进反馈循环；MAX_TURNS 只对 vista。
3. flat/pd 工件复制进 workspace，初始 prompt 点名技能位置。
4. 现有保护保留: 验证器藏于 agent 不可触及位置、路径片段清洗、模型身份 fail-closed。
5. vista 披露模块与 pd 同源（同一 PD 工件，唯一差异是披露驱动者）。

双端点并行（用户确认）: --provider new-provider（v1 chat-completions，宿主默认配置）
或 new-provider-2（anthropic-messages，经修正配置 + ZCODE_PERSONAL_PROVIDER_CONFIG_FILE）。
两个实例分别跑两个任务；每个实例内 trial 用 --workers 线程池并发（用户确认 4-6），
达到双端点 × N 线程并行。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
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
ARTIFACT_ROOT = REPO / "experiments" / "artifacts" / "sj4"
TOOLS_VENV_BIN = REPO / ".venv" / "bin"
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv" / "bin" / "python")
).expanduser().absolute()
LOCAL_ROOT = Path(
    os.environ.get(
        "VISTA_EXPERIMENT_ROOT", Path.home() / "Library" / "Caches" / "VistaExperiments"
    )
).expanduser().resolve()
MAX_TURNS = 6  # 只对 vista 生效；noskill/flat/pd 固定 1 回合
MODEL_TURN_TIMEOUT_SECONDS = 1800
VERIFIER_TIMEOUT_SECONDS = 900
CONDITIONS = ("noskill", "flat", "pd", "vista")
REPLICATES = (1, 2, 3)
# 并发 episode 线程共享同一份 .shared-inputs 解压缓存，抽取段须互斥
_UNZIP_LOCK = threading.Lock()

PROVIDER_V1 = "new-provider"
PROVIDER_V3 = "new-provider-2"
PROVIDER_CONFIG_V3 = Path(
    os.environ.get(
        "VISTA_ZCODE_PROVIDER_CONFIG_V3",
        Path.home() / ".zcode" / "v2" / "provider_config_v3_corrected.json",
    )
).expanduser().resolve()

TASKS: dict[str, dict[str, Any]] = {
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
}

FAILURE_LAYER_RULES: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
    "task-011": (
        (("q4_answer", ".lower() ==", "ans.lower"), "13f_name_list_mismatch"),
        (("q1_answer", "q2_answer", "q3_answer"), "13f_value_mismatch"),
        (("os.path.isfile", "no such file", "not found"), "13f_missing_output"),
    ),
    "task-012": (
        (("missed fraud", "number of fraud cases"), "fraud_missed"),
        (("false positives",), "fraud_false_positive"),
        (("reason mismatch", "vendor name mismatch", "iban mismatch",
          "po number mismatch", "amount mismatch"), "fraud_field_mismatch"),
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


COMMON_PROMPT = """Solve the isolated {benchmark} task in this working directory. \
Work only inside this directory and do not access parent or external directories. \
First read task.md and the supplied input files: {inputs}. \
Write the required output files declared in task.md to this directory: {outputs}. \
Use the local python interpreter for any simulation or analysis you need to run. \
Hidden verifier tests are run only by the controller after the episode."""
INITIAL_PROMPTS = {
    "noskill": " No task Skill is available in this condition.",
    "flat": " All guidance modules are available as flattened single files under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
    "pd": " A progressive-disclosure skill is available at ./skill/SKILL.md with detailed references under ./skill/references/; read the entry skill and decide for yourself which references to load and use.",
    "vista": " No task Skill is initially available; after a public verifier failure VISTA may disclose guidance modules.",
}


def task_paths(task_id: str) -> tuple[Path, Path]:
    spec = TASKS[task_id]
    source = VENDOR_TASKS / spec["benchmark"]
    return source, source / "environment"


def result_root(task_id: str) -> Path:
    run_prefix = os.environ.get(
        "VISTA_RUN_PREFIX",
        f"formal-{task_id}-sj4-x3-glm53-{datetime.now().strftime('%Y%m%d-%H')}",
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
        subprocess.run(["cp", "-cR", str(src), str(dst)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def write_local_verifier(source: Path, verifier: Path, workspace: Path) -> Path:
    """Copy the SkillsBench verifier with its /root prefix rewritten to the workspace.

    Every check stays byte-identical; only the host base path differs.
    Companion data files (expected_output.json / ground_truth.json) travel with
    the verifier or the content checks hit FileNotFoundError every turn.
    """

    text = (source / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    rewritten = text.replace("/root", str(workspace)).replace(
        '"/verifier/', f'"{verifier}/'
    )
    path = verifier / "test_outputs_local.py"
    path.write_text(rewritten, encoding="utf-8")
    for item in sorted((source / "verifier").iterdir()):
        if item.is_file() and item.name not in {"test_outputs.py", "test.sh"}:
            shutil.copy2(item, verifier / item.name)
    return path


def prepare_trial(task_id: str, condition: str, replicate: int) -> dict[str, Any]:
    spec = TASKS[task_id]
    source, environment = task_paths(task_id)
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    base = LOCAL_ROOT / f"{result_root(task_id).name}-{condition}-replicate-{replicate:02d}"
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse trial evidence: {base}")
    workspace = base / "workspace"
    # 验证器藏进 agent 不可触及的隐藏目录（oracle 泄漏教训保留）
    verifier = LOCAL_ROOT / ".verifiers" / base.name
    router_source = base / "router-source"
    storage = base / "zcode-storage"
    for directory in (workspace, verifier, router_source, storage):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "task.md", workspace / "task.md")
    for name in spec["inputs"]:
        item = environment / name
        if item.is_dir():
            shutil.copytree(item, workspace / name)
        else:
            clone_copy(item, workspace / name)
    for step in spec.get("unzip", []):
        # 大输入只解压一次进共享缓存，各试验用 APFS clone 进工作区
        zip_path = environment / step["zip"]
        cache = LOCAL_ROOT / ".shared-inputs" / task_id / zip_path.stem
        with _UNZIP_LOCK:
            marker = cache / ".complete"
            if not marker.exists():
                cache.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(cache)
                marker.write_text("ok", encoding="utf-8")
        payload = cache
        if step.get("move_top_to"):
            tops = [p for p in cache.iterdir() if p.name != ".complete"]
            if len(tops) == 1 and tops[0].is_dir():
                payload = tops[0]
        target = workspace / (step.get("move_top_to") or step["dest"])
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(payload.iterdir()):
            if entry.name.startswith("."):
                continue
            clone_copy(entry, target / entry.name)

    if condition == "flat":
        # flat 工件：每模块一个压平 SKILL.md（SkillJuror flatten-source 形态）
        flat_root = ARTIFACT_ROOT / task_id / "flat"
        (workspace / "modules").mkdir(exist_ok=True)
        for skill_md in sorted(flat_root.glob("*/SKILL.md")):
            clone_copy(skill_md.parent, workspace / "modules" / skill_md.parent.name)
    elif condition == "pd":
        # pd 工件：入口 SKILL.md + references/（与 vista 披露模块同源）
        clone_copy(ARTIFACT_ROOT / task_id / "pd", workspace / "skill")
    elif condition == "vista":
        # 初始隐藏；披露 payload（PD 工件）与 manifest 进 router-source
        clone_copy(ARTIFACT_ROOT / task_id / "pd", router_source / "pd")
        shutil.copy2(ARTIFACT_ROOT / task_id / "manifest.yaml", router_source / "manifest.yaml")

    write_local_verifier(source, verifier, workspace)
    return {
        "base": base, "workspace": workspace, "verifier": verifier,
        "router_source": router_source, "storage": storage,
    }


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "no module named", "modulenotfounderror", "failed to import",
        "error while loading conftest", "collection error", "connection timed out",
    ))


_LITERAL_OPERAND = re.compile(
    r"""^\s*(?:-?\d[\d.,eE+-]*|'[^']*'|"[^"]*"|\[.*|\{.*|\(.*|None|True|False|nan)\s*$"""
)
_VALUE_FRAGMENTS = (
    # AssertionError 消息里的 Expected/Got 值：保留字段与位置，掩去值
    # 未加引号的裸值（如 Expected PO-1234）也要掩，故兜底匹配到逗号/空白为止；
    # (?<!<) 防止把已替换的 <expected>/<actual> 占位符再掩一次。
    (re.compile(r"(?i)(?<!<)\b(expected)\s*[:=]?\s*('[^']*'|\"[^\"]*\"|\[[^\]]*\]|(?!<)[^\s,;)]+)"),
     r"\1 <expected>"),
    (re.compile(r"(?i)(?<!<)\b(got|actual)\s*[:=]?\s*('[^']*'|\"[^\"]*\"|\[[^\]]*\]|(?!<)[^\s,;)]+)"),
     r"\1 <actual>"),
    # pytest 序列比较：At index N diff: X != Y
    (re.compile(r"(?i)(at index \d+ diff:).*"), r"\1 <actual> != <expected>"),
)
# pytest 断言展开的 diff 行（E   - ... / E   + ... / E   ? ...）整行丢弃
_DIFF_LINE = re.compile(r"^\s*E\s+[-+?]\s")


def mask_assert_values(line: str) -> str:
    """Mask comparison *values* in assertion lines while keeping field names.

    教训 1：20261013 批次所有多轮试验的诊断都带了 `assert 3457 == 2451` 这类真值，
    agent 直接抄答案。教训 2：sj4 首版把 `answers["q2_answer"]` 也一并掩掉，
    agent 只能看到 test 名、不知道哪一项错。现在：
      - `==`/`!=` 右侧一律掩为 <expected>；
      - 左侧若是字面值（数字/字符串/列表/None 等）掩为 <actual>，
        若是表达式（如 answers["q2_answer"]、act["po_number"]）原样保留；
      - Expected/Got/At index diff 片段掩值。
    """

    out = line
    match = re.search(r"\bassert\b(.*?)(==|!=)(.*)$", out)
    if match:
        lhs, op = match.group(1), match.group(2)
        lhs_text = " <actual> " if _LITERAL_OPERAND.match(lhs) else lhs
        rhs = match.group(3)
        tail = ""
        if rhs.rstrip().endswith(", ("):
            tail = ", ("
        out = out[: match.start()] + f"assert{lhs_text}{op} <expected>{tail}"
        out = re.sub(r"assert\s+<actual>\s+", "assert <actual> ", out)
    for pattern, replacement in _VALUE_FRAGMENTS:
        out = pattern.sub(replacement, out)
    return out


def public_diagnostic(output: str, *, limit: int = 5000) -> str:
    """Keep agent-observable failure lines while removing host paths, verifier
    data path fragments, and leaked assert operand values."""

    selected: list[str] = []
    # 大小写不敏感：首版只因 "AssertionError" 不含小写 "assert" 才没漏出
    # Expected/Got 值，属于侥幸；现在显式选中并显式掩值。
    markers = ("failed", "error", "assert", "not found", "missing", "exit code",
               "passed", "mismatch", "expected", "got ")
    for raw in output.splitlines():
        if _DIFF_LINE.match(raw):
            continue
        if not any(marker in raw.casefold() for marker in markers):
            continue
        line = re.sub(r"/Users/[^\s:]+", "<local-path>", raw)
        line = re.sub(r"[\w./-]*(?:verifier|ground_truth|expected_output)[\w./-]*",
                      "<verifier-data>", line)
        line = mask_assert_values(line)
        line = line.strip()
        if line and line not in selected:
            selected.append(line)
    diagnostic = "\n".join(selected[:40])
    return (diagnostic or "The local verifier run failed.")[:limit]


def run_verifier(workspace: Path, verifier: Path, turn: int) -> tuple[bool, str, bool, float]:
    command = [
        str(VERIFIER_PYTHON), "-m", "pytest",
        str(verifier / "test_outputs_local.py"),
        "-rA", "-v", "--tb=short", "-p", "no:cacheprovider",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=workspace, env=tool_environment(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=VERIFIER_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
        return False, output, False, time.monotonic() - started
    output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    elapsed = time.monotonic() - started
    infra = verifier_infrastructure_failure(output) and completed.returncode != 0
    return completed.returncode == 0 and not infra, output, infra, elapsed


def parse_zcode_turn(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("zcode --json output carries no JSON object")
    payload = json.loads(text[start:])
    for key in ("sessionId", "turnId", "usage"):
        if key not in payload:
            raise RuntimeError(f"zcode --json output is missing {key!r}")
    return payload


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


def turn_records(storage: Path, session_id: str, turn_id: str) -> list[dict[str, Any]]:
    path = model_io_rollout_path(storage, session_id)
    if not path.is_file():
        return []
    return [
        record for record in parse_model_io_rollout_raw(path)
        if record.get("turnId") == turn_id
    ]


SKILL_PATH_PATTERN = re.compile(r"(?:modules/[\w.-]+/SKILL\.md|skill/(?:SKILL\.md|references/[\w.-]+\.md))")


def estimate_tokens(text: str) -> int:
    """粗估 token：英文/代码约 4 字符/token，CJK 约 1 字符/token。
    只用于条件间相对比较（flat vs pd vs vista 同一估计口径）。"""

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk + 3) // 4


def skill_reads(records: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
    """Skill-reading observations: which skill files the agent opened.

    file_tokens = 被读文件本身的估计 token（组织方式对比的核心观测）；
    context_input_tokens = 该次请求的整段上下文输入量，仅作参考。
    首版把 context_input_tokens 当作技能阅读 token 求和，同一个 1.9KB
    文件被记成 15k~67k，flat/pd 对比因此失真。"""

    reads: list[dict[str, Any]] = []
    for record in records:
        usage = (record.get("response") or {}).get("usage") or {}
        for tool_call in ((record.get("response") or {}).get("toolCalls") or []):
            rendered = json.dumps(tool_call.get("input") or {}, ensure_ascii=False)
            for match in sorted(set(SKILL_PATH_PATTERN.findall(rendered))):
                if any(existing["file"] == match and existing["turn_id"] == record.get("turnId")
                       for existing in reads):
                    continue
                path = workspace / match
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                reads.append({
                    "file": match,
                    "turn_id": record.get("turnId"),
                    "file_chars": len(content),
                    "file_tokens": estimate_tokens(content),
                    "context_input_tokens": int(usage.get("inputTokens", 0) or 0),
                })
    return reads


def rollout_agent_seconds(storage: Path, session_id: str) -> float:
    path = model_io_rollout_path(storage, session_id)
    if not path.is_file():
        return 0.0
    return sum(
        (record.get("durationMs") or 0) / 1000.0
        for record in parse_model_io_rollout_raw(path)
    )


def run_episode(task_id: str, condition: str, replicate: int, *, model: str) -> dict[str, Any]:
    """One trial. noskill/flat/pd: single episode, no feedback. vista: turn loop."""

    paths = prepare_trial(task_id, condition, replicate)
    base: Path = paths["base"]
    workspace: Path = paths["workspace"]
    verifier: Path = paths["verifier"]
    router_source: Path = paths["router_source"]
    storage: Path = paths["storage"]

    parser = FeedbackParser()
    router = (
        DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml"))
        if condition == "vista" else None
    )
    disclosed: list[str] = []
    accessed_after_disclosure: list[str] = []
    entry_disclosed = False
    session_id: str | None = None
    prompt = COMMON_PROMPT.format(
        benchmark=TASKS[task_id]["benchmark"],
        inputs=", ".join(TASKS[task_id]["inputs"]),
        outputs=", ".join(f"./{name}" for name in TASKS[task_id]["output_files"]),
    ) + INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    all_skill_reads: list[dict[str, Any]] = []
    final_pass = False
    pass_turn: int | None = None
    stop_reason = "max_turns"
    trigger_opportunities = routable_feedback = unroutable_feedback = 0
    disclosure_count = access_rounds = 0
    last_disclosure_turn: int | None = None
    access_after_last_disclosure = False
    stalled_rounds = 0
    previous_signatures: tuple[str, ...] = ()
    verifier_seconds = 0.0
    agent_net_seconds = 0.0
    max_turns = MAX_TURNS if condition == "vista" else 1

    process_env = tool_environment()
    process_env.update(turn_environment(base))

    for turn in range(1, max_turns + 1):
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
        if not timed_out and completed.returncode != 0:
            # ZCode 自身报错（如 API 域名 ENOTFOUND）是基础设施故障，不是 agent 失败：
            # 首版照常跑验证器并进入反馈，task-011 vista r2 因此被污染。
            turns.append({
                "turn": turn, "return_code": completed.returncode,
                "model_turn_timeout": False, "zcode_turn_id": None,
                "requests": 0, "tool_uses": 0, "skill_reads": [],
                "turn_usage": {}, "verifier_pass": False,
                "verifier_infrastructure_error": False,
            })
            stop_reason = "api_error"
            break

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
        reads_now = skill_reads(records, workspace)
        all_skill_reads.extend(reads_now)
        accessed_now = [read["file"] for read in reads_now]
        if accessed_now:
            access_rounds += 1
            accessed_after_disclosure.extend(accessed_now)
            if last_disclosure_turn is not None and turn == last_disclosure_turn + 1:
                access_after_last_disclosure = True

        verifier_pass, verifier_output, verifier_infra, v_seconds = run_verifier(
            workspace, verifier, turn
        )
        verifier_seconds += v_seconds
        turns.append({
            "turn": turn, "return_code": completed.returncode,
            "model_turn_timeout": timed_out,
            "zcode_turn_id": turn_payload.get("turnId"),
            "requests": len(records), "tool_uses": tool_uses,
            "skill_reads": reads_now,
            # 回合级用量（ZCode --json 回执）：用于把上下文累积成本与技能阅读分开
            "turn_usage": {
                key: (turn_payload.get("usage") or {}).get(key)
                for key in ("modelRequestCount", "inputTokens", "outputTokens", "cacheReadTokens")
            },
            "verifier_pass": verifier_pass,
            "verifier_infrastructure_error": verifier_infra,
        })
        print(
            f"task={task_id} condition={condition} replicate={replicate} turn={turn} "
            f"requests={len(records)} tools={tool_uses} skill_reads={accessed_now} "
            f"verifier_pass={verifier_pass} model_timeout={timed_out}", flush=True,
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
        if condition != "vista":
            # 单回合组：失败即停止，无反馈、无第二回合（manual 二.1/2/3）
            stop_reason = "single_episode_failed"
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
        diagnostic = public_diagnostic(verifier_output)
        newly_disclosed: list[str] = []
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
        decision = router.route(
            (*routed.trigger_names, *signatures), previous_modules=disclosed
        )
        if decision.modules:
            routable_feedback += 1
        else:
            unroutable_feedback += 1
        for module in router.disclose(decision):
            # 披露 PD 结构模块：入口 SKILL.md 首次披露 + 对应 reference 文件；
            # 命令依赖的支撑脚本随首次披露进入 workspace。
            # 注意 DisclosedModule.path 是解析后的绝对路径，不能拿它拼目标路径
            skill_root = router_source / "pd"
            destination_skill = workspace / "skill"
            if not entry_disclosed:
                destination_skill.mkdir(parents=True, exist_ok=True)
                shutil.copy2(skill_root / "SKILL.md", destination_skill / "SKILL.md")
                for support in sorted(skill_root.iterdir()):
                    if support.name in {"SKILL.md", "references"} or support.name.startswith("."):
                        continue
                    if support.is_dir():
                        clone_copy(support, destination_skill / support.name)
                    else:
                        shutil.copy2(support, destination_skill / support.name)
                entry_disclosed = True
            source_ref = skill_root / "references" / f"{module.module_id}.md"
            destination_ref = destination_skill / "references" / f"{module.module_id}.md"
            destination_ref.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_ref, destination_ref)
            disclosed.append(module.module_id)
            newly_disclosed.append(f"./skill/references/{module.module_id}.md")
            disclosure_count += 1
            last_disclosure_turn = turn
            access_after_last_disclosure = False
        # 停滞检测：只有「新披露」或「失败层签名集合变化」算进展。
        # 首版把重读已披露模块也算进展，task-011 vista r2 因此同一签名空转到
        # 第 4 轮超时（5.7M token）。
        current_signatures = tuple(sorted(signatures))
        made_progress = bool(newly_disclosed) or current_signatures != previous_signatures
        previous_signatures = current_signatures
        stalled_rounds = 0 if made_progress else stalled_rounds + 1
        if disclosed and stalled_rounds >= 2:
            stop_reason = "stalled_after_full_disclosure"
            break
        if newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(newly_disclosed) + (
                ". Read the disclosed module before repairing."
            )
        elif disclosed:
            instruction = (
                "The verifier still fails. Re-examine the already disclosed modules "
                "under ./skill/ (" + ", ".join(disclosed) + ") and repair the outputs "
                "accordingly."
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
    # 补跑覆盖同一目录时清掉旧试验的回合证据，否则会残留多出来的 verifier-turn-N
    for stale in [*result_dir.glob("turn-*.*"), *result_dir.glob("verifier-turn-*.txt")]:
        stale.unlink()
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for name in TASKS[task_id]["output_files"]:
        if (workspace / name).is_file():
            shutil.copy2(workspace / name, result_dir / name)

    usage = rollout_usage_summary(parse_model_io_rollout(
        model_io_rollout_path(storage, session_id)
    )) if session_id and model_io_rollout_path(storage, session_id).is_file() else {
        "requests": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "input_plus_output_tokens": 0,
    }
    # agent 净时间 = 全 session 请求时长和（逐回合累加会重复计数，回合末算一次）
    agent_net_seconds = rollout_agent_seconds(storage, session_id) if session_id else 0.0
    # 干净闭环：披露后下一轮有技能读取 + gap ≤ 1
    disclosure_gap = (
        pass_turn - last_disclosure_turn
        if final_pass and last_disclosure_turn is not None else None
    )
    clean_closure = int(
        condition == "vista" and final_pass and disclosure_count > 0
        and access_after_last_disclosure and disclosure_gap is not None
        and disclosure_gap <= 1
    )
    quarantined = stop_reason in {
        "provider_policy_rejection", "api_error", "session_identity_changed",
        "verifier_infrastructure_error", "model_turn_timeout",
    }
    status = (
        "passed_with_clean_vista_feedback_loop" if clean_closure
        else "passed" if final_pass and condition != "vista"
        else "passed_without_clean_vista_feedback_loop" if final_pass
        else f"quarantined_{stop_reason}" if quarantined
        else f"failed_{stop_reason}"
    )
    result = {
        "schema_version": 1,
        "run_id": f"{result_root(task_id).name}-{condition}-replicate-{replicate:02d}",
        "task_id": task_id, "condition": condition, "replicate_id": replicate,
        "backend": "zcode-local-minimal-dependencies", "docker_used": False,
        "protocol": "sj4",
        "requested_provider_model": model, "session_id": session_id,
        "turns": turns, "usage": usage,
        "skill_reads": all_skill_reads,
        "skill_read_tokens": sum(read["file_tokens"] for read in all_skill_reads),
        "skill_unique_file_tokens": sum(
            {read["file"]: read["file_tokens"] for read in all_skill_reads}.values()
        ),
        "skill_read_count": len(all_skill_reads),
        "agent_net_seconds": round(agent_net_seconds, 3),
        "verifier_seconds": round(verifier_seconds, 3),
        "initially_available_modules": (
            ["modules/<module-id>/SKILL.md"] if condition == "flat"
            else ["skill/SKILL.md"] if condition == "pd" else []
        ),
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


def archive_partial_base(task_id: str, condition: str, replicate: int) -> None:
    """被中断 episode 的半成品目录（有 turn-*.jsonl 但无 result.json）归档旁置，
    避免 prepare_trial 的 refuse-to-reuse 保护误报；证据不删除。"""

    base_name = f"{result_root(task_id).name}-{condition}-replicate-{replicate:02d}"
    base = LOCAL_ROOT / base_name
    if base.exists() and any(base.glob("turn-*.jsonl")) and not (
        base / "result.json"
    ).is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = LOCAL_ROOT / f"{base_name}-abandoned-{stamp}"
        base.rename(archived)
        print(f"archived partial trial: {base_name} -> {archived.name}", flush=True)


def run_group(task_id: str, *, model: str, provider: str, workers: int) -> int:
    readiness = dependency_readiness(task_id)
    if not readiness["ready"]:
        raise RuntimeError(
            f"{task_id} local dependencies are not ready: {json.dumps(readiness, ensure_ascii=False)}"
        )
    root = result_root(task_id)
    if not root.exists():
        root.mkdir(parents=True)
        (root / "readiness.json").write_text(
            json.dumps({"ready": readiness, "provider": provider, "model": model},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    schedule = tuple((rep, condition) for rep in REPLICATES for condition in CONDITIONS)
    results: dict[tuple[int, str], dict[str, Any]] = {}
    pending: list[tuple[int, str]] = []
    for replicate, condition in schedule:
        existing = root / condition / f"replicate-{replicate:02d}" / "result.json"
        if existing.is_file():
            results[(replicate, condition)] = json.loads(existing.read_text(encoding="utf-8"))
            continue
        archive_partial_base(task_id, condition, replicate)
        pending.append((replicate, condition))
    if pending:
        print(
            f"running {len(pending)} pending trials with {workers} workers "
            f"(task={task_id}, provider={provider})", flush=True,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(run_episode, task_id, condition, replicate, model=model):
                    (replicate, condition)
                for replicate, condition in pending
            }
            for future in as_completed(futures):
                key = futures[future]
                results[key] = future.result()
                print(f"trial done: {results[key]['run_id']} status={results[key]['status']}", flush=True)
    ordered = [results[(rep, cond)] for rep, cond in schedule]

    condition_summaries = {}
    for condition in CONDITIONS:
        values = [r for r in ordered if r["condition"] == condition]
        condition_summaries[condition] = {
            "planned_trials": len(REPLICATES), "completed_trials": len(values),
            "first_turn_passes": sum(r["vista_metrics"]["first_turn_pass"] for r in values),
            "final_verifier_passes": sum(r["final_verifier_pass"] for r in values),
            "agent_net_seconds": round(sum(r["agent_net_seconds"] for r in values), 3),
            "verifier_seconds": round(sum(r["verifier_seconds"] for r in values), 3),
            "usage": {
                key: sum(r["usage"].get(key, 0) for r in values)
                for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens")
            },
            "skill_read_tokens": sum(r["skill_read_tokens"] for r in values),
        }
    summary = {
        "schema_version": 1,
        "protocol": (
            f"{task_id}-sj4-x3-glm53-"
            "single-episode-for-noskill-flat-pd/multi-turn-feedback-vista-only"
        ),
        "task_id": task_id,
        "benchmark_task_id": TASKS[task_id]["benchmark"],
        "conditions": list(CONDITIONS),
        "replicates_per_condition": len(REPLICATES),
        "planned_trials": len(schedule), "completed_trials": len(ordered),
        "requested_provider_model": model,
        "provider_id": provider,
        "construction_model": "glm53-flash",
        "backend": "zcode-local-minimal-dependencies",
        "condition_summaries": condition_summaries,
        "results": [
            {
                "condition": r["condition"], "replicate_id": r["replicate_id"],
                "status": r["status"], "final_verifier_pass": r["final_verifier_pass"],
                "session_id": r["session_id"], "usage": r["usage"],
                "skill_read_tokens": r["skill_read_tokens"],
                "agent_net_seconds": r["agent_net_seconds"],
                "verifier_seconds": r["verifier_seconds"],
                "disclosed_modules": r["disclosed_modules"],
                "vista_metrics": r["vista_metrics"],
            }
                for r in ordered
            ],
    }
    (root / "group-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if len(ordered) == len(schedule) else 1


def dependency_readiness(task_id: str) -> dict[str, Any]:
    source, environment = task_paths(task_id)
    spec = TASKS[task_id]
    checks: dict[str, Any] = {
        "verifier_python": VERIFIER_PYTHON.is_file(),
        "task_md": (source / "task.md").is_file(),
        "verifier_source": (source / "verifier" / "test_outputs.py").is_file(),
        "inputs_present": {name: (environment / name).exists() for name in spec["inputs"]},
        "flat_artifacts": sorted(
            p.parent.name for p in (ARTIFACT_ROOT / task_id / "flat").glob("*/SKILL.md")
        ),
        "pd_artifact": (ARTIFACT_ROOT / task_id / "pd" / "SKILL.md").is_file(),
        "pd_references": sorted(
            p.name for p in (ARTIFACT_ROOT / task_id / "pd" / "references").glob("*.md")
        ),
        "sj4_manifest": (ARTIFACT_ROOT / task_id / "manifest.yaml").is_file(),
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
        if key not in {"flat_artifacts", "pd_references"}
    ) and bool(checks["flat_artifacts"]) and bool(checks["pd_references"])
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--provider", choices=(PROVIDER_V1, PROVIDER_V3), default=PROVIDER_V1,
                        help="v1 chat-completions (host default config) or v3 anthropic "
                             "endpoint via corrected provider config")
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent episodes per endpoint (1-6)")
    parser.add_argument("--dry-run", action="store_true",
                        help="run readiness and preflight gates only, no model call")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 6:
        raise SystemExit("--workers must be between 1 and 6")

    os.environ.pop("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", None)
    os.environ.pop("VISTA_ZCODE_PROVIDER_CONFIG", None)
    if args.provider == PROVIDER_V3:
        if not PROVIDER_CONFIG_V3.is_file():
            raise SystemExit(f"corrected v3 provider config is missing: {PROVIDER_CONFIG_V3}")
        os.environ["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(PROVIDER_CONFIG_V3)
        os.environ["VISTA_ZCODE_PROVIDER_CONFIG"] = str(PROVIDER_CONFIG_V3)
        expected_provider = PROVIDER_V3
    else:
        expected_provider = PROVIDER_V1
    model_config = current_zcode_model_config(provider_id=expected_provider)
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
    print(
        f"zcode launcher ok: {launcher.version} | provider: {model_config['provider_id']} "
        f"| model: {model_config['model_id']} | config: {model_config['config_path']}",
        flush=True,
    )
    if args.dry_run:
        readiness = dependency_readiness(args.task)
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0 if readiness["ready"] else 1
    return run_group(args.task, model=model_config["model_id"], provider=args.provider,
                     workers=args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
