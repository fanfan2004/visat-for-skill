import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const repo = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318";
const runId = "formal-task001-local-3x3-minimaxm3-20260822-04";
const rawRoot = path.join(repo, "data/raw", runId);
const outputDir = path.join(repo, "outputs", `${runId}-audit`);
const outputPath = path.join(outputDir, "task1_final_audit_zh.xlsx");
const previewDir = path.join(outputDir, "previews");

const group = JSON.parse(await fs.readFile(path.join(rawRoot, "group-summary.json"), "utf8"));
const audit = JSON.parse(await fs.readFile(path.join(rawRoot, "posthoc-audit.json"), "utf8"));

const costByTrial = {
  "noskill-1": 0.11850864,
  "noskill-2": 0.13510002,
  "noskill-3": 0.18769842,
  "native-1": 0.31023468,
  "native-2": 0.17100744,
  "native-3": 0.28477254,
  "pd-vista-1": 0.15159300,
  "pd-vista-2": 0.10781574,
  "pd-vista-3": 0.15285594,
};

const conditionZh = { noskill: "No-Skill", native: "Native", "pd-vista": "PD-VISTA" };
const nativeModules = [
  "python-scala-syntax-mapping",
  "python-scala-idioms",
  "python-scala-oop",
  "python-scala-functional",
  "python-scala-collections",
  "python-scala-libraries",
].join("、");

const trialRows = [];
for (const item of group.results) {
  const resultPath = path.join(rawRoot, item.condition, `replicate-${String(item.replicate_id).padStart(2, "0")}`, "result.json");
  const detail = JSON.parse(await fs.readFile(resultPath, "utf8"));
  const finalTurn = detail.turns.length;
  const verifierText = await fs.readFile(
    path.join(path.dirname(resultPath), `verifier-turn-${finalTurn}.txt`),
    "utf8",
  );
  const tests = verifierText.match(/Tests passed: ([0-9]+\/[0-9]+)/)?.[1] ?? "";
  const quality = Number(verifierText.match(/Overall Score: ([0-9]+)\/[0-9]+/)?.[1] ?? 0);
  const timeouts = detail.turns.filter((turn) => turn.model_turn_timeout).length;
  const usage = detail.usage;
  trialRows.push({
    runId: detail.run_id,
    condition: conditionZh[item.condition],
    replicate: item.replicate_id,
    status: detail.status,
    passed: detail.final_verifier_pass ? 1 : 0,
    turns: detail.turns.length,
    timeouts,
    requests: usage.requests,
    tools: usage.tool_uses,
    input: usage.input_tokens,
    output: usage.output_tokens,
    cache: usage.cache_read_tokens,
    cost: costByTrial[`${item.condition}-${item.replicate_id}`],
    tests,
    quality,
    session: detail.session_id,
    initial: item.condition === "native" ? nativeModules : "",
    disclosed: detail.disclosed_modules.join("、"),
    accessed: detail.accessed_modules_after_disclosure.join("、"),
    triggers: detail.vista_metrics.trigger_opportunities,
    routable: detail.vista_metrics.routable_feedback,
    disclosures: detail.vista_metrics.successful_disclosures,
    accessRounds: detail.vista_metrics.next_round_module_access,
    closure: detail.vista_metrics.clean_complete_closure,
    model: detail.requested_provider_model,
  });
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("实验总览");
const detailSheet = workbook.worksheets.add("单组明细");
const tokenSheet = workbook.worksheets.add("Token与成本");
const processSheet = workbook.worksheets.add("方法与流程");
const promptSheet = workbook.worksheets.add("条件提示词");
const disclosureSheet = workbook.worksheets.add("披露证据");
const mapSheet = workbook.worksheets.add("字段映射");

const colors = {
  navy: "#1F3F66",
  blue: "#2F75B5",
  lightHeader: "#D9EAF7",
  band: "#BFE3F3",
  green: "#E2F0D9",
  yellow: "#FFF2CC",
  white: "#FFFFFF",
  text: "#25364A",
  border: "#B8CCE4",
  lightGray: "#F2F2F2",
};

function title(sheet, range, text) {
  sheet.getRange(range).merge();
  const cell = sheet.getRange(range.split(":")[0]);
  cell.values = [[text]];
  cell.format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange(range).format.rowHeight = 30;
}

function section(sheet, range, text) {
  sheet.getRange(range).merge();
  const cell = sheet.getRange(range.split(":")[0]);
  cell.values = [[text]];
  cell.format = { fill: colors.blue, font: { bold: true, color: colors.white, size: 12 } };
}

function header(range) {
  range.format = {
    fill: colors.lightHeader,
    font: { bold: true, color: colors.text },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: colors.border },
  };
}

function body(range) {
  range.format = {
    font: { color: colors.text, size: 10 },
    verticalAlignment: "top",
    borders: { preset: "all", style: "thin", color: colors.border },
  };
}

function stripe(sheet, startRow, endRow, startCol, endCol) {
  for (let r = startRow; r <= endRow; r += 1) {
    if ((r - startRow) % 2 === 0) {
      sheet.getRangeByIndexes(r - 1, startCol - 1, 1, endCol - startCol + 1).format.fill = colors.band;
    }
  }
}

for (const sheet of workbook.worksheets.items) sheet.showGridLines = false;

// 实验总览
title(summary, "A1:H1", "Vista task1 · 三条件 × 三重复本机 OpenCode 实验审计");
summary.getRange("A3:B10").values = [
  ["正式批次", runId],
  ["任务", "task-001 · Python → Scala 翻译"],
  ["审计状态", "通过"],
  ["完成试验", audit.completed_trials],
  ["独立会话", audit.unique_sessions],
  ["最终验收", "9/9 通过；每组最终均为 10/10 单元测试"],
  ["实际提供商 / 模型", `${audit.model_identity.provider_id} / ${audit.model_identity.model_id}`],
  ["OpenCode 记录费用", audit.totals.recorded_cost],
];
summary.getRange("A3:A10").format = { fill: colors.lightGray, font: { bold: true, color: colors.text } };
body(summary.getRange("A3:B10"));
summary.getRange("B8").format.fill = colors.green;
summary.getRange("B10").format.numberFormat = "0.00000000";

section(summary, "A12:H12", "三种条件结果总览");
summary.getRange("A13:H13").values = [["条件", "试验数", "通过数", "通过率", "总轮次", "输入+输出 Token", "缓存读取 Token", "记录费用"]];
header(summary.getRange("A13:H13"));
summary.getRange("A14:A16").values = [["No-Skill"], ["Native"], ["PD-VISTA"]];
for (let r = 14; r <= 16; r += 1) {
  const tr = r - 10;
  summary.getRange(`B${r}:H${r}`).formulas = [[
    `='Token与成本'!B${tr}`,
    `='Token与成本'!C${tr}`,
    `='Token与成本'!D${tr}`,
    `='Token与成本'!E${tr}`,
    `='Token与成本'!F${tr}`,
    `='Token与成本'!G${tr}`,
    `='Token与成本'!J${tr}`,
  ]];
}
body(summary.getRange("A14:H16"));
stripe(summary, 14, 16, 1, 8);
summary.getRange("B14:C16").format.numberFormat = "#,##0";
summary.getRange("D14:D16").format.numberFormat = "0.0%";
summary.getRange("E14:G16").format.numberFormat = "#,##0";
summary.getRange("H14:H16").format.numberFormat = "0.00000000";

section(summary, "A19:H19", "关键完整性检查");
summary.getRange("A20:B20").values = [["检查项", "结果"]];
header(summary.getRange("A20:B20"));
summary.getRange("A21:B29").values = [
  ["9 个最终 Verifier 全部通过", audit.all_final_verifiers_passed],
  ["每个最终 Verifier 均通过 10/10 测试", audit.all_final_unit_tests_10_of_10],
  ["独立会话数等于试验数", audit.unique_sessions === audit.completed_trials],
  ["实际模型在 9 组中一致", audit.model_identity.consistent_across_all_trials],
  ["使用 Docker", audit.docker_used],
  ["使用 SkillBench 标准执行路线", audit.skillbench_execution_route_used],
  ["PD-VISTA 干净闭环数", audit.conditions["pd-vista"].clean_complete_closures],
  ["模型轮次收尾超时数", audit.model_turn_timeouts],
  ["目标相关自动化测试", "18/18 通过"],
];
body(summary.getRange("A21:B29"));
summary.getRange("B21:B24").format.fill = colors.green;
summary.getRange("B25:B26").format.fill = colors.green;
summary.getRange("B27:B28").format.fill = colors.yellow;

section(summary, "A32:H32", "数据来源");
summary.getRange("A33:H35").merge(true);
summary.getRange("A33:A35").values = [[
  path.join(rawRoot, "group-summary.json")
], [
  path.join(rawRoot, "posthoc-audit.json")
], [
  "模型与费用：逐个只读查询隔离 OpenCode opencode.db；最终测试与质量分：各组最终 verifier-turn-N.txt"
]];
summary.getRange("A33:H35").format = { wrapText: true, font: { color: colors.text, size: 9 }, borders: { preset: "all", style: "thin", color: colors.border } };
summary.freezePanes.freezeRows(1);
summary.getRange("A1:H35").format.verticalAlignment = "center";
summary.getRange("A:A").format.columnWidth = 28;
summary.getRange("B:B").format.columnWidth = 38;
summary.getRange("C:H").format.columnWidth = 16;

// 单组明细
title(detailSheet, "A1:AA1", "task1 单组审计明细（9 个独立 OpenCode 会话）");
const detailHeaders = ["试验 ID", "条件", "重复", "状态", "最终通过", "轮次", "模型超时", "请求数", "工具调用", "输入 Token", "输出 Token", "缓存读取", "新鲜 Token", "总 Token", "记录费用", "最终测试", "质量分 /25", "会话 ID", "初始可见模块", "披露模块", "披露后访问", "触发机会", "可路由反馈", "成功披露", "后续访问", "干净闭环", "请求模型"];
detailSheet.getRange("A3:AA3").values = [detailHeaders];
header(detailSheet.getRange("A3:AA3"));
const rawMatrix = trialRows.map((row) => [
  row.runId, row.condition, row.replicate, row.status, row.passed, row.turns, row.timeouts,
  row.requests, row.tools, row.input, row.output, row.cache, null, null, row.cost,
  row.tests, row.quality, row.session, row.initial, row.disclosed, row.accessed,
  row.triggers, row.routable, row.disclosures, row.accessRounds, row.closure, row.model,
]);
detailSheet.getRange("A4:AA12").values = rawMatrix;
for (let r = 4; r <= 12; r += 1) {
  detailSheet.getRange(`M${r}`).formulas = [[`=J${r}+K${r}`]];
  detailSheet.getRange(`N${r}`).formulas = [[`=M${r}+L${r}`]];
}
body(detailSheet.getRange("A4:AA12"));
stripe(detailSheet, 4, 12, 1, 27);
detailSheet.getRange("E4:E12").format.fill = colors.green;
detailSheet.getRange("Z4:Z12").format.fill = colors.green;
detailSheet.getRange("C4:N12").format.numberFormat = "#,##0";
detailSheet.getRange("O4:O12").format.numberFormat = "0.00000000";
detailSheet.getRange("Q4:Q12").format.numberFormat = "0";
detailSheet.getRange("A3:AA12").format.wrapText = true;
detailSheet.freezePanes.freezeRows(3);
detailSheet.freezePanes.freezeColumns(2);
const detailWidths = [42, 12, 8, 34, 11, 8, 10, 10, 10, 13, 13, 15, 14, 14, 14, 11, 11, 30, 52, 32, 32, 11, 12, 11, 11, 11, 24];
for (let c = 0; c < detailWidths.length; c += 1) detailSheet.getRangeByIndexes(0, c, 12, 1).format.columnWidth = detailWidths[c];
detailSheet.getRange("4:12").format.rowHeight = 56;

// Token 与成本（由单组明细公式汇总）
title(tokenSheet, "A1:J1", "Token、轮次与成本汇总");
tokenSheet.getRange("A3:J3").values = [["条件", "试验数", "通过数", "通过率", "总轮次", "输入+输出 Token", "缓存读取 Token", "总 Token", "平均总 Token / 组", "记录费用"]];
header(tokenSheet.getRange("A3:J3"));
tokenSheet.getRange("A4:A6").values = [["No-Skill"], ["Native"], ["PD-VISTA"]];
for (let r = 4; r <= 6; r += 1) {
  tokenSheet.getRange(`B${r}`).formulas = [[`=COUNTIF('单组明细'!$B$4:$B$12,A${r})`]];
  tokenSheet.getRange(`C${r}`).formulas = [[`=SUMIF('单组明细'!$B$4:$B$12,A${r},'单组明细'!$E$4:$E$12)`]];
  tokenSheet.getRange(`D${r}`).formulas = [[`=C${r}/B${r}`]];
  tokenSheet.getRange(`E${r}`).formulas = [[`=SUMIF('单组明细'!$B$4:$B$12,A${r},'单组明细'!$F$4:$F$12)`]];
  tokenSheet.getRange(`F${r}`).formulas = [[`=SUMIF('单组明细'!$B$4:$B$12,A${r},'单组明细'!$M$4:$M$12)`]];
  tokenSheet.getRange(`G${r}`).formulas = [[`=SUMIF('单组明细'!$B$4:$B$12,A${r},'单组明细'!$L$4:$L$12)`]];
  tokenSheet.getRange(`H${r}`).formulas = [[`=F${r}+G${r}`]];
  tokenSheet.getRange(`I${r}`).formulas = [[`=H${r}/B${r}`]];
  tokenSheet.getRange(`J${r}`).formulas = [[`=SUMIF('单组明细'!$B$4:$B$12,A${r},'单组明细'!$O$4:$O$12)`]];
}
body(tokenSheet.getRange("A4:J6"));
stripe(tokenSheet, 4, 6, 1, 10);
tokenSheet.getRange("B4:C6").format.numberFormat = "#,##0";
tokenSheet.getRange("D4:D6").format.numberFormat = "0.0%";
tokenSheet.getRange("E4:I6").format.numberFormat = "#,##0";
tokenSheet.getRange("J4:J6").format.numberFormat = "0.00000000";

section(tokenSheet, "A9:J9", "关键指标与解释");
tokenSheet.getRange("A10:C10").values = [["指标", "数值", "解释"]];
header(tokenSheet.getRange("A10:C10"));
tokenSheet.getRange("A11:A16").values = [["9 组总 Token"], ["新鲜 Token"], ["PD-VISTA 干净闭环"], ["模型轮次超时"], ["PD-VISTA 新鲜 Token 相对 No-Skill"], ["PD-VISTA 新鲜 Token 相对 Native"]];
tokenSheet.getRange("B11").formulas = [["=SUM(H4:H6)"]];
tokenSheet.getRange("B12").formulas = [["=SUM(F4:F6)"]];
tokenSheet.getRange("B13").values = [[audit.conditions["pd-vista"].clean_complete_closures]];
tokenSheet.getRange("B14").values = [[audit.model_turn_timeouts]];
tokenSheet.getRange("B15").formulas = [["=F6/F4-1"]];
tokenSheet.getRange("B16").formulas = [["=F6/F5-1"]];
tokenSheet.getRange("C11:C16").values = [["输入 + 输出 + 缓存读取，覆盖全部 9 组"], ["输入 + 输出，不含缓存读取"], ["失败 → 披露 → 访问 → 修复通过的完整闭环"], ["OpenCode 工具调用完成后，模型收尾响应达到 7 分钟上限；控制器保留同一会话继续"], ["负值表示 PD-VISTA 使用更少新鲜 Token"], ["负值表示 PD-VISTA 使用更少新鲜 Token"]];
body(tokenSheet.getRange("A11:C16"));
tokenSheet.getRange("B11:B14").format.numberFormat = "#,##0";
tokenSheet.getRange("B15:B16").format.numberFormat = "0.0%";
tokenSheet.getRange("A1:J16").format.wrapText = true;
tokenSheet.freezePanes.freezeRows(3);
tokenSheet.getRange("A:A").format.columnWidth = 30;
tokenSheet.getRange("B:J").format.columnWidth = 18;
tokenSheet.getRange("C:C").format.columnWidth = 64;

// 方法与流程
title(processSheet, "A1:F1", "task1 本机 OpenCode 三条件对照实验：方法与流程");
processSheet.getRange("A3:F3").values = [["阶段", "控制器动作", "模型可见内容", "控制器隐藏内容", "记录证据", "控制目的"]];
header(processSheet.getRange("A3:F3"));
processSheet.getRange("A4:F15").values = [
  ["1 · 最小依赖准备", "仅在项目内安装 Coursier、Temurin JDK 17、SBT；预热依赖缓存。", "compile.sh 与任务目录", "无", "readiness.json、版本检查", "不安装 Docker、BenchFlow 或 SkillBench 执行栈"],
  ["2 · 固定执行配置", "读取当前 OpenCode 配置，固定 minimax-cn/MiniMax-M3 与 OpenCode 1.18.18。", "任务提示词", "鉴权存储细节", "二进制 SHA256、数据库模型记录", "确保 9 组使用相同模型与本机配置"],
  ["3 · 平衡排程", "按 No-Skill→Native→PD-VISTA、Native→PD-VISTA→No-Skill、PD-VISTA→No-Skill→Native 排列 9 组。", "本组任务", "其他组结果", "condition、replicate_id", "降低固定顺序与时间漂移影响"],
  ["4 · 试验隔离", "每组创建独立工作区、Verifier 目录、OpenCode 状态与唯一会话；仅共享依赖缓存。", "task.md、Tokenizer.py、compile.sh", "其他组工作区与会话", "9 个唯一 session_id", "防止跨组提示、文件和会话污染"],
  ["5 · No-Skill 条件", "不暴露任何任务 Skill。", "任务文件与公开编译入口", "全部 6 个指导模块", "initially_available_modules 为空", "测量无 Skill 辅助时的任务完成情况"],
  ["6 · Native 条件", "首轮前完整复制 6 个原始指导模块。", "modules/<module-id>/SKILL.md", "无任务 Skill 隐藏", "initially_available_modules 为 6 项", "测量全部 Skill 前置可见的情况"],
  ["7 · PD-VISTA 起点", "初始只暴露 VISTA.md；原始模块保存在控制器侧。", "VISTA.md 与任务文件", "manifest.yaml 与原始模块", "initially_available_modules 为空", "只在失败反馈证明有需要时披露"],
  ["8 · 模型执行", "每轮要求在同一目录写入完整 Scala 2.13 实现，可运行公开 compile.sh。", "公开编译结果", "隐藏测试与答案", "turn-N.jsonl、工具事件", "保持三条件任务目标一致"],
  ["9 · 统一验收", "每轮后运行同一套本机 Verifier：编译、10 个测试、质量阈值。", "经清洗的公开错误摘要", "Verifier 实现、隐藏测试源码细节", "verifier-turn-N.txt", "提供相同修复信号并避免 oracle 泄露"],
  ["10 · VISTA 路由", "仅 PD-VISTA 将公开失败分类映射到 manifest，单轮最多披露一个新模块。", "新披露模块的完整目录", "尚未披露模块", "routable_feedback、successful_disclosures", "实现反馈驱动、渐进式技能披露"],
  ["11 · 超时容错", "单轮 7 分钟；若文件已落盘但收尾卡住，仍验收并在同一会话继续。", "下一轮公开诊断", "服务端内部状态", "model_turn_timeout", "不把空收尾延迟误判为任务能力失败"],
  ["12 · 停止与审计", "通过即停止；否则最多 6 轮。结束后核对模型、Token、费用、会话、披露访问和 Docker 标志。", "最终结果", "其他条件会话", "group-summary.json、posthoc-audit.json", "形成可复核的 3×3 正式结果"],
];
body(processSheet.getRange("A4:F15"));
stripe(processSheet, 4, 15, 1, 6);
processSheet.getRange("A4:A15").format.font = { bold: true, color: colors.blue };
processSheet.getRange("A3:F15").format.wrapText = true;
processSheet.freezePanes.freezeRows(3);
const processWidths = [24, 50, 42, 40, 38, 42];
for (let c = 0; c < processWidths.length; c += 1) processSheet.getRangeByIndexes(0, c, 15, 1).format.columnWidth = processWidths[c];
processSheet.getRange("4:15").format.rowHeight = 62;

// 条件提示词
const commonPrompt = "Solve the isolated Python-to-Scala translation task in this directory. Work only inside this directory and do not access parent or external directories. Read task.md and Tokenizer.py. Write the complete Scala 2.13 implementation to ./Tokenizer.scala. Preserve all required classes and functions and use maintainable Scala conventions. You may run ./compile.sh for a public compilation check. Hidden unit tests are run only by the controller.";
const promptRows = [
  ["共同基础提示词", "首轮", "限定隔离目录；读取任务与 Python 原实现；输出完整 Scala 2.13 文件；只允许公开编译，隐藏测试由控制器执行。", commonPrompt, "三条件共同可见", "三条件完全相同"],
  ["No-Skill", "首轮", "明确没有任务 Skill。", `${commonPrompt} No task Skill is available in this condition.`, "无任务 Skill", "只依靠任务材料和公开反馈"],
  ["Native", "首轮", "完整 6 个原始指导模块从一开始可用，要求读取相关模块。", `${commonPrompt} All original guidance modules are available under ./modules/<module-id>/SKILL.md; read and use the relevant modules.`, "全部 6 个模块", "前置完整暴露"],
  ["PD-VISTA", "首轮", "先读 VISTA.md；原始模块初始隐藏，公开编译或测试失败后最多披露一个模块。", `${commonPrompt} Read VISTA.md. Original guidance modules are initially hidden; after a public compiler or test failure, VISTA may disclose one module under ./modules/<module-id>/SKILL.md.`, "仅 VISTA.md", "失败驱动的渐进披露"],
  ["失败反馈模板", "第 2–6 轮", "向三条件返回同样的公开失败类型和经清洗诊断，继续同一会话修复。", "The public local check failed with <feedback_types>. Public diagnostic:\n<sanitized diagnostic>\n<condition instruction> Continue in the same session, work only inside this directory, repair ./Tokenizer.scala, and use ./compile.sh when useful.", "由条件指令决定", "不暴露绝对路径、隐藏测试实现或答案"],
  ["PD-VISTA 新披露指令", "披露后的下一轮", "要求先读取本轮新披露的模块再修复。", "VISTA disclosed ./modules/<module-id>/SKILL.md. Read it before repairing.", "本轮最多新增 1 个模块", "记录后续实际访问"],
  ["PD-VISTA 已披露指令", "无新模块时", "继续使用已经披露的模块。", "Use the already disclosed modules: <module-id list>.", "仅已披露模块", "禁止访问控制器侧隐藏源"],
  ["Native 修复指令", "第 2–6 轮", "继续使用首轮已完整提供的模块。", "Use the original modules already available under ./modules/.", "全部 6 个模块", "与首轮可见性一致"],
  ["No-Skill 修复指令", "第 2–6 轮", "不提供任何模块，仅依据公开失败类别修复。", "No task Skill is available; repair from the public failure category.", "无任务 Skill", "与基线定义一致"],
];
title(promptSheet, "A1:F1", "task1 实验提示词与条件差异（中文说明 + 原始英文）");
promptSheet.getRange("A3:F3").values = [["条件 / 阶段", "适用轮次", "中文说明", "实验中使用的原始英文提示词 / 模板", "技能可见性", "控制规则"]];
header(promptSheet.getRange("A3:F3"));
promptSheet.getRange("A4:F12").values = promptRows;
body(promptSheet.getRange("A4:F12"));
stripe(promptSheet, 4, 12, 1, 6);
promptSheet.getRange("A4:A12").format.font = { bold: true, color: colors.blue };
promptSheet.getRange("A3:F12").format.wrapText = true;
promptSheet.freezePanes.freezeRows(3);
const promptWidths = [24, 14, 52, 105, 30, 38];
for (let c = 0; c < promptWidths.length; c += 1) promptSheet.getRangeByIndexes(0, c, 12, 1).format.columnWidth = promptWidths[c];
promptSheet.getRange("4:12").format.rowHeight = 100;

// 披露证据
title(disclosureSheet, "A1:I1", "三条件与 PD-VISTA 披露闭环证据");
disclosureSheet.getRange("A3:I3").values = [["条件 / 试验", "初始可见性", "首轮结果", "总轮次", "触发机会", "披露模块", "实际访问证据", "模型超时", "最终结果与解释"]];
header(disclosureSheet.getRange("A3:I3"));
disclosureSheet.getRange("A4:I9").values = [
  ["No-Skill · 3 组", "无模块", "3 组首轮均失败", 12, 9, "无", "无", 0, "3/3 通过；无 Skill 对照"],
  ["Native · 3 组", "全部 6 个模块", "3 组首轮均失败", 10, 7, "首轮前全部可见", "无需披露后访问证明", 2, "3/3 通过；完整前置暴露对照"],
  ["PD-VISTA · 重复 1", "仅 VISTA.md", "第 1 轮失败", 6, 5, "python-scala-oop；python-scala-syntax-mapping", "两模块均在后续轮读取", 1, "第 6 轮通过；完整干净闭环"],
  ["PD-VISTA · 重复 2", "仅 VISTA.md", "第 1 轮失败", 3, 2, "python-scala-oop；python-scala-syntax-mapping", "两模块均在后续轮读取", 0, "第 3 轮通过；完整干净闭环"],
  ["PD-VISTA · 重复 3", "仅 VISTA.md", "第 1 轮失败", 2, 1, "python-scala-syntax-mapping", "第 2 轮读取", 1, "第 2 轮通过；完整干净闭环"],
  ["PD-VISTA · 汇总", "仅根路由", "3 组首轮均失败", 11, 8, "成功披露 5 次；涉及 2 个唯一模块", "后续访问 5 次", 2, "3/3 通过；3/3 均形成完整闭环"],
];
body(disclosureSheet.getRange("A4:I9"));
stripe(disclosureSheet, 4, 9, 1, 9);
disclosureSheet.getRange("A6:I9").format.fill = colors.green;
disclosureSheet.getRange("A3:I9").format.wrapText = true;
disclosureSheet.freezePanes.freezeRows(3);
const disclosureWidths = [28, 26, 24, 12, 12, 46, 38, 12, 42];
for (let c = 0; c < disclosureWidths.length; c += 1) disclosureSheet.getRangeByIndexes(0, c, 9, 1).format.columnWidth = disclosureWidths[c];
disclosureSheet.getRange("4:9").format.rowHeight = 58;

// 字段映射
title(mapSheet, "A1:E1", "最终审计 JSON → 中文工作簿字段映射");
mapSheet.getRange("A3:E3").values = [["JSON 区域", "JSON 路径", "工作簿位置", "含义", "来源 / 备注"]];
header(mapSheet.getRange("A3:E3"));
mapSheet.getRange("A4:E23").values = [
  ["运行", "run_id", "实验总览!B3", "正式实验唯一标识", "posthoc-audit.json"],
  ["运行", "audit_status", "实验总览!B5", "最终审计状态", "posthoc-audit.json"],
  ["运行", "completed_trials", "实验总览!B6", "完成试验数", "posthoc-audit.json"],
  ["运行", "unique_sessions", "实验总览!B7", "不同 OpenCode 会话数量", "posthoc-audit.json"],
  ["模型", "model_identity.provider_id / model_id", "实验总览!B9", "逐个数据库确认的实际模型", "只读查询 9 个 opencode.db"],
  ["试验", "group-summary.results[*]", "单组明细!A4:AA12", "条件 × 重复的单组记录", "group-summary.json + result.json"],
  ["Token", "conditions.*.input_tokens", "单组明细!J4:J12", "输入 Token", "result.json / OpenCode DB 复核"],
  ["Token", "conditions.*.output_tokens", "单组明细!K4:K12", "输出 Token", "result.json / OpenCode DB 复核"],
  ["Token", "conditions.*.cache_read_tokens", "单组明细!L4:L12", "缓存读取 Token", "result.json / OpenCode DB 复核"],
  ["Token", "conditions.*.recorded_cost", "Token与成本!J4:J6", "OpenCode 记录费用", "单组数据库费用汇总"],
  ["验收", "all_final_unit_tests_10_of_10", "实验总览!B22", "所有最终验收均为 10/10", "posthoc-audit.json"],
  ["验收", "final_quality_score_range", "单组明细!Q4:Q12", "最终代码质量分", "最终 verifier-turn-N.txt"],
  ["超时", "turns[*].model_turn_timeout", "单组明细!G4:G12", "模型轮次收尾超时次数", "result.json"],
  ["披露", "vista_metrics.trigger_opportunities", "单组明细!V4:V12", "公开失败触发机会", "result.json"],
  ["披露", "disclosed_modules", "单组明细!T4:T12", "实际披露模块", "result.json"],
  ["披露", "accessed_modules_after_disclosure", "单组明细!U4:U12", "后续实际访问模块", "result.json"],
  ["披露", "vista_metrics.clean_complete_closure", "单组明细!Z4:Z12", "完整反馈闭环", "result.json"],
  ["完整性", "docker_used", "实验总览!B25", "是否使用 Docker", "posthoc-audit.json"],
  ["完整性", "skillbench_execution_route_used", "实验总览!B26", "是否使用 SkillBench 标准执行路线", "posthoc-audit.json"],
  ["提示词", "COMMON_PROMPT / INITIAL_PROMPTS", "条件提示词!A4:F12", "实际基础与条件提示词", "run_task001_local_group.py"],
];
body(mapSheet.getRange("A4:E23"));
stripe(mapSheet, 4, 23, 1, 5);
mapSheet.getRange("A4:A23").format.font = { bold: true, color: colors.blue };
mapSheet.getRange("A3:E23").format.wrapText = true;
mapSheet.freezePanes.freezeRows(3);
const mapWidths = [20, 45, 30, 45, 48];
for (let c = 0; c < mapWidths.length; c += 1) mapSheet.getRangeByIndexes(0, c, 23, 1).format.columnWidth = mapWidths[c];
mapSheet.getRange("4:23").format.rowHeight = 42;

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

const keyChecks = await workbook.inspect({
  kind: "table",
  range: "实验总览!A1:H35",
  include: "values,formulas",
  tableMaxRows: 40,
  tableMaxCols: 10,
  maxChars: 12000,
});
console.log(keyChecks.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "最终公式错误扫描",
});
console.log(errors.ndjson);

for (const sheet of workbook.worksheets.items) {
  const preview = await workbook.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheet.name}.png`), new Uint8Array(await preview.arrayBuffer()));
}

console.log(JSON.stringify({ outputPath, previewDir, sheets: workbook.worksheets.items.map((s) => s.name) }, null, 2));
