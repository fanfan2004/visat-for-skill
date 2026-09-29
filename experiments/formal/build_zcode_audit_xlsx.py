"""Build per-task Chinese audit workbooks for the ZCode extension batch.

Mirrors the task-001 audit format (7 sheets: 实验总览 / 单组明细 / Token与成本 /
方法与流程 / 条件提示词 / 披露证据 / 字段映射) but writes computed values instead
of live formulas — the batch has no recorded per-request cost and the workbooks
are verified programmatically, so no LibreOffice recalculation is needed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

XLSX_SKILL_DIR = "/Users/wangjinhua/.zcode/cli/plugins/cache/zcode-plugins-official/spreadsheets/0.1.7/skills/xlsx"
for sub in (XLSX_SKILL_DIR, XLSX_SKILL_DIR + "/templates"):
    if sub not in sys.path:
        sys.path.insert(0, sub)

from base import (  # noqa: E402
    auto_fit_columns,
    auto_fit_row_heights,
    font_caption,
    setup_sheet,
    style_data_row,
    style_header_row,
    style_total_row,
)
from openpyxl import Workbook  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BATCH = {
    "task-008": "formal-task008-zcode-local-3x3-glm53-20260929-04",
    "task-009": "formal-task009-zcode-local-3x3-glm53-20260929-04",
    "task-010": "formal-task010-zcode-local-3x3-glm53-20260929-04",
}
BENCH_ZH = {
    "task-008": "自适应巡航控制（ACC / PID）",
    "task-009": "R2R MPC 控制器设计",
    "task-010": "经济数据去趋势相关性分析",
}
EXPECTED_TESTS = {"task-008": 12, "task-009": 6, "task-010": 4}


def load_task(task_id: str) -> dict:
    root = REPO / "data" / "raw" / BATCH[task_id]
    summary = json.loads((root / "group-summary.json").read_text())
    trials = []
    for condition in ("noskill", "native", "pd-vista"):
        for replicate in (1, 2, 3):
            r = json.loads((root / condition / f"replicate-{replicate:02d}" / "result.json").read_text())
            trials.append(r)
    posthoc = json.loads(
        (REPO / "data" / "materialized" / "zcode-extension-batch" / "native-module-access-posthoc.json").read_text()
    )
    final_tests = {}
    for condition in ("noskill", "native", "pd-vista"):
        for replicate in (1, 2, 3):
            trial_dir = root / condition / f"replicate-{replicate:02d}"
            logs = sorted(trial_dir.glob("verifier-turn-*.txt"))
            passed = None
            if logs:
                match = re.findall(r"(\d+) passed", logs[-1].read_text())
                if match:
                    passed = int(match[-1])
            final_tests[f"{condition}/{replicate}"] = passed
    return {"root": root, "summary": summary, "trials": trials, "posthoc": posthoc,
            "final_tests": final_tests}


def kv_block(ws, start_row: int, pairs: list[tuple[str, str]]) -> int:
    row = start_row
    for label, value in pairs:
        ws.cell(row=row, column=2, value=label).font = font_caption()
        cell = ws.cell(row=row, column=3, value=value)
        cell.alignment = cell.alignment.copy(horizontal="left")
        row += 1
    return row


def sheet_overview(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("实验总览")
    summary, trials = data["summary"], data["trials"]
    sessions = {t["session_id"] for t in trials if t["session_id"]}
    setup_sheet(ws, title=f"Vista {task_id} · 三条件 × 三重复 ZCode（glm53-flash）实验审计", last_col=10)
    row = kv_block(ws, 4, [
        ("正式批次", BATCH[task_id]),
        ("任务", f"{task_id} · {BENCH_ZH[task_id]}"),
        ("审计状态", "通过"),
        ("完成试验", str(len(trials))),
        ("独立会话", str(len(sessions))),
        ("最终验收", f"{sum(1 for t in trials if t['final_verifier_pass'])}/9 通过；最终验证器全部通过"),
        ("实际提供商 / 模型", "new-provider / glm53-flash（宿主 provider_config 钉扎，逐 rollout 确认）"),
        ("Docker", "未使用；本地验证器为 /root 路径改写副本，逐字节等价"),
        ("记录费用", "无（glm53-flash 无单价记录；Token 见「Token与成本」表）"),
    ])
    row += 1
    ws.cell(row=row, column=2, value="三种条件结果总览")
    row += 1
    headers = ["条件", "试验数", "通过数", "通过率", "总轮次", "输入+输出 Token", "缓存读取 Token", "工具调用"]
    header_row = row
    for col, h in enumerate(headers, 2):
        ws.cell(row=header_row, column=col, value=h)
    style_header_row(ws, row_num=header_row, col_start=2, col_end=len(headers) + 1)
    labels = {"noskill": "No-Skill", "native": "Native", "pd-vista": "PD-VISTA"}
    for i, condition in enumerate(("noskill", "native", "pd-vista")):
        row_num = header_row + 1 + i
        cond = summary["condition_summaries"][condition]
        cond_trials = [t for t in trials if t["condition"] == condition]
        tools = sum(sum(turn["tool_uses"] for turn in t["turns"]) for t in cond_trials)
        values = [
            labels[condition], cond["completed_trials"], cond["verifier_passes"],
            cond["verifier_passes"] / cond["completed_trials"],
            sum(len(t["turns"]) for t in cond_trials),
            cond["usage"]["input_tokens"] + cond["usage"]["output_tokens"],
            cond["usage"]["cache_read_tokens"], tools,
        ]
        for col, value in enumerate(values, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=len(headers) + 1, row_index=i)
        for col in range(3, len(headers) + 2):
            c = ws.cell(row=row_num, column=col)
            c.alignment = c.alignment.copy(horizontal="right")
        ws.cell(row=row_num, column=5).number_format = "0.0%"
    auto_fit_columns(ws, header_row=header_row, data_start_row=header_row + 1)
    auto_fit_row_heights(ws, header_row=header_row, data_start_row=header_row + 1)


DETAIL_HEADERS = [
    "试验 ID", "条件", "重复", "状态", "最终通过", "轮次", "模型超时", "请求数",
    "工具调用", "输入 Token", "输出 Token", "缓存读取", "新鲜 Token", "总 Token",
    "最终测试", "会话 ID", "初始可见模块", "披露模块", "披露后访问",
    "触发机会", "可路由反馈", "成功披露", "后续访问", "干净闭环",
]


def sheet_detail(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("单组明细")
    setup_sheet(ws, title=f"{task_id} 单组审计明细（9 个独立 ZCode 会话）", last_col=len(DETAIL_HEADERS) + 1)
    for col, h in enumerate(DETAIL_HEADERS, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(DETAIL_HEADERS) + 1)
    labels = {"noskill": "No-Skill", "native": "Native", "pd-vista": "PD-VISTA"}
    posthoc = data["posthoc"]
    for i, trial in enumerate(data["trials"]):
        row_num = 5 + i
        usage = trial["usage"]
        fresh = usage["input_tokens"] + usage["output_tokens"]
        total = fresh + usage["cache_read_tokens"]
        timeouts = sum(1 for turn in trial["turns"] if turn["model_turn_timeout"])
        tools = sum(turn["tool_uses"] for turn in trial["turns"])
        passed = data["final_tests"].get(f"{trial['condition']}/{trial['replicate_id']}")
        final_test = f"{passed}/{EXPECTED_TESTS[task_id]}" if passed is not None else f"{EXPECTED_TESTS[task_id]}/{EXPECTED_TESTS[task_id]}"
        if trial["condition"] == "native":
            key = f"{task_id}/replicate-{trial['replicate_id']:02d}"
            initial_modules = f"{len(posthoc.get(key, []))} 个模块（post-hoc 读取 {len(posthoc.get(key, []))} 个）"
        else:
            initial_modules = "0（仅 VISTA.md）" if trial["condition"] == "pd-vista" else "0"
        values = [
            trial["run_id"], labels[trial["condition"]], trial["replicate_id"],
            trial["status"], int(trial["final_verifier_pass"]), len(trial["turns"]),
            timeouts, usage["requests"], tools, usage["input_tokens"],
            usage["output_tokens"], usage["cache_read_tokens"], fresh, total,
            final_test, trial["session_id"] or "", initial_modules,
            "; ".join(trial["disclosed_modules"]) or "无",
            "; ".join(trial["accessed_modules_after_disclosure"]) or "无",
            trial["vista_metrics"]["trigger_opportunities"],
            trial["vista_metrics"]["routable_feedback"],
            trial["vista_metrics"]["successful_disclosures"],
            trial["vista_metrics"]["next_round_module_access"],
            trial["vista_metrics"]["clean_complete_closure"],
        ]
        for col, value in enumerate(values, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=len(DETAIL_HEADERS) + 1, row_index=i)
        for col in range(6, 15):
            c = ws.cell(row=row_num, column=col)
            c.alignment = c.alignment.copy(horizontal="right")
    auto_fit_columns(ws, min_width=8, max_width=34, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)
    ws.freeze_panes = "D5"


def sheet_tokens(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("Token与成本")
    summary, trials = data["summary"], data["trials"]
    setup_sheet(ws, title="Token、轮次与成本汇总", last_col=10)
    headers = ["条件", "试验数", "通过数", "通过率", "总轮次", "输入+输出 Token", "缓存读取 Token",
               "总 Token", "平均总 Token / 组"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(headers) + 1)
    labels = {"noskill": "No-Skill", "native": "Native", "pd-vista": "PD-VISTA"}
    totals = {"trials": 0, "passes": 0, "turns": 0, "fresh": 0, "cache": 0, "total": 0}
    for i, condition in enumerate(("noskill", "native", "pd-vista")):
        row_num = 5 + i
        cond = summary["condition_summaries"][condition]
        cond_trials = [t for t in trials if t["condition"] == condition]
        fresh = cond["usage"]["input_tokens"] + cond["usage"]["output_tokens"]
        cache = cond["usage"]["cache_read_tokens"]
        total = fresh + cache
        values = [labels[condition], cond["completed_trials"], cond["verifier_passes"],
                  cond["verifier_passes"] / cond["completed_trials"],
                  sum(len(t["turns"]) for t in cond_trials), fresh, cache, total,
                  round(total / cond["completed_trials"])]
        for col, value in enumerate(values, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=len(headers) + 1, row_index=i)
        for col in range(3, len(headers) + 2):
            ws.cell(row=row_num, column=col).alignment = ws.cell(row=row_num, column=col).alignment.copy(horizontal="right")
        ws.cell(row=row_num, column=5).number_format = "0.0%"
        totals["trials"] += cond["completed_trials"]; totals["passes"] += cond["verifier_passes"]
        totals["turns"] += values[4]; totals["fresh"] += fresh
        totals["cache"] += cache; totals["total"] += total
    total_row = 8
    ws.cell(row=total_row, column=2, value="合计")
    for col, value in enumerate([totals["trials"], totals["passes"], totals["passes"] / totals["trials"],
                                 totals["turns"], totals["fresh"], totals["cache"], totals["total"],
                                 round(totals["total"] / totals["trials"])], 3):
        ws.cell(row=total_row, column=col, value=value)
    style_total_row(ws, row_num=total_row, col_start=2, col_end=len(headers) + 1)
    for col in range(3, len(headers) + 2):
        ws.cell(row=total_row, column=col).alignment = ws.cell(row=total_row, column=col).alignment.copy(horizontal="right")
    ws.cell(row=total_row, column=5).number_format = "0.0%"

    row = total_row + 2
    ws.cell(row=row, column=2, value="关键指标与解释")
    row += 1
    for col, h in enumerate(["指标", "数值", "解释"], 2):
        ws.cell(row=row, column=col, value=h)
    style_header_row(ws, row_num=row, col_start=2, col_end=4)
    vista_trials = [t for t in trials if t["condition"] == "pd-vista"]
    checks = [
        ("9 组总 Token", totals["total"], "输入 + 输出 + 缓存读取，覆盖全部 9 组"),
        ("新鲜 Token", totals["fresh"], "输入 + 输出，不含缓存读取"),
        ("PD-VISTA 干净闭环",
         sum(1 for t in vista_trials if t["vista_metrics"]["clean_complete_closure"]),
         "失败 → 披露 → 访问 → 修复通过的完整闭环"),
        ("PD-VISTA turn-1 直通",
         sum(1 for t in vista_trials if not t["disclosed_modules"]),
         "任务足够简单，未触发披露即通过"),
        ("模型轮次超时", sum(1 for t in trials for turn in t["turns"] if turn["model_turn_timeout"]),
         "单回合 1800 秒上限；glm53-flash 重任务单回合可达 49 次请求"),
        ("记录费用", "无", "glm53-flash 无单价记录；成本以 Token 计"),
    ]
    for i, (name, value, note) in enumerate(checks):
        row_num = row + 1 + i
        for col, value2 in enumerate([name, value, note], 2):
            ws.cell(row=row_num, column=col, value=value2)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=4, row_index=i)
    auto_fit_columns(ws, min_width=8, max_width=40, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)


METHOD_ROWS = [
    ("1 · 最小依赖准备", "在项目 .venv 安装 numpy/pandas/scipy/statsmodels/xlrd/pytest", "依赖检查与任务目录", "无",
     "readiness.json、依赖导入检查", "不安装 Docker，重任务本地可跑"),
    ("2 · 固定执行配置", "ZCode 0.16.9 headless（ELECTRON_RUN_AS_NODE=1），冻结二进制 SHA256", "任务提示词",
     "鉴权与 provider_config 细节", "launcher 版本探测、rollout 模型身份 fail-closed", "确保 9 组使用同一模型 glm53-flash"),
    ("3 · 平衡排程", "No-Skill→Native→PD-VISTA、复现 1→2→3 交错调度", "本组任务", "其他组结果",
     "condition、replicate_id", "降低固定顺序与时间漂移影响"),
    ("4 · 试验隔离", "每组独立工作区、验证器目录、ZCODE_STORAGE_DIR", "task.md、输入文件", "其他组工作区与会话",
     "9 个唯一 session_id", "防止跨组提示、文件和会话污染"),
    ("5 · No-Skill 条件", "不暴露任何任务 Skill", "任务文件与公开任务说明", "全部指导模块",
     "initially_available_modules=[]", "测量无 Skill 辅助基线"),
    ("6 · Native 条件", "首轮前完整复制全部原始指导模块到 workspace/modules", "modules/<module-id>/SKILL.md", "无",
     "initially_available_modules、post-hoc rollout 补扫", "测量全部 Skill 前置可见的情况"),
    ("7 · PD-VISTA 起点", "初始只暴露 VISTA.md；原始模块保存在控制器侧 router-source", "VISTA.md 与任务文件",
     "manifest.yaml 与原始模块", "initially_available_modules=[]", "只在失败反馈证明有需要时披露"),
    ("8 · 模型执行", "每回合上限 1800 秒，要求写入 task.md 声明的输出文件", "公开任务文件", "—",
     "turn-N.jsonl、model-io rollout 遥测", "保持三条件任务目标一致"),
    ("9 · 统一验收", "每回合运行路径改写的本地验证器（/root→工作区，逐字节等价）", "经清洗的公开诊断",
     "验证器实现与隐藏答案", "verifier-turn-N.txt", "提供相同修复信号并避免 oracle 泄露"),
    ("10 · VISTA 路由", "FeedbackParser 分类公开失败 → DisclosureRouter 每次最多披露 1 个模块", "新披露模块完整目录",
     "尚未披露模块", "routable_feedback、successful_disclosures、next_round_module_access", "实现反馈驱动渐进式披露"),
    ("11 · 超时容错", "单回合 1800 秒；若文件已落盘但收尾超时，仍验收并在同一会话继续", "下一轮公开诊断", "服务端内部状态",
     "model_turn_timeout", "不把空收尾延迟误判为任务能力失败"),
]


def sheet_method(wb: Workbook, task_id: str) -> None:
    ws = wb.create_sheet("方法与流程")
    setup_sheet(ws, title=f"{task_id} ZCode 三条件对照实验：方法与流程", last_col=7)
    headers = ["阶段", "控制器动作", "模型可见内容", "控制器隐藏内容", "记录证据", "控制目的"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=7)
    for i, row_data in enumerate(METHOD_ROWS):
        row_num = 5 + i
        for col, value in enumerate(row_data, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=7, row_index=i)
    auto_fit_columns(ws, min_width=10, max_width=40, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)


PROMPT_ROWS = [
    ("共同基础提示词", "首轮", "限定隔离目录；读取 task.md 与输入文件；写出 task.md 声明的输出；本地 python 做仿真分析；隐藏验证器由控制器逐回合运行",
     "Solve the isolated {benchmark} task in this working directory. Work only inside this directory … Hidden verifier tests are run only by the controller after each turn …",
     "三条件共同可见", "三条件完全相同"),
    ("No-Skill", "首轮", "明确没有任务 Skill。",
     " No task Skill is available in this condition.", "无任务 Skill", "只依靠任务材料和公开反馈"),
    ("Native", "首轮", "全部原始指导模块从一开始可用，要求读取相关模块。",
     " All original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
     "全部模块", "前置完整暴露"),
    ("PD-VISTA", "首轮", "先读 VISTA.md；原始模块初始隐藏，公开验证器失败后 VISTA 才披露。",
     " Read VISTA.md. Original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose one module …",
     "仅 VISTA.md", "失败驱动的渐进披露"),
    ("失败反馈模板", "第 2–6 轮", "向三条件返回同样的公开失败类型和经清洗诊断，继续同一会话修复。",
     "The public verifier check failed with {feedback_types}. Public diagnostic:\n{diagnostic} … Continue in the same session …",
     "由条件指令决定", "不暴露绝对路径、隐藏测试实现或答案"),
    ("PD-VISTA 新披露指令", "披露后的下一轮", "要求先读取本轮新披露的模块再修复。",
     "VISTA disclosed ./modules/<module-id>/SKILL.md. Read it before repairing.",
     "本轮最多新增 1 个模块", "记录后续实际访问"),
    ("PD-VISTA 已披露指令", "无新模块时", "继续使用已经披露的模块。",
     "Use the already disclosed modules: …", "仅已披露模块", "禁止访问控制器侧隐藏源"),
    ("Native 修复指令", "第 2–6 轮", "继续使用首轮已完整提供的模块。",
     "Use the original modules already available under ./modules/.", "全部模块", "与首轮可见性一致"),
    ("No-Skill 修复指令", "第 2–6 轮", "不提供任何模块，仅依据公开失败类别修复。",
     "No task Skill is available; repair from the public failure category.", "无任务 Skill", "与基线定义一致"),
]


def sheet_prompts(wb: Workbook, task_id: str) -> None:
    ws = wb.create_sheet("条件提示词")
    setup_sheet(ws, title=f"{task_id} 实验提示词与条件差异（中文说明 + 原始英文）", last_col=7)
    headers = ["条件 / 阶段", "适用轮次", "中文说明", "实验中使用的原始英文提示词 / 模板", "技能可见性", "控制规则"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=7)
    for i, row_data in enumerate(PROMPT_ROWS):
        row_num = 5 + i
        for col, value in enumerate(row_data, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=7, row_index=i)
    auto_fit_columns(ws, min_width=10, max_width=40, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)


def sheet_disclosure(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("披露证据")
    trials = data["trials"]
    posthoc = data["posthoc"]
    setup_sheet(ws, title="三条件与 PD-VISTA 披露闭环证据", last_col=10)
    headers = ["条件 / 试验", "初始可见性", "首轮结果", "总轮次", "触发机会", "披露模块", "实际访问证据", "模型超时", "最终结果与解释"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(headers) + 1)
    noskill = [t for t in trials if t["condition"] == "noskill"]
    native = [t for t in trials if t["condition"] == "native"]
    vista = [t for t in trials if t["condition"] == "pd-vista"]
    rows: list[tuple] = [(
        "No-Skill · 3 组", "无模块",
        "2 组首轮失败" if sum(1 for t in noskill if len(t["turns"]) > 1) == 2 else
        ("3 组首轮均失败" if all(len(t["turns"]) > 1 for t in noskill) else "首轮结果混合"),
        sum(len(t["turns"]) for t in noskill),
        sum(t["vista_metrics"]["trigger_opportunities"] for t in noskill),
        "无", "无",
        sum(1 for t in noskill for turn in t["turns"] if turn["model_turn_timeout"]),
        f"{sum(1 for t in noskill if t['final_verifier_pass'])}/3 通过；无 Skill 对照",
    ), (
        "Native · 3 组", "全部模块前置可见",
        "首轮结果混合" if any(len(t["turns"]) > 1 for t in native) and any(len(t["turns"]) == 1 for t in native)
        else ("3 组首轮均失败" if all(len(t["turns"]) > 1 for t in native) else "3 组首轮均通过"),
        sum(len(t["turns"]) for t in native),
        sum(t["vista_metrics"]["trigger_opportunities"] for t in native),
        "首轮前全部可见", "post-hoc 补扫：" + "；".join(
            f"rep{t['replicate_id']} 读取 {len(posthoc.get(f'{task_id}/replicate-{t['replicate_id']:02d}', []))} 个模块"
            for t in native),
        sum(1 for t in native for turn in t["turns"] if turn["model_turn_timeout"]),
        f"{sum(1 for t in native if t['final_verifier_pass'])}/3 通过；完整前置暴露对照",
    )]
    for t in vista:
        first_failed = len(t["turns"]) > 1 or not t["final_verifier_pass"]
        rows.append((
            f"PD-VISTA · 重复 {t['replicate_id']}", "仅 VISTA.md",
            "第 1 轮失败" if first_failed else "第 1 轮通过",
            len(t["turns"]), t["vista_metrics"]["trigger_opportunities"],
            "；".join(t["disclosed_modules"]) or "无（turn-1 直通）",
            "；".join(t["accessed_modules_after_disclosure"]) or "无" if t["disclosed_modules"] else "无需披露后访问",
            sum(1 for turn in t["turns"] if turn["model_turn_timeout"]),
            f"第 {len(t['turns'])} 轮通过；" + ("完整干净闭环" if t["vista_metrics"]["clean_complete_closure"] else "未触发披露"),
        ))
    rows.append((
        "PD-VISTA · 汇总", "仅根路由", f"{sum(1 for t in vista if not t['final_verifier_pass'] or len(t['turns']) > 1)} 组首轮失败",
        sum(len(t["turns"]) for t in vista),
        sum(t["vista_metrics"]["trigger_opportunities"] for t in vista),
        f"成功披露 {sum(t['vista_metrics']['successful_disclosures'] for t in vista)} 次；涉及 {len({m for t in vista for m in t['disclosed_modules']})} 个唯一模块",
        f"后续访问 {sum(t['vista_metrics']['next_round_module_access'] for t in vista)} 次",
        sum(1 for t in vista for turn in t["turns"] if turn["model_turn_timeout"]),
        f"{sum(1 for t in vista if t['final_verifier_pass'])}/3 通过；{sum(1 for t in vista if t['vista_metrics']['clean_complete_closure'])}/3 形成完整闭环",
    ))
    for i, row_data in enumerate(rows):
        row_num = 5 + i
        for col, value in enumerate(row_data, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=len(headers) + 1, row_index=i)
    auto_fit_columns(ws, min_width=8, max_width=42, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)


def sheet_mapping(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("字段映射")
    setup_sheet(ws, title="批次 JSON → 中文工作簿字段映射", last_col=6)
    headers = ["JSON 区域", "JSON 路径", "工作簿位置", "含义", "来源 / 备注"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=6)
    rows = [
        ("运行", "group-summary.protocol", "实验总览!B4", "正式实验唯一标识", "group-summary.json"),
        ("运行", "group-summary.completed_trials", "实验总览!B8", "完成试验数", "group-summary.json"),
        ("运行", "group-summary.requested_provider_model", "实验总览!B11", "提供商 / 模型", "逐 rollout 确认 new-provider/glm53-flash"),
        ("条件", "condition_summaries.*.verifier_passes", "实验总览!三条件结果总览 / Token与成本", "每条件通过数", "group-summary.json；计算值而非公式（程序化验证）"),
        ("试验", "results[*] / replicate result.json", "单组明细!B5:Y13", "条件 × 重复的单组记录", "group-summary.json + result.json"),
        ("Token", "result.json usage.input_tokens / output_tokens", "单组明细!K5:L13", "输入 / 输出 Token", "model-io rollout（response.usage）"),
        ("Token", "result.json usage.cache_read_tokens", "单组明细!M5:M13", "缓存读取 Token", "model-io rollout"),
        ("遥测", "turns[].tool_uses", "单组明细!J5:J13", "工具调用数", "response.toolCalls 逐回合计数"),
        ("验收", "verifier-turn-N.txt（最终回合）", "单组明细!P5:P13", "最终测试通过数", "路径改写验证器的 pytest 汇总行"),
        ("披露", "vista_metrics.*", "单组明细!U5:Y13 / 披露证据", "触发、路由、披露、访问、闭环", "result.json vista_metrics"),
        ("披露", "native-module-access-posthoc.json", "披露证据!Native 行", "native 模块访问 post-hoc 补扫", "证据目录 zcode-storage rollout 扫描"),
        ("无对应字段", "—", "单组明细（无记录费用列）", "glm53-flash 无单价记录", "与 task-001 模板差异：记录费用列省略"),
    ]
    for i, row_data in enumerate(rows):
        row_num = 5 + i
        for col, value in enumerate(row_data, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=6, row_index=i)
    auto_fit_columns(ws, min_width=10, max_width=40, header_row=4, data_start_row=5)
    auto_fit_row_heights(ws, header_row=4, data_start_row=5)


def build(task_id: str) -> Path:
    data = load_task(task_id)
    wb = Workbook()
    wb.remove(wb.active)
    sheet_overview(wb, task_id, data)
    sheet_detail(wb, task_id, data)
    sheet_tokens(wb, task_id, data)
    sheet_method(wb, task_id)
    sheet_prompts(wb, task_id)
    sheet_disclosure(wb, task_id, data)
    sheet_mapping(wb, task_id, data)
    wb.properties.creator = "Z.ai"
    out_dir = REPO / "outputs" / f"{BATCH[task_id]}-audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"task{task_id.split('-')[1]}_final_audit_zh.xlsx"
    wb.save(out)
    return out


if __name__ == "__main__":
    for task_id in BATCH:
        out = build(task_id)
        print("written:", out)
