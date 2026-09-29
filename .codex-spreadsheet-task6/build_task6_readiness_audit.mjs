import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const REPO = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318";
const RUN_ID = "formal-task006-local-3x3-minimaxm3-20260824-03-readiness-final";
const DATA_ROOT = `${REPO}/data/raw/${RUN_ID}`;
const AUDIT_ROOT = `${REPO}/outputs/${RUN_ID}-audit`;
const OUTPUT = `${AUDIT_ROOT}/task6_readiness_audit_zh.xlsx`;
const readiness = JSON.parse(await fs.readFile(`${DATA_ROOT}/readiness.json`, "utf8"));
const group = JSON.parse(await fs.readFile(`${DATA_ROOT}/group-summary.json`, "utf8"));
const audit = JSON.parse(await fs.readFile(`${AUDIT_ROOT}/posthoc-audit.json`, "utf8"));

const navy = "#1F4E78", blue = "#2F75B5", lightBlue = "#D9EAF7", band = "#BFE3F3", pale = "#F2F2F2", green = "#E2F0D9", yellow = "#FFF2CC", red = "#FCE4D6", text = "#25364A", border = "#B8CCE4", white = "#FFFFFF";
const wb = Workbook.create();
const names = ["实验总览", "9组明细", "Token与成本", "方法与流程", "条件提示词", "PD-VISTA披露证据", "字段映射"];
const sheets = Object.fromEntries(names.map((name) => [name, wb.worksheets.add(name)]));
for (const sheet of Object.values(sheets)) sheet.showGridLines = false;

function title(sheet, endCol, value) {
  const range = sheet.getRange(`A1:${endCol}1`); range.merge(); range.values = [[value]];
  range.format = { fill: navy, font: { bold: true, color: white, fontSize: 16, name: "Carlito" }, rowHeight: 28, horizontalAlignment: "left" };
}
function section(sheet, row, endCol, value) {
  const range = sheet.getRange(`A${row}:${endCol}${row}`); range.merge(); range.values = [[value]];
  range.format = { fill: blue, font: { bold: true, color: white, fontSize: 11, name: "Carlito" }, rowHeight: 22 };
}
function header(sheet, range) {
  sheet.getRange(range).format = { fill: lightBlue, font: { bold: true, color: text, name: "Carlito" }, wrapText: true, borders: { preset: "all", style: "thin", color: border } };
}
function body(sheet, range, wrap = false) {
  sheet.getRange(range).format = { font: { color: text, fontSize: 10, name: "Carlito" }, wrapText: wrap, borders: { preset: "all", style: "thin", color: border } };
}
function widths(sheet, pairs) { for (const [col, width] of Object.entries(pairs)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }
function fill(sheet, range, color) { sheet.getRange(range).format = { fill: color, font: { color: text, name: "Carlito" }, borders: { preset: "all", style: "thin", color: border }, wrapText: true }; }
function safeText(value) { const textValue = String(value ?? ""); return textValue.startsWith("=") ? `'${textValue}` : textValue; }

const schedule = [
  [1, "noskill"], [1, "native"], [1, "pd-vista"],
  [2, "native"], [2, "pd-vista"], [2, "noskill"],
  [3, "pd-vista"], [3, "noskill"], [3, "native"],
];
const blocker = "未执行：task-006 缺失 BugSwarm 失败仓库与 /home/github 构建环境";
const evidencePath = `${DATA_ROOT}/readiness.json`;

// 9 组明细
const detail = sheets["9组明细"];
title(detail, "T", `task6 · 9组计划明细（正式实验未启动 · ${RUN_ID}）`);
detail.getRange("A3:T3").values = [["序号", "条件", "重复", "平衡顺序", "状态", "最终Verifier", "Session ID", "请求数", "工具调用", "轮次", "输入Token", "输出Token", "缓存读取Token", "输入+输出Token", "记录费用", "超时次数", "PD触发机会", "披露模块", "下一轮实际访问", "证据/原因"]]; header(detail, "A3:T3");
const detailRows = schedule.map(([rep, condition], index) => [
  `${condition}-${String(rep).padStart(2, "0")}`, condition, rep, index + 1, blocker, "未执行", null, 0, 0, 0, 0, 0, 0, null, 0, 0, 0, "未触发", "未触发", `${evidencePath}；${blocker}`,
]);
detail.getRange("A4:T12").values = detailRows;
detail.getRange("N4").formulas = [["=K4+L4"]]; detail.getRange("N4:N12").fillDown();
body(detail, "A4:T12", true); fill(detail, "E4:E12", yellow); fill(detail, "F4:F12", red); detail.getRange("H4:P12").format.numberFormat = "#,##0"; detail.getRange("O4:O12").format.numberFormat = "0.00000000";
detail.freezePanes.freezeRows(3); widths(detail, {A:18,B:12,C:8,D:10,E:46,F:14,G:28,H:10,I:10,J:8,K:14,L:14,M:16,N:16,O:14,P:12,Q:14,R:20,S:24,T:74}); detail.tables.add("A3:T12", true, "Task6PlannedTrialDetail");

// Token 与成本
const cost = sheets["Token与成本"];
title(cost, "K", "Token 与成本（正式批次未启动，公式汇总为 0）"); section(cost, 3, "K", "逐组使用量与费用");
cost.getRange("A4:K4").values = [["组ID", "条件", "重复", "请求数", "工具调用", "输入Token", "输出Token", "缓存读取Token", "输入+输出Token", "记录费用", "Session ID"]]; header(cost, "A4:K4");
for (let i = 0; i < 9; i++) {
  const r = 5 + i, d = 4 + i;
  cost.getRange(`A${r}:K${r}`).formulas = [[`='9组明细'!A${d}`, `='9组明细'!B${d}`, `='9组明细'!C${d}`, `='9组明细'!H${d}`, `='9组明细'!I${d}`, `='9组明细'!K${d}`, `='9组明细'!L${d}`, `='9组明细'!M${d}`, `='9组明细'!N${d}`, `='9组明细'!O${d}`, `=IF('9组明细'!G${d}=\"\",\"\",'9组明细'!G${d})`]];
}
body(cost, "A5:K13", true); cost.getRange("D5:I13").format.numberFormat = "#,##0"; cost.getRange("J5:J13").format.numberFormat = "0.00000000"; cost.freezePanes.freezeRows(4); widths(cost, {A:18,B:12,C:8,D:12,E:12,F:14,G:14,H:16,I:16,J:14,K:30}); cost.tables.add("A4:K13", true, "Task6UsageDetail");
section(cost, 16, "K", "条件汇总（由 9 组明细公式计算）"); cost.getRange("A17:K17").values = [["条件", "计划数", "完成数", "通过数", "通过率", "请求数", "工具调用", "输入Token", "输出Token", "缓存读取Token", "记录费用"]]; header(cost, "A17:K17"); cost.getRange("A18:A20").values = [["noskill"], ["native"], ["pd-vista"]];
for (let r = 18; r <= 20; r++) {
  cost.getRange(`B${r}:K${r}`).formulas = [[
    `=COUNTIF('9组明细'!$B$4:$B$12,A${r})`, `=COUNTIFS('9组明细'!$B$4:$B$12,A${r},'9组明细'!$E$4:$E$12,"已完成")`, `=COUNTIFS('9组明细'!$B$4:$B$12,A${r},'9组明细'!$F$4:$F$12,"通过")`, `=IFERROR(D${r}/B${r},0)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$H$4:$H$12)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$I$4:$I$12)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$K$4:$K$12)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$L$4:$L$12)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$M$4:$M$12)`, `=SUMIF('9组明细'!$B$4:$B$12,A${r},'9组明细'!$O$4:$O$12)`
  ]];
}
body(cost, "A18:K20"); cost.getRange("E18:E20").format.numberFormat = "0.0%"; cost.getRange("F18:J20").format.numberFormat = "#,##0"; cost.getRange("K18:K20").format.numberFormat = "0.00000000";

// 实验总览
const summary = sheets["实验总览"];
title(summary, "H", `Vista task6 · 三条件 × 三重复本机 OpenCode 审计（就绪阻断，未启动正式批次）`);
summary.getRange("A3:B11").values = [["诊断批次", RUN_ID], ["任务", "task-006 · fix-build-agentops 构建修复"], ["审计状态", "未就绪：正式批次未启动"], ["计划试验", null], ["完成试验", null], ["独立会话", null], ["最终Verifier", null], ["当前提供商 / 模型", readiness.requested_provider_model || "minimax-cn/MiniMax-M3"], ["OpenCode 记录费用", null]];
summary.getRange("B6:B7").formulas = [["=COUNTA('9组明细'!$A$4:$A$12)"], ["=COUNTIF('9组明细'!$E$4:$E$12,\"已完成\")"]]; summary.getRange("B8:B9").formulas = [["=COUNTA('9组明细'!$G$4:$G$12)"], ["=COUNTIF('9组明细'!$F$4:$F$12,\"通过\")&\"/9 组通过\""]]; summary.getRange("B11").formulas = [["=SUM('9组明细'!$O$4:$O$12)"]]; body(summary, "A3:B11"); summary.getRange("A3:A11").format.fill = pale; summary.getRange("A3:A11").format.font = { bold: true, color: text, name: "Carlito" }; fill(summary, "B5:B5", yellow); fill(summary, "B7:B9", red); summary.getRange("B11").format.numberFormat = "0.00000000";
section(summary, 14, "H", "三种条件结果总览"); summary.getRange("A15:H15").values = [["条件", "计划数", "完成数", "通过数", "通过率", "请求数", "输入+输出Token", "记录费用"]]; header(summary, "A15:H15"); summary.getRange("A16:A18").values = [["noskill"], ["native"], ["pd-vista"]];
for (let r = 16; r <= 18; r++) summary.getRange(`B${r}:H${r}`).formulas = [[`='Token与成本'!B${r+2}`, `='Token与成本'!C${r+2}`, `='Token与成本'!D${r+2}`, `='Token与成本'!E${r+2}`, `='Token与成本'!F${r+2}+0`, `='Token与成本'!H${r+2}+'Token与成本'!I${r+2}`, `='Token与成本'!K${r+2}`]];
body(summary, "A16:H18"); summary.getRange("E16:E18").format.numberFormat = "0.0%"; summary.getRange("F16:G18").format.numberFormat = "#,##0"; summary.getRange("H16:H18").format.numberFormat = "0.00000000";
section(summary, 21, "H", "关键完整性检查"); summary.getRange("A22:B22").values = [["检查项", "结果"]]; header(summary, "A22:B22"); summary.getRange("A23:A29").values = [["9 个正式结果文件"], ["9 个唯一 session"], ["最终 Verifier 全部通过"], ["当前模型配置已读取"], ["使用 Docker"], ["使用 SkillBench 标准执行路线"], ["正式统计是否启动"]];
summary.getRange("B23:B29").formulas = [["=IF(B7=9,\"通过\",\"未执行\")"], ["=IF(B8=9,\"通过\",\"未创建\")"], ["=IF(B9=9,\"通过\",\"未执行\")"], ["=IF(B10<>\"\",\"已读取\",\"缺失\")"], ["=IF(FALSE,\"是\",\"否\")"], ["=IF(FALSE,\"是\",\"否\")"], ["=IF(B7>0,\"已启动\",\"未启动\")"]]; body(summary, "A23:B29"); summary.getRange("A23:A29").format.fill = pale; summary.getRange("A23:A29").format.font = { bold: true, color: text, name: "Carlito" }; fill(summary, "B23:B25", red); fill(summary, "B26:B28", green); fill(summary, "B29:B29", yellow);
section(summary, 32, "H", "Readiness 阻断证据"); summary.getRange("A33:H36").values = [["Task 材料", "task-006 身份、task.md、4 个原始模块和 PD-VISTA 资产存在；environment 仅含 Dockerfile 与 Skill 文件。", null, null, null, null, null, null], ["缺失内容", "原始失败 AgentOps 代码库不存在；/home/github/build/failed/AgentOps-AI/agentops 不存在。", null, null, null, null, null, null], ["Verifier", "公开收集已通过 3 tests collected；完整运行依赖缺失的 BugSwarm /home/github 构建环境，不能忠实执行。", null, null, null, null, null, null], ["路线约束", "未调用 Docker、SkillBench/BenchFlow 或平台路线；未启动 OpenCode 模型请求。", null, null, null, null, null, null]]; for (const row of [33,34,35,36]) summary.getRange(`B${row}:H${row}`).merge(); body(summary, "A33:H36", true); summary.getRange("A33:A36").format.fill = pale; summary.getRange("A33:A36").format.font = { bold: true, color: text, name: "Carlito" }; widths(summary, {A:30,B:34,C:14,D:14,E:14,F:18,G:18,H:18}); summary.freezePanes.freezeRows(15);

// 方法与流程
const method = sheets["方法与流程"];
title(method, "F", "方法与流程（task6 · 本机 OpenCode readiness 审计）"); section(method, 3, "F", "执行协议与当前状态"); method.getRange("A4:F4").values = [["步骤", "统一协议", "task6 状态", "公开证据", "正式统计", "中文说明"]]; header(method, "A4:F4");
method.getRange("A5:F14").values = [
  ["1", "独立确认身份", "task-006 / fix-build-agentops", `${REPO}/tasks/pilot/task-006/task.yaml`, "否", "身份解析通过，但不等于输入材料就绪。"],
  ["2", "检查任务材料", "task.md、Dockerfile、Verifier、4 个原始模块存在；失败仓库缺失", evidencePath, "否", "不能用 Dockerfile 代替原始代码库。"],
  ["3", "最小依赖", "项目 .venv 补齐 unidiff==0.7.5；pytest/yaml 已有", `${REPO}/.venv`, "否", "仅补 Verifier 收集所需依赖，未安装平台。"],
  ["4", "公开 Verifier 入口", "pytest --collect-only 通过，3 tests collected", `${REPO}/vendor/skillsbench/tasks/fix-build-agentops/verifier/test_outputs.py`, "否", "收集通过但完整运行依赖 /home/github。"],
  ["5", "共享限流检查", "未发现其他 task 的 OpenCode/控制器进程", "独立只读 ps 检查为空", "否", "因此不存在等待窗口；正式批次仍因 readiness 阻断。"],
  ["6", "平衡交错矩阵", "计划 9 组：1N-1Native-1PDV-2Native-2PDV-2N-3PDV-3N-3Native", "9组明细", "计划", "尚未创建任何工作区、session 或 DB。"],
  ["7", "No-Skill", "计划首轮不提供任务 Skill", "条件提示词", "计划", "仅使用 task.md 与本地任务材料。"],
  ["8", "Native", "计划首轮完整提供 4 个原始模块", "条件提示词", "计划", "模块正文不改写。"],
  ["9", "PD-VISTA", "计划首轮只提供 VISTA.md；失败后单轮最多披露一个模块", "PD-VISTA披露证据", "计划", "未发生触发、披露或下一轮读取。"],
  ["10", "正式启动门槛", "未通过：缺失原始失败仓库与完整构建环境", "group-summary.json / posthoc-audit.json", "排除", "不启动模型，避免虚构 task6 结果。"],
]; body(method, "A5:F14", true); widths(method, {A:8,B:24,C:58,D:52,E:14,F:48}); method.freezePanes.freezeRows(4);
section(method, 17, "F", "公开诊断、超时、限流和审计规则"); method.getRange("A18:F18").values = [["规则", "统一设定", "task6 计划", "模型可见内容", "排除内容", "当前记录"]]; header(method, "A18:F18"); method.getRange("A19:F25").values = [
  ["最大轮数", "6 轮；单轮 420 秒；Verifier 240 秒", "固定，不因条件改变", "同一公开诊断可继续同一 session", "内部控制器状态", "未启动"],
  ["公开诊断清洗", "仅保留失败/错误/缺失/断言等安全摘要，路径归一化", "固定规则", "任务失败反馈", "完整内部堆栈、DB、密钥", "未生成"],
  ["落盘容错", "模型收尾卡住时先验收已落盘文件", "固定", "公开 Verifier 结果", "未公开答案", "未触发"],
  ["429 / 零请求", "结构化 429 或明确配额才隔离重跑", "串行 fresh workspace/session", "不计任务失败", "普通正文中的 429 数字", "未触发"],
  ["PD-VISTA 路由", "manifest trigger；每轮最多披露 1 个相关模块", "模块未披露", "根路由与公开诊断", "其他隐藏模块正文", "触发机会 0"],
  ["数据库审计", "只读查询每个隔离 opencode.db", "正式 session=0", "provider/model/token/cost", "不共享状态", "无 DB"],
  ["执行路线", "OpenCode 当前配置；Docker=false；SkillBench=false", "只允许本机路线", "当前 provider/model 配置", "平台依赖与容器", "已确认 false"],
]; body(method, "A19:F25", true); widths(method, {A:20,B:42,C:40,D:36,E:42,F:34});

// 条件提示词
const prompts = sheets["条件提示词"];
title(prompts, "F", "三种条件及失败反馈的原始提示词与中文说明（计划，未发送）"); section(prompts, 3, "F", "统一基础任务提示词"); prompts.getRange("A4:F4").values = [["条件", "原始首轮提示词（英文，未发送）", "中文说明", "Skill 可见性", "失败反馈/修复规则", "当前状态"]]; header(prompts, "A4:F4");
const commonPrompt = "Solve the isolated task-006 fix-build-agentops task in this working directory. Work only inside this directory and do not access parent or external directories. Read task.md and follow its three steps: write failed_reasons.txt, create standard patch_*.diff files under the failed repository path, and apply the patches. Use only local task materials; do not fetch data or call Docker, BenchFlow, or SkillBench. The controller runs the complete public verifier after each turn and may return a cleaned public diagnostic for repair.";
const conditionPrompts = {
  noskill: `${commonPrompt} No task Skill is available in this condition; solve from task.md and the local files only.`,
  native: `${commonPrompt} The complete original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use all relevant modules before the first turn.`,
  "pd-vista": `${commonPrompt} Read VISTA.md. Original guidance modules are initially hidden; after a public verifier failure, VISTA may disclose at most one relevant module under ./modules/<module-id>/SKILL.md. Read any disclosed module before the next repair turn.`,
};
const promptExplain = {
  noskill: "No-Skill：零任务 Skill，对照模型仅依赖任务说明与本地材料。",
  native: "Native：首轮前完整提供 analyze-ci、temporal-python-testing、testing-python、uv-package-manager 原始模块。",
  "pd-vista": "PD-VISTA：首轮只提供根路由；公开失败后按 manifest 单轮最多披露一个相关模块，并审计下一轮实际读取。",
};
prompts.getRange("A5:F7").values = Object.entries(conditionPrompts).map(([condition, prompt]) => [condition, safeText(prompt), promptExplain[condition], condition === "noskill" ? "无" : condition === "native" ? "4 个原始模块" : "VISTA.md 根路由", "失败后同一 session 返回清洗后的公开诊断；PD 每轮最多披露 1 个相关模块。", "未发送（readiness 阻断）"]); body(prompts, "A5:F7", true); widths(prompts, {A:14,B:92,C:48,D:26,E:54,F:26}); prompts.freezePanes.freezeRows(4);
section(prompts, 10, "F", "task.md 原始任务文本与中文说明"); prompts.getRange("A11:F11").values = [["来源", "原始文本（英文）", "中文说明", "适配约束", "执行入口", "状态"]]; header(prompts, "A11:F11"); prompts.getRange("A12:F14").values = [
  ["task.md", safeText("You need to fix errors in a Python codebase. The repository is located in /home/github/build/failed/<repo>/<id>. Step 1: analyze the repository and write the analysis and plan in /home/github/build/failed/failed_reasons.txt. Step 2: write standard unified patch files at /home/github/build/failed/<repo>/<id>/patch_{i}.diff. Step 3: apply your changes."), "修复 Python 代码库构建错误；先写失败原因，再生成并应用 unified diff。", "原始路径依赖 BugSwarm 工作区，本机未重写为合成路径。", "vendor/.../verifier/test_outputs.py", "材料缺失"],
  ["Verifier", safeText("The public verifier checks failed_reasons.txt, valid patch_*.diff files, and runs run_passed.sh in the copied passed repository."), "Verifier 检查说明文件、合法补丁并运行通过构建脚本。", "完整运行需 /home/github/build 与 run_passed.sh 的 BugSwarm 环境。", "pytest --collect-only", "收集通过"],
  ["controller", safeText("The controller would run the complete public verifier after each turn and return a cleaned public diagnostic if repair is needed."), "控制器统一执行 Verifier，失败后回传清洗诊断。", "本批次未发送模型请求，因此无实际诊断。", "task6 专属运行器", "未启动"],
]; body(prompts, "A12:F14", true); widths(prompts, {A:18,B:92,C:48,D:54,E:46,F:20});
section(prompts, 17, "F", "原始 Skill 模块清单"); prompts.getRange("A18:F18").values = [["模块 ID", "原始路径", "文件数", "触发类型", "Native", "PD-VISTA"]]; header(prompts, "A18:F18"); prompts.getRange("A19:F22").values = [
  ["analyze-ci", `${REPO}/vendor/skillsbench/tasks/fix-build-agentops/environment/skills/analyze-ci`, 1, "compilation_error / runtime_error", "首轮可见", "失败后按路由披露"],
  ["temporal-python-testing", `${REPO}/vendor/skillsbench/tasks/fix-build-agentops/environment/skills/temporal-python-testing`, 5, "timeout / repeated_failure", "首轮可见", "失败后按路由披露"],
  ["testing-python", `${REPO}/vendor/skillsbench/tasks/fix-build-agentops/environment/skills/testing-python`, 1, "test_failure / assertion_error", "首轮可见", "失败后按路由披露"],
  ["uv-package-manager", `${REPO}/vendor/skillsbench/tasks/fix-build-agentops/environment/skills/uv-package-manager`, 1, "dependency_error / command_failure", "首轮可见", "失败后按路由披露"],
]; body(prompts, "A19:F22", true); widths(prompts, {A:28,B:80,C:10,D:34,E:16,F:22});

// PD-VISTA disclosure evidence
const pd = sheets["PD-VISTA披露证据"];
title(pd, "J", "PD-VISTA 披露证据（正式批次未启动）"); section(pd, 3, "J", "触发—披露—下一轮访问—闭环审计"); pd.getRange("A4:J4").values = [["组ID", "重复", "首轮结果", "触发机会", "可路由反馈", "披露模块", "下一轮实际访问", "干净闭环", "最终结果", "证据/原因"]]; header(pd, "A4:J4");
pd.getRange("A5:J7").values = [1, 2, 3].map((rep) => [`pd-vista-${String(rep).padStart(2, "0")}`, rep, "未启动", 0, 0, "无", "无", 0, "未执行", blocker]); body(pd, "A5:J7", true); fill(pd, "C5:J7", yellow); widths(pd, {A:20,B:8,C:14,D:12,E:14,F:22,G:24,H:12,I:14,J:78}); pd.freezePanes.freezeRows(4);
section(pd, 10, "J", "PD 根路由协议"); pd.getRange("A11:J11").values = [["项目", "路径", "首轮状态", "触发条件", "单轮披露上限", "下一轮审计", "当前机会", "当前披露", "当前访问", "当前闭环"]]; header(pd, "A11:J11"); pd.getRange("A12:J13").values = [
  ["根路由", `${REPO}/skills/pd-vista/task-006/SKILL.md`, "计划可见", "公开编译/测试/依赖/超时失败", "1 个相关模块", "检查下一轮实际读取", 0, "无", "无", 0],
  ["manifest", `${REPO}/skills/pd-vista/task-006/manifest.yaml`, "计划可见", "4 类反馈触发器", "1 个相关模块", "route decision + JSONL 读取证据", 0, "无", "无", 0],
]; body(pd, "A12:J13", true); widths(pd, {A:18,B:72,C:16,D:34,E:18,F:34,G:12,H:18,I:18,J:16});

// 字段映射
const map = sheets["字段映射"];
title(map, "F", "字段映射与可审计来源（task6 readiness）"); map.getRange("A3:F3").values = [["工作簿字段", "来源 JSON / 文件", "公式或填充规则", "审计用途", "类型", "备注"]]; header(map, "A3:F3");
map.getRange("A4:F20").values = [
  ["批次", "readiness.run_id / group-summary.run_id", "固定诊断批次 ID", "批次唯一性", "文本", RUN_ID],
  ["任务身份", "tasks/pilot/task-006/task.yaml", "task_id + source.benchmark_task_id", "独立身份确认", "文本", "task-006 / fix-build-agentops"],
  ["计划组ID", "schedule + condition + replicate", "条件-两位重复编号", "9 组平衡矩阵", "文本", "只写计划，不表示已执行"],
  ["状态", "readiness.direct_execution_ready", "固定为未执行（就绪阻断）", "防止把诊断当正式结果", "文本", "正式统计 0 组"],
  ["最终Verifier", "verifier.collect / full_verifier_ready", "收集通过；完整运行 false", "入口与完整性区分", "文本", "3 tests collected"],
  ["Session ID", "posthoc-audit.formal_trials[].session_id", "空值；未启动模型", "独立会话审计", "文本", "0 个 session"],
  ["输入/输出/缓存 Token", "posthoc-audit.usage_totals", "逐组 0；汇总 SUM", "消耗审计", "数值", "未发生请求"],
  ["输入+输出 Token", "9组明细 K/L", "N 列 = K+L", "可复算新鲜 Token", "公式", "不硬编码汇总"],
  ["记录费用", "posthoc-audit.usage_totals.recorded_cost_usd", "逐组 0；汇总 SUM", "OpenCode 记录费用", "数值", "无 DB"],
  ["Provider / Model", "readiness.opencode_config.requested_provider_model", "当前配置读取", "模型身份", "文本", "minimax-cn / MiniMax-M3"],
  ["PD 触发机会", "PD-VISTA 披露证据", "0；无正式轮次", "披露机会审计", "数值", "未触发"],
  ["披露模块", "PD-VISTA manifest + event log", "无；未启动", "模块披露审计", "文本", "计划单轮最多 1 个"],
  ["下一轮实际访问", "PD-VISTA event log", "无；未启动", "防止只披露不读取", "文本", "未触发"],
  ["干净闭环", "PD-VISTA event log + final verifier", "0；未启动", "披露质量检查", "数值", "未触发"],
  ["Docker", "group-summary.docker_used", "固定 false", "路线约束", "布尔", "未使用"],
  ["SkillBench 路线", "group-summary.skillbench_execution_route_used", "固定 false", "路线约束", "布尔", "未使用"],
  ["排除原因", "readiness.readiness_reasons", "原样列出", "阻断透明度", "文本", "不进入正式统计"],
]; body(map, "A4:F20", true); widths(map, {A:28,B:56,C:48,D:34,E:16,F:48}); map.freezePanes.freezeRows(3);

const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "task6 readiness formula error scan" });
const errorText = errors.ndjson || "";
if (/(#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A)/.test(errorText)) throw new Error(`formula errors found: ${errorText.slice(0, 1200)}`);
await fs.mkdir(AUDIT_ROOT, { recursive: true });
const rendered = [];
for (const [index, sheet] of Object.values(sheets).entries()) {
  const blob = await wb.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
  const path = `${AUDIT_ROOT}/render-${String(index + 1).padStart(2, "0")}-${sheet.name.replace(/[^A-Za-z0-9_-]/g, "_")}.png`;
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer())); rendered.push(path);
}
await fs.writeFile(`${AUDIT_ROOT}/formula-error-scan.json`, JSON.stringify({ formula_errors: 0, ndjson: errorText || "0 matches" }, null, 2) + "\n");
await fs.writeFile(`${AUDIT_ROOT}/workbook-qc.json`, JSON.stringify({ formal_batch_started: false, formal_trials: 0, unique_sessions: 0, formula_errors: 0, rendered_sheets: rendered, output: OUTPUT }, null, 2) + "\n");
const output = await SpreadsheetFile.exportXlsx(wb); await output.save(OUTPUT);
console.log(JSON.stringify({ output: OUTPUT, sheets: names, formula_errors: 0, formal_batch_started: false, formal_trials: 0, unique_sessions: 0 }, null, 2));
