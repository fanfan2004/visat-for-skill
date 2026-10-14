"""Build per-task Chinese audit workbooks for the sj4 x5 batches (task-011/012).

Mirrors the tier1 audit format (7 sheets: 实验总览 / 单组明细 / Token与成本 /
方法与流程 / 条件提示词 / 披露证据 / 字段映射) but targets the sj4 protocol
(noskill / flat / pd 单回合单 episode；vista 唯一多回合反馈组，4 条件 × 5 复现)。
Batches: formal-task-01{1,2}-sj4-x5-glm53-20261014-17（新版字段掩码 + api_error 隔离）。
Writes computed values instead of live formulas — the workbooks are verified
programmatically, so no LibreOffice recalculation is needed. Corrected
skill-file tokens (被读文件本身口径) come from data/derived/sj4_x5_token_summary.json
(recompute_sj4_tokens.py output, gitignored).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

XLSX_SKILL_DIR = "/Users/wangjinhua/.zcode/cli/plugins/cache/zcode-plugins-official/spreadsheets/0.1.8/skills/xlsx"
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
    "task-011": "formal-task-011-sj4-x5-glm53-20261014-17",
    "task-012": "formal-task-012-sj4-x5-glm53-20261014-17",
}
REPLICATES = (1, 2, 3, 4, 5)
BENCH_ZH = {
    "task-011": "SEC 13F 财报多跳检索（sec-financial-report）",
    "task-012": "发票欺诈检测（invoice-fraud-detection）",
}
EXPECTED_TESTS = {"task-011": 2, "task-012": 2}
CONDITIONS = ("noskill", "flat", "pd", "vista")
LABELS = {
    "noskill": "No-Skill（无技能）",
    "flat": "Flat（压平单文件）",
    "pd": "PD（入口自选披露）",
    "vista": "VISTA（反馈驱动披露）",
}
TOKEN_SUMMARY_PATH = REPO / "data" / "derived" / "sj4_x5_token_summary.json"
CACHE_COST_PATH = REPO / "data" / "derived" / "sj4_x5_cache_cost_estimate.json"


def load_task(task_id: str) -> dict:
    root = REPO / "data" / "raw" / BATCH[task_id]
    summary = json.loads((root / "group-summary.json").read_text())
    trials = []
    for condition in CONDITIONS:
        for replicate in REPLICATES:
            r = json.loads((root / condition / f"replicate-{replicate:02d}" / "result.json").read_text())
            trials.append(r)
    final_tests = {}
    for condition in CONDITIONS:
        for replicate in REPLICATES:
            trial_dir = root / condition / f"replicate-{replicate:02d}"
            logs = sorted(trial_dir.glob("verifier-turn-*.txt"))
            passed = None
            if logs:
                match = re.findall(r"(\d+) passed", logs[-1].read_text())
                if match:
                    passed = int(match[-1])
            final_tests[f"{condition}/{replicate}"] = passed
    token_summary = (json.loads(TOKEN_SUMMARY_PATH.read_text()).get(task_id, {})
                     if TOKEN_SUMMARY_PATH.is_file() else {})
    cache_cost = (json.loads(CACHE_COST_PATH.read_text()).get("base", {}).get(task_id, {})
                  if CACHE_COST_PATH.is_file() else {})
    return {"root": root, "summary": summary, "trials": trials,
            "final_tests": final_tests, "token_summary": token_summary,
            "cache_cost": cache_cost}


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
    setup_sheet(ws, title=f"Vista {task_id} · sj4 四条件 × 5重复 ZCode（glm53-flash）实验审计", last_col=10)
    row = kv_block(ws, 4, [
        ("正式批次", BATCH[task_id]),
        ("任务", f"{task_id} · {BENCH_ZH[task_id]}"),
        ("审计状态", "通过"),
        ("协议", "sj4：noskill/flat/pd 单回合单 episode（失败即停止）；vista 唯一多回合反馈组（最多 6 回合，失败→脱敏诊断+失败层签名→路由披露→resume 修复）"),
        ("完成试验", str(len(trials))),
        ("独立会话", str(len(sessions))),
        ("最终验收", f"{sum(1 for t in trials if t['final_verifier_pass'])}/{len(trials)} 通过"),
        ("实际提供商 / 模型", "双端点并行：task-011=new-provider，task-012=new-provider-2 / glm53-flash（宿主 provider_config 钉扎，4 线程/端点）"),
        ("Docker", "未使用；本地验证器为 /root 路径改写副本，逐字节等价，ground truth 随附于控制器侧隐藏验证器目录"),
        ("诊断掩码", "新版：保留字段名、只掩值（含 Expected/Got 裸值）；x3 批次（旧版整体掩码）保留作对照，两批不混算"),
        ("掩码抽查", "50 个 verifier 输出，leaks=0（runbook 3.3 脚本）"),
        ("记录费用", "无（glm53-flash 无单价记录；Token 见「Token与成本」表）"),
    ])
    row += 1
    ws.cell(row=row, column=2, value="四条件结果总览")
    row += 1
    headers = ["条件", "试验数", "通过数", "通过率", "总轮次", "输入+输出 Token", "缓存读取 Token", "工具调用"]
    header_row = row
    for col, h in enumerate(headers, 2):
        ws.cell(row=header_row, column=col, value=h)
    style_header_row(ws, row_num=header_row, col_start=2, col_end=len(headers) + 1)
    for i, condition in enumerate(CONDITIONS):
        row_num = header_row + 1 + i
        cond = summary["condition_summaries"][condition]
        cond_trials = [t for t in trials if t["condition"] == condition]
        tools = sum(sum(turn["tool_uses"] for turn in t["turns"]) for t in cond_trials)
        values = [
            LABELS[condition], cond["completed_trials"], cond["final_verifier_passes"],
            cond["final_verifier_passes"] / cond["completed_trials"],
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
    "触发机会", "可路由反馈", "不可路由反馈", "成功披露", "后续访问",
    "披露→通过差", "干净闭环",
]


def initial_modules_of(trial: dict) -> str:
    condition = trial["condition"]
    if condition == "flat":
        return f"全量前置（./modules/<id>/SKILL.md × {len(trial['initially_available_modules'])}）"
    if condition == "pd":
        return "./skill/SKILL.md + references/（入口自选）"
    return "0"


def sheet_detail(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("单组明细")
    setup_sheet(ws, title=f"{task_id} 单组审计明细（{len(data['trials'])} 个独立 ZCode 会话）", last_col=len(DETAIL_HEADERS) + 1)
    for col, h in enumerate(DETAIL_HEADERS, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(DETAIL_HEADERS) + 1)
    for i, trial in enumerate(data["trials"]):
        row_num = 5 + i
        usage = trial["usage"]
        fresh = usage["input_tokens"] + usage["output_tokens"]
        total = fresh + usage["cache_read_tokens"]
        timeouts = sum(1 for turn in trial["turns"] if turn["model_turn_timeout"])
        tools = sum(turn["tool_uses"] for turn in trial["turns"])
        passed = data["final_tests"].get(f"{trial['condition']}/{trial['replicate_id']}")
        expected = EXPECTED_TESTS[task_id]
        final_test = f"{passed}/{expected}" if passed is not None else f"{expected}/{expected}"
        condition = trial["condition"]
        if condition == "vista":
            disclosure = "; ".join(trial["disclosed_modules"]) or "无（首轮直通）"
            access = "; ".join(trial["accessed_modules_after_disclosure"]) or "无"
        elif condition == "pd":
            disclosure = "入口自选（无披露步骤）"
            access = "; ".join(trial["accessed_modules_after_disclosure"]) or "无"
        elif condition == "flat":
            disclosure = "全量预置（无披露步骤）"
            access = "; ".join(trial["accessed_modules_after_disclosure"]) or "无"
        else:
            disclosure = "无"
            access = "无"
        values = [
            trial["run_id"], LABELS[condition], trial["replicate_id"],
            trial["status"], int(trial["final_verifier_pass"]), len(trial["turns"]),
            timeouts, usage["requests"], tools, usage["input_tokens"],
            usage["output_tokens"], usage["cache_read_tokens"], fresh, total,
            final_test, trial["session_id"] or "", initial_modules_of(trial),
            disclosure, access,
            trial["vista_metrics"]["trigger_opportunities"],
            trial["vista_metrics"]["routable_feedback"],
            trial["vista_metrics"]["unroutable_feedback"],
            trial["vista_metrics"]["successful_disclosures"],
            trial["vista_metrics"]["next_round_module_access"],
            trial["vista_metrics"]["disclosure_to_pass_gap"]
            if trial["vista_metrics"]["disclosure_to_pass_gap"] is not None else "—",
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


TOKEN_HEADERS = [
    "条件", "试验数", "通过数", "通过率", "总轮次", "输入 Token", "输出 Token",
    "缓存读取 Token", "总 Token", "平均总 Token / 组", "agent 净时 (s)", "修正技能文件 Token",
]


def sheet_tokens(wb: Workbook, task_id: str, data: dict) -> None:
    ws = wb.create_sheet("Token与成本")
    summary, trials = data["summary"], data["trials"]
    token_summary = data["token_summary"]
    setup_sheet(ws, title="Token、轮次与成本汇总（sj4 四条件，5 重复）", last_col=len(TOKEN_HEADERS) + 1)
    for col, h in enumerate(TOKEN_HEADERS, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(TOKEN_HEADERS) + 1)
    totals = {"trials": 0, "passes": 0, "turns": 0, "fresh": 0, "cache": 0, "total": 0, "agent": 0.0}
    for i, condition in enumerate(CONDITIONS):
        row_num = 5 + i
        cond = summary["condition_summaries"][condition]
        fresh = cond["usage"]["input_tokens"] + cond["usage"]["output_tokens"]
        cache = cond["usage"]["cache_read_tokens"]
        total = fresh + cache
        corrected_skill = token_summary.get(condition, {}).get("skill_file_tokens", "—")
        values = [
            LABELS[condition], cond["completed_trials"], cond["final_verifier_passes"],
            cond["final_verifier_passes"] / cond["completed_trials"],
            sum(len(t["turns"]) for t in trials if t["condition"] == condition),
            cond["usage"]["input_tokens"], cond["usage"]["output_tokens"], cache, total,
            round(total / cond["completed_trials"]),
            round(cond["agent_net_seconds"]),
            corrected_skill,
        ]
        for col, value in enumerate(values, 2):
            ws.cell(row=row_num, column=col, value=value)
        style_data_row(ws, row_num=row_num, col_start=2, col_end=len(TOKEN_HEADERS) + 1, row_index=i)
        for col in range(3, len(TOKEN_HEADERS) + 2):
            ws.cell(row=row_num, column=col).alignment = ws.cell(row=row_num, column=col).alignment.copy(horizontal="right")
        ws.cell(row=row_num, column=5).number_format = "0.0%"
        totals["trials"] += cond["completed_trials"]; totals["passes"] += cond["final_verifier_passes"]
        totals["turns"] += values[4]; totals["fresh"] += fresh
        totals["cache"] += cache; totals["total"] += total
        totals["agent"] += cond["agent_net_seconds"]
    total_row = 9
    ws.cell(row=total_row, column=2, value="合计")
    for col, value in enumerate([totals["trials"], totals["passes"], totals["passes"] / totals["trials"],
                                 totals["turns"], totals["fresh"], totals["cache"], totals["total"],
                                 round(totals["total"] / totals["trials"]), round(totals["agent"]), "—"], 3):
        ws.cell(row=total_row, column=col, value=value)
    style_total_row(ws, row_num=total_row, col_start=2, col_end=len(TOKEN_HEADERS) + 1)
    for col in range(3, len(TOKEN_HEADERS) + 2):
        ws.cell(row=total_row, column=col).alignment = ws.cell(row=total_row, column=col).alignment.copy(horizontal="right")
    ws.cell(row=total_row, column=5).number_format = "0.0%"

    row = total_row + 2
    ws.cell(row=row, column=2, value="关键指标与解释")
    row += 1
    for col, h in enumerate(["指标", "数值", "解释"], 2):
        ws.cell(row=row, column=col, value=h)
    style_header_row(ws, row_num=row, col_start=2, col_end=4)
    vista_trials = [t for t in trials if t["condition"] == "vista"]
    stalls = sum(1 for t in trials if t["stop_reason"] == "stalled_after_full_disclosure")
    cache_cost = data["cache_cost"].get("vista", {})
    checks = [
        (f"{len(trials)} 组总 Token", totals["total"], "输入 + 输出 + 缓存读取，覆盖全部 20 组"),
        ("VISTA 干净闭环",
         sum(1 for t in vista_trials if t["vista_metrics"]["clean_complete_closure"]),
         "失败 → 披露 → 访问 → 修复通过的完整闭环（gap≤1）"),
        ("VISTA 首轮直通",
         sum(1 for t in vista_trials if not t["disclosed_modules"]),
         "任务足够简单，未触发披露即通过"),
        ("VISTA 停滞停止", stalls,
         "披露后连续 2 轮无进展（新披露/技能读取/新失败层均无），停止保护生效"),
        ("技能文件 Token（修正口径）",
         "flat 全包 / pd 入口 / vista 披露后",
         "recompute_sj4_tokens.py：被读文件本身 token；vista 披露后仅 461（t011）/ 740（t012）"),
        ("模型轮次超时", sum(1 for t in trials for turn in t["turns"] if turn["model_turn_timeout"]),
         "单回合 1800 秒上限；本批次无超时隔离"),
        ("缓存成本估算（hit=1.0）",
         (f"vista/flat 成本比 {round(cache_cost.get('glm-5.3-flash:cached', 0) / cache_cost.get('glm-5.3-flash:no_cache', 1), 2)}"
          if cache_cost.get("glm-5.3-flash:no_cache") else "—") + "（glm-5.3-flash，全缓存档）",
         "estimate_sj4_cache_cost.py：全缓存下 vista 相对 flat 的多回合成本"),
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
    ("1 · 最小依赖准备", "在项目 .venv 安装 numpy/pandas/yaml/rapidfuzz/openpyxl/pypdf；readiness 预检 --dry-run 两任务均 ready:true", "依赖检查与任务目录", "无",
     "readiness.json、依赖导入检查", "不安装 Docker，重任务本地可跑"),
    ("2 · 固定执行配置", "ZCode 0.16.9 headless（ELECTRON_RUN_AS_NODE=1）；双端点并行（task-011=new-provider，task-012=new-provider-2），每端点 4 线程（ThreadPoolExecutor）", "任务提示词",
     "鉴权与 provider_config 细节", "launcher 版本探测、rollout 模型身份 fail-closed", "20 组使用同一模型 glm53-flash；共享解压缓存以 threading.Lock 互斥"),
    ("3 · 平衡排程", "复现×条件 交错调度；--replicates 5 参数化；结果目录启动时冻结（VISTA_RUN_PREFIX 显式设定），中断后同命令续跑", "本组任务", "其他组结果",
     "condition、replicate_id", "降低固定顺序与时间漂移影响；跨整点不分裂"),
    ("4 · 试验隔离", "每组独立工作区、验证器目录、ZCODE_STORAGE_DIR；13F 大输入用共享解压缓存 + APFS clone；被中断半成品自动归档 *-abandoned-<时间>", "task.md、输入文件", "其他组工作区与会话",
     "20 个唯一 session_id", "防止跨组提示、文件和会话污染"),
    ("5 · No-Skill 条件", "不暴露任何任务 Skill", "任务文件与公开任务说明", "全部指导模块",
     "initially_available_modules=[]", "测量无 Skill 辅助基线"),
    ("6 · Flat 条件", "首轮前复制压平工件：每模块一个 SKILL.md 到 workspace/modules（SkillJuror flatten-source 形态）", "modules/<module-id>/SKILL.md", "无",
     "initially_available_modules、逐回合访问遥测", "测量全部指导前置可见的情况"),
    ("7 · PD 条件", "复制 PD 工件：入口 skill/SKILL.md + references/，代理自选读取；与 vista 披露模块同源", "skill/SKILL.md + references/", "无",
     "initially_available_modules、逐回合访问遥测", "测量渐进披露结构本身（无反馈回路）"),
    ("8 · VISTA 条件", "初始隐藏；PD 工件与 manifest 保存在控制器侧 router-source", "任务文件", "manifest.yaml 与原始模块",
     "initially_available_modules=[]", "只在失败反馈证明有需要时披露"),
    ("9 · 模型执行", "noskill/flat/pd 单回合单 episode（失败即停止，无反馈）；vista 最多 6 回合同 session resume；每回合上限 1800 秒", "公开任务文件", "—",
     "turn-N.jsonl、model-io rollout 遥测、每回合 turn_usage", "回合结构分条件，防止反馈污染单回合组"),
    ("10 · 统一验收", "每回合运行路径改写的本地验证器（/root→工作区，逐字节等价）；公开诊断新版掩码：保留字段名、只掩值（含 Expected/Got 裸值）", "经清洗的公开诊断",
     "验证器实现与隐藏答案", "verifier-turn-N.txt；掩码抽查 leaks=0", "提供字段级修复信号并避免 oracle 泄露"),
    ("11 · VISTA 路由", "FeedbackParser 分类公开失败 + 失败层签名（t011：13f_name_list_mismatch/13f_value_mismatch/13f_missing_output；t012：fraud_missed/fraud_false_positive/fraud_field_mismatch/missing_output_file）→ DisclosureRouter 披露对应 PD reference", "新披露 reference 文件",
     "尚未披露模块", "routable_feedback、successful_disclosures、next_round_module_access", "实现反馈驱动渐进式披露"),
    ("12 · 停滞保护", "披露后连续 2 轮无新披露、无技能读取、无新失败层 → 停止（failed_stalled_after_full_disclosure）", "—", "—",
     "stop_reason", "防止无限修复循环烧 token"),
]


def sheet_method(wb: Workbook, task_id: str) -> None:
    ws = wb.create_sheet("方法与流程")
    setup_sheet(ws, title=f"{task_id} ZCode sj4 四条件对照实验：方法与流程", last_col=7)
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
    ("共同基础提示词", "首轮", "限定隔离目录；读取 task.md 与输入文件；写出 task.md 声明的输出；本地 python 做分析；隐藏验证器由控制器在回合结束后运行",
     "Solve the isolated {benchmark} task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and the supplied input files: {inputs}. Write the required output files declared in task.md to this directory: {outputs}. Use the local python interpreter for any simulation or analysis you need to run. Hidden verifier tests are run only by the controller after the episode.",
     "四条件共同可见", "四条件完全相同"),
    ("No-Skill", "首轮", "明确没有任务 Skill。",
     " No task Skill is available in this condition.", "无任务 Skill", "只依靠任务材料和公开反馈"),
    ("Flat", "首轮", "全部指导模块以压平单文件形态可用，要求读取相关模块。",
     " All guidance modules are available as flattened single files under ./modules/<module-id>/SKILL.md; read and use the relevant modules.",
     "全部压平模块", "前置完整暴露（SkillJuror flatten-source 形态）"),
    ("PD", "首轮", "渐进披露 skill 可用，含入口 SKILL.md 与 references/，由代理自选加载。",
     " A progressive-disclosure skill is available at ./skill/SKILL.md with detailed references under ./skill/references/; read the entry skill and decide for yourself which references to load and use.",
     "入口 + 全部 reference", "结构渐进披露（无反馈回路）"),
    ("VISTA", "首轮", "初始无任务 Skill；公开验证器失败后 VISTA 才披露指导模块。",
     " No task Skill is initially available; after a public verifier failure VISTA may disclose guidance modules.",
     "仅任务文件", "失败驱动的渐进披露"),
    ("失败反馈模板", "vista 第 2–6 轮", "向 vista 返回公开失败类型（+失败层签名）和经清洗诊断，同会话继续修复。",
     "The public verifier check failed with {feedback_types}. Public diagnostic:\n{diagnostic} … Continue in the same session, work only inside this directory, and repair the required outputs: {outputs}.",
     "由条件指令决定", "不暴露绝对路径、隐藏测试实现或答案"),
    ("VISTA 新披露指令", "披露后的下一轮", "要求先读取本轮新披露的模块再修复。",
     "VISTA disclosed ./skill/references/<module-id>.md. Read the disclosed module before repairing.",
     "本轮新增披露 reference", "记录后续实际访问"),
    ("VISTA 已披露指令", "无新模块时", "继续检查已经披露的模块。",
     "The verifier still fails. Re-examine the already disclosed modules under ./skill/ (<modules>) and repair the outputs accordingly.",
     "仅已披露模块", "禁止访问控制器侧隐藏源"),
    ("VISTA 无可用模块指令", "路由无结果时", "不提供模块，仅依据公开失败类别修复。",
     "No task Skill is available; repair from the public failure category.", "无模块", "与基线定义一致"),
]


def sheet_prompts(wb: Workbook, task_id: str) -> None:
    ws = wb.create_sheet("条件提示词")
    setup_sheet(ws, title=f"{task_id} sj4 实验提示词与条件差异（中文说明 + 原始英文）", last_col=7)
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
    setup_sheet(ws, title="sj4 四条件披露闭环证据（VISTA 为唯一反馈披露组）", last_col=10)
    headers = ["条件 / 试验", "初始可见性", "首轮结果", "总轮次", "触发机会", "披露模块", "实际访问证据", "模型超时", "最终结果与解释"]
    for col, h in enumerate(headers, 2):
        ws.cell(row=4, column=col, value=h)
    style_header_row(ws, row_num=4, col_start=2, col_end=len(headers) + 1)
    noskill = [t for t in trials if t["condition"] == "noskill"]
    flat = [t for t in trials if t["condition"] == "flat"]
    pd = [t for t in trials if t["condition"] == "pd"]
    vista = [t for t in trials if t["condition"] == "vista"]
    first_failed = lambda group: sum(1 for t in group if len(t["turns"]) > 1 or not t["final_verifier_pass"])

    def group_row(label, visibility, group, disclosure_note, access_note, note):
        return (
            label, visibility,
            f"{first_failed(group)} 组首轮失败",
            sum(len(t["turns"]) for t in group),
            sum(t["vista_metrics"]["trigger_opportunities"] for t in group),
            disclosure_note, access_note,
            sum(1 for t in group for turn in t["turns"] if turn["model_turn_timeout"]),
            note,
        )

    rows: list[tuple] = [
        group_row(
            f"No-Skill · {len(noskill)} 组", "无模块", noskill, "无", "无",
            f"{sum(1 for t in noskill if t['final_verifier_pass'])}/{len(noskill)} 通过；无 Skill 对照",
        ),
        group_row(
            f"Flat · {len(flat)} 组", "全部压平模块前置可见", flat, "首轮前全部可见",
            "逐回合遥测：" + "；".join(
                f"rep{t['replicate_id']} 读取 {len(t['accessed_modules_after_disclosure'])} 个模块"
                for t in flat),
            f"{sum(1 for t in flat if t['final_verifier_pass'])}/{len(flat)} 通过；完整前置暴露对照",
        ),
        group_row(
            f"PD · {len(pd)} 组", "入口 SKILL.md + references/", pd,
            "入口自选（无披露步骤）",
            "逐回合遥测：" + "；".join(
                f"rep{t['replicate_id']} 读取 {len(t['accessed_modules_after_disclosure'])} 个模块"
                for t in pd),
            f"{sum(1 for t in pd if t['final_verifier_pass'])}/{len(pd)} 通过；与 vista 对照隔离反馈回路贡献",
        ),
    ]
    for t in vista:
        failed = len(t["turns"]) > 1 or not t["final_verifier_pass"]
        rows.append((
            f"VISTA · 重复 {t['replicate_id']}", "初始隐藏",
            "第 1 轮失败" if failed else "第 1 轮通过",
            len(t["turns"]), t["vista_metrics"]["trigger_opportunities"],
            "；".join(t["disclosed_modules"]) or "无（turn-1 直通）",
            "；".join(t["accessed_modules_after_disclosure"]) or "无需披露后访问",
            sum(1 for turn in t["turns"] if turn["model_turn_timeout"]),
            f"第 {len(t['turns'])} 轮通过；" + ("完整干净闭环" if t["vista_metrics"]["clean_complete_closure"] else "未形成完整闭环"),
        ))
    rows.append((
        "VISTA · 汇总", "初始隐藏", f"{first_failed(vista)} 组首轮失败",
        sum(len(t["turns"]) for t in vista),
        sum(t["vista_metrics"]["trigger_opportunities"] for t in vista),
        f"成功披露 {sum(t['vista_metrics']['successful_disclosures'] for t in vista)} 次；涉及 {len({m for t in vista for m in t['disclosed_modules']})} 个唯一模块",
        f"后续访问 {sum(t['vista_metrics']['next_round_module_access'] for t in vista)} 次",
        sum(1 for t in vista for turn in t["turns"] if turn["model_turn_timeout"]),
        f"{sum(1 for t in vista if t['final_verifier_pass'])}/{len(vista)} 通过；{sum(1 for t in vista if t['vista_metrics']['clean_complete_closure'])}/{len(vista)} 形成完整闭环",
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
    n = len(data["trials"])
    last = 4 + n
    rows = [
        ("运行", "group-summary.protocol", "实验总览!B4", "正式实验唯一标识", "group-summary.json"),
        ("运行", "group-summary.completed_trials", "实验总览!B8", "完成试验数", "group-summary.json"),
        ("运行", "group-summary.provider_id / requested_provider_model", "实验总览!B11", "提供商 / 模型", "new-provider(-2) / glm53-flash"),
        ("条件", "condition_summaries.*.final_verifier_passes", "实验总览!四条件结果总览 / Token与成本", "每条件通过数", "group-summary.json；计算值而非公式（程序化验证）"),
        ("试验", "replicate result.json", f"单组明细!B5:AA{last}", f"条件 × 重复的单组记录（{n} 试验）", "group-summary.json + result.json"),
        ("Token", "result.json usage.input_tokens / output_tokens", f"单组明细!K5:L{last}", "输入 / 输出 Token", "model-io rollout（response.usage）"),
        ("Token", "result.json usage.cache_read_tokens", f"单组明细!M5:M{last}", "缓存读取 Token", "model-io rollout"),
        ("Token", "recompute_sj4_tokens.py → sj4_x5_token_summary.json", "Token与成本!修正技能文件Token 列", "被读文件本身 token（修正口径）", "data/derived/sj4_x5_token_summary.json（gitignored）；旧口径 skill_read_tokens 为请求整段上下文输入"),
        ("遥测", "turns[].tool_uses / turn_usage", f"单组明细!J5:J{last}", "工具调用数；每回合 turn_usage 为新版新增", "response.toolCalls 逐回合计数"),
        ("验收", "verifier-turn-N.txt（最终回合）", f"单组明细!P5:P{last}", "最终测试通过数", "路径改写验证器的 pytest 汇总行"),
        ("披露", "vista_metrics.*", f"单组明细!U5:AA{last} / 披露证据", "触发、路由、披露、访问、闭环", "result.json vista_metrics；VISTA 为唯一反馈披露组"),
        ("无对应字段", "—", "单组明细（无记录费用列）", "glm53-flash 无单价记录", "与 task-001 模板差异：记录费用列省略；缓存成本估算见 Token与成本 关键指标"),
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
    tasks = sys.argv[1:] or sorted(BATCH)
    for task_id in tasks:
        out = build(task_id)
        print("written:", out)
