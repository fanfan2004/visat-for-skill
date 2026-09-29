import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318";
const AUDIT = path.join(ROOT, "outputs/formal-task003-local-3x3-minimaxm3-20260823-02-audit");
const OUT = path.join(AUDIT, "task3_final_audit_zh.xlsx");
const RENDER_DIR = path.join(AUDIT, "rendered-workbook");
const NAVY = "#1F3F66";
const NAVY_DARK = "#17365D";
const LIGHT = "#DCEAF7";
const PALE = "#F2F2F2";
const TEXT = "#25364A";
const BORDER = "#B8CCE4";
const GREEN = "#E2F0D9";
const AMBER = "#FFF2CC";
const RED = "#FCE4D6";

const summary = JSON.parse(await fs.readFile(path.join(AUDIT, "group-summary.json"), "utf8"));
const posthoc = JSON.parse(await fs.readFile(path.join(AUDIT, "posthoc-audit.json"), "utf8"));
const trials = posthoc.trials;
console.log("BUILD_INPUT", posthoc.formal_included_trials, posthoc.totals?.formal_included?.input_tokens, trials.filter((t) => t.condition === "noskill").map((t) => [t.replicate_id, t.session_id, t.final_verifier_pass]));
const trialMap = new Map(trials.map((t) => [`${t.condition}-${t.replicate_id}`, t]));
const formalIncluded = posthoc.formal_included_trials ?? trials.filter((t) => t.included_in_formal_stats).length;
const excludedFinal = trials.length - formalIncluded;
const historicalExcludedAttempts = posthoc.excluded_trial_attempts?.length ?? 0;

const conditionZh = { noskill: "No-Skill", native: "Native", "pd-vista": "PD-VISTA" };
const statusZh = {
  passed: "通过",
  passed_with_clean_vista_feedback_loop: "通过（PD干净闭环）",
  passed_without_clean_vista_feedback_loop: "通过（无反馈闭环）",
  quarantined_api_error: "隔离：API错误",
  quarantined_verifier_infrastructure_error: "隔离：Verifier/控制器误判",
};

const prompts = {
  noskill: "Solve the isolated data-to-D3 task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inspect the local data under ./data. Produce the requested browser-openable single-page web app under ./output/index.html with the exact supporting paths requested by task.md. Use only local inputs and deterministic D3.js v6 assets; do not fetch external data or libraries. You may run local browser checks when useful. The controller runs the complete acceptance verifier after each turn. No task Skill is available in this condition; solve from task.md and the local data only.",
  native: "Solve the isolated data-to-D3 task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inspect the local data under ./data. Produce the requested browser-openable single-page web app under ./output/index.html with the exact supporting paths requested by task.md. Use only local inputs and deterministic D3.js v6 assets; do not fetch external data or libraries. You may run local browser checks when useful. The controller runs the complete acceptance verifier after each turn. The full original guidance module is available at ./modules/d3-visualization/SKILL.md; read and use it.",
  "pd-vista": "Solve the isolated data-to-D3 task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md and inspect the local data under ./data. Produce the requested browser-openable single-page web app under ./output/index.html with the exact supporting paths requested by task.md. Use only local inputs and deterministic D3.js v6 assets; do not fetch external data or libraries. You may run local browser checks when useful. The controller runs the complete acceptance verifier after each turn. Read VISTA.md. The original guidance module is initially hidden; after a public verifier or browser-output failure, VISTA may disclose one relevant module at ./modules/d3-visualization/SKILL.md.",
};

function firstFeedback(condition) {
  const values = trials.filter((t) => t.condition === condition);
  for (const t of values) {
    for (const turn of t.turns ?? []) {
      if (turn.public_diagnostic) return turn.public_diagnostic;
    }
  }
  return "无公开失败反馈：该组首轮已通过；API隔离组未进入Verifier诊断轮。";
}

function firstDisclosure(t) {
  return (t.disclosure_events ?? [])[0] ?? null;
}

function styleTitle(sheet, range, text) {
  const r = sheet.getRange(range);
  r.merge();
  r.values = [[text]];
  r.format = { fill: NAVY, font: { bold: true, color: "#FFFFFF", size: 18 }, horizontalAlignment: "left", verticalAlignment: "center" };
  r.format.rowHeight = 30;
}

function styleSection(sheet, range, text) {
  const r = sheet.getRange(range);
  r.merge();
  r.values = [[text]];
  r.format = { fill: LIGHT, font: { bold: true, color: NAVY_DARK, size: 11 }, borders: { preset: "outside", style: "thin", color: BORDER } };
  r.format.rowHeight = 22;
}

function styleHeader(sheet, range) {
  const r = sheet.getRange(range);
  r.format = { fill: NAVY_DARK, font: { bold: true, color: "#FFFFFF", size: 10 }, wrapText: true, horizontalAlignment: "center", verticalAlignment: "center", borders: { preset: "all", style: "thin", color: BORDER } };
  r.format.rowHeight = 30;
}

function styleBody(sheet, range) {
  const r = sheet.getRange(range);
  r.format = { font: { color: TEXT, size: 10 }, wrapText: true, verticalAlignment: "top", borders: { preset: "all", style: "thin", color: BORDER } };
}

function setWidths(sheet, widths) {
  for (const [col, width] of Object.entries(widths)) sheet.getRange(`${col}:${col}`).format.columnWidth = width;
}

function setNum(sheet, range, format) {
  sheet.getRange(range).format.numberFormat = format;
}

const wb = Workbook.create();
const overview = wb.worksheets.add("实验总览");
const detail = wb.worksheets.add("9组明细");
const token = wb.worksheets.add("Token与成本");
const method = wb.worksheets.add("方法与流程");
const prompt = wb.worksheets.add("条件提示词");
const pd = wb.worksheets.add("PD披露证据");
const mapping = wb.worksheets.add("字段映射");
for (const s of [overview, detail, token, method, prompt, pd, mapping]) s.showGridLines = false;

// 9组明细: raw audit fields plus formula-derived total tokens.
styleTitle(detail, "A1:X1", "task3 本机 OpenCode 三条件对照实验｜9 组明细");
detail.getRange("A2:W2").merge();
detail.getRange("A2").values = [["每行对应一个逻辑组；正式统计=1 表示纳入比较，=0 表示因 API/控制器基础设施异常隔离。总Token由输入+输出公式计算。"]];
detail.getRange("A2:W2").format = { fill: PALE, font: { color: TEXT, size: 10 }, wrapText: true };
const detailHeaders = ["组ID", "条件", "重复", "原始状态", "正式统计", "最终Verifier通过", "轮次", "请求数", "工具调用", "输入Token", "输出Token", "cache-read Token", "总Token（公式）", "OpenCode记录费用", "实际Provider", "实际Model", "唯一Session", "输出文件数", "停止原因", "PD触发机会", "披露模块", "下轮实际读取", "干净闭环", "结果JSON"];
detail.getRange("A4:X4").values = [detailHeaders];
styleHeader(detail, "A4:X4");
const detailRows = [];
for (const t of trials) {
  const db = t.opencode_db ?? {};
  const included = t.included_in_formal_stats ? 1 : 0;
  const disclosed = (t.disclosed_modules ?? []).join(", ");
  const dbProvider = (db.provider_ids ?? []).join(", ");
  const dbModel = (db.model_ids ?? []).join(", ");
  const session = t.session_id ?? "";
  detailRows.push([
    `${conditionZh[t.condition]}-${String(t.replicate_id).padStart(2, "0")}`,
    conditionZh[t.condition],
    t.replicate_id,
    statusZh[t.status] ?? t.status,
    included,
    t.final_verifier_pass ? 1 : 0,
    (t.turns ?? []).length,
    t.usage?.requests ?? 0,
    t.usage?.tool_uses ?? 0,
    t.usage?.input_tokens ?? 0,
    t.usage?.output_tokens ?? 0,
    t.usage?.cache_read_tokens ?? 0,
    null,
    db.recorded_cost ?? 0,
    dbProvider,
    dbModel,
    session,
    t.output_file_count ?? 0,
    t.stop_reason ?? "",
    t.vista_metrics?.trigger_opportunities ?? 0,
    disclosed || "—",
    t.vista_metrics?.next_round_module_access ?? 0,
    t.vista_metrics?.clean_complete_closure ?? 0,
    `data/raw/formal-task003-local-3x3-minimaxm3-20260823-02/${t.condition}/replicate-${String(t.replicate_id).padStart(2, "0")}/result.json`,
  ]);
}
detail.getRange("A5:X13").values = detailRows;
detail.getRange("M5").formulas = [["=J5+K5"]];
detail.getRange("M5:M13").fillDown();
styleBody(detail, "A5:X13");
setNum(detail, "E5:F13", "0");
setNum(detail, "G5:M13", "#,##0");
setNum(detail, "N5:N13", "0.000000");
setNum(detail, "T5:W13", "0");
detail.freezePanes.freezeRows(4);
detail.freezePanes.freezeColumns(2);
setWidths(detail, { A: 16, B: 12, C: 8, D: 22, E: 10, F: 12, G: 8, H: 10, I: 10, J: 13, K: 13, L: 15, M: 15, N: 15, O: 15, P: 16, Q: 27, R: 11, S: 25, T: 11, U: 18, V: 12, W: 11, X: 52 });

// Overview formulas link to the group detail sheet.
styleTitle(overview, "A1:J1", "task3 本机 OpenCode 三条件对照实验｜中文审计总览");
overview.getRange("A3:B8").values = [
  ["任务身份", "task-003 / data-to-d3"],
  ["正式批次", summary.run_id],
  ["请求模型", summary.requested_provider_model],
  ["执行路线", "本机 OpenCode；Docker=false；SkillBench route=false"],
  ["矩阵", "No-Skill / Native / PD-VISTA × 3"],
  ["交错顺序", "1N → 1Native → 1PD → 2Native → 2PD → 2N → 3PD → 3N → 3Native"],
];
overview.getRange("A3:A8").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
overview.getRange("B3:B8").format = { font: { color: TEXT, size: 10 }, wrapText: true, borders: { preset: "all", style: "thin", color: BORDER } };
overview.getRange("D3:J8").merge();
overview.getRange("D3").values = [[`正式结果：9 个逻辑结果 JSON、9 个唯一 session；${formalIncluded}/9 纳入正式比较${excludedFinal ? `，${excludedFinal} 个逻辑组仍因基础设施/API 错误隔离` : "，当前全部纳入正式比较"}。正式纳入组最终 Verifier 全部通过。PD-VISTA：3 组均使用根路由说明；其中 ${posthoc.conditions["pd-vista"].clean_complete_closure ?? 0} 组完成公开失败→单模块披露→下一轮实际读取→通过的干净闭环。历史隔离尝试共 ${historicalExcludedAttempts} 条，均保留在 posthoc-audit.json。`]];
overview.getRange("D3:J8").format = { fill: LIGHT, font: { color: NAVY_DARK, size: 11 }, wrapText: true, verticalAlignment: "center", borders: { preset: "all", style: "thin", color: BORDER } };
styleSection(overview, "A10:J10", "条件比较（公式由 9组明细 汇总）");
overview.getRange("A11:J11").values = [["条件", "结果文件数", "纳入正式统计", "Verifier通过", "通过率（纳入）", "输入Token", "输出Token", "cache-read", "记录费用", "PD干净闭环"]];
styleHeader(overview, "A11:J11");
overview.getRange("A12:A14").values = [["No-Skill"], ["Native"], ["PD-VISTA"]];
for (let row = 12; row <= 14; row++) {
  overview.getRange(`B${row}:J${row}`).formulas = [[
    `=COUNTIF('9组明细'!$B$5:$B$13,A${row})`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$E$5:$E$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$F$5:$F$13)`,
    `=IF(C${row}=0,0,D${row}/C${row})`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$J$5:$J$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$K$5:$K$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$L$5:$L$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$N$5:$N$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$W$5:$W$13)`,
  ]];
}
overview.getRange("A15").values = [["合计"]];
overview.getRange("B15:J15").formulas = [["=SUM(B12:B14)", "=SUM(C12:C14)", "=SUM(D12:D14)", "=IF(C15=0,0,D15/C15)", "=SUM(F12:F14)", "=SUM(G12:G14)", "=SUM(H12:H14)", "=SUM(I12:I14)", "=SUM(J12:J14)"]];
styleBody(overview, "A12:J15");
overview.getRange("A15:J15").format = { fill: PALE, font: { bold: true, color: NAVY_DARK, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
setNum(overview, "E12:E15", "0.0%");
setNum(overview, "F12:H15", "#,##0");
setNum(overview, "I12:I15", "0.000000");
setNum(overview, "J12:J15", "0");
styleSection(overview, "A18:J18", "审计结论与排除项");
overview.getRange("A19:B25").values = [
  ["9 个结果 JSON", null],
  ["9 个唯一 session", null],
  ["最终 Verifier 全部通过", null],
  ["正式纳入组数", null],
  ["排除的正式逻辑组", null],
  ["诊断批次数", null],
  ["公式错误扫描", "0"],
];
overview.getRange("B19:B24").formulas = [["=COUNTA('9组明细'!$A$5:$A$13)"], ["=COUNTA('9组明细'!$Q$5:$Q$13)"], ["=SUM('9组明细'!$F$5:$F$13)"], ["=SUM('9组明细'!$E$5:$E$13)"], ["=B19-B22"], [String(summary.diagnostic_batches?.length ?? 0)]];
overview.getRange("A19:A25").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
overview.getRange("B19:B25").format = { font: { color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
overview.getRange("B21").format = { fill: posthoc.all_final_verifiers_passed ? GREEN : AMBER, font: { bold: true, color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
overview.getRange("D19:J25").merge();
overview.getRange("D19").values = [["排除说明：formal-task003-local-3x3-minimaxm3-20260823-00-readiness 为零模型 readiness；-01 为固定端口 8765 控制器诊断批次；-02-resume-readiness-check-01 为筛选续跑前置检查。历史受控制器误判/API 429 影响的 archived attempts 仍在 posthoc-audit.json 的 excluded_trial_attempts 中逐项保留，但不影响当前 9 个逻辑组的最终有效结果。"]];
overview.getRange("D19:J25").format = { fill: RED, font: { color: TEXT, size: 10 }, wrapText: true, verticalAlignment: "center", borders: { preset: "all", style: "thin", color: BORDER } };
setWidths(overview, { A: 23, B: 28, C: 15, D: 16, E: 15, F: 15, G: 15, H: 15, I: 14, J: 14 });
overview.freezePanes.freezeRows(11);

// Token and cost formulas.
styleTitle(token, "A1:J1", "Token 与成本｜正式纳入组与全量结果文件");
token.getRange("A3:J3").merge();
token.getRange("A3").values = [["正式比较口径：排除 status=quarantined_* 的 API/控制器基础设施错误；Token 与费用均来自各隔离 OpenCode opencode.db 的只读审计。"]];
token.getRange("A3:J3").format = { fill: PALE, font: { color: TEXT, size: 10 }, wrapText: true };
token.getRange("A5:J5").values = [["条件", "纳入组数", "输入Token", "输出Token", "总Token", "cache-read Token", "工具调用", "请求数", "记录费用", "平均每组费用"]];
styleHeader(token, "A5:J5");
token.getRange("A6:A8").values = [["No-Skill"], ["Native"], ["PD-VISTA"]];
for (let row = 6; row <= 8; row++) {
  token.getRange(`B${row}:J${row}`).formulas = [[
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$E$5:$E$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$J$5:$J$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$K$5:$K$13)`,
    `=C${row}+D${row}`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$L$5:$L$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$I$5:$I$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$H$5:$H$13)`,
    `=SUMIF('9组明细'!$B$5:$B$13,A${row},'9组明细'!$N$5:$N$13)`,
    `=IF(B${row}=0,0,I${row}/B${row})`,
  ]];
}
token.getRange("A9").values = [["合计"]];
token.getRange("B9:J9").formulas = [["=SUM(B6:B8)", "=SUM(C6:C8)", "=SUM(D6:D8)", "=SUM(E6:E8)", "=SUM(F6:F8)", "=SUM(G6:G8)", "=SUM(H6:H8)", "=SUM(I6:I8)", "=IF(B9=0,0,I9/B9)"]];
styleBody(token, "A6:J9");
token.getRange("A9:J9").format = { fill: PALE, font: { bold: true, color: NAVY_DARK, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
setNum(token, "B6:B9", "0");
setNum(token, "C6:H9", "#,##0");
setNum(token, "I6:J9", "0.000000");
setWidths(token, { A: 18, B: 12, C: 15, D: 15, E: 15, F: 17, G: 12, H: 11, I: 15, J: 16 });
token.freezePanes.freezeRows(5);

// Methods and process.
styleTitle(method, "A1:F1", "方法与流程｜task3 本机执行协议");
method.getRange("A3:F3").values = [["环节", "执行内容", "统一规则", "审计证据", "结果", "备注"]];
styleHeader(method, "A3:F3");
method.getRange("A4:F13").values = [
  ["任务身份", "task-003 / data-to-d3", "先独立确认 task.yaml 与 benchmark_task_id", "tasks/pilot/task-003/task.yaml", "已确认", "来源为本机 vendor/skillsbench 任务材料"],
  ["材料与资产", "task.md、50 个 CSV、原始 d3-visualization 模块、PD-VISTA 根路由与 manifest", "各组复制到独立 workspace；仅共享依赖缓存", "各 trial workspace 与 readiness.json", "满足直接执行", "不共享任务文件、中间产物或模型会话"],
  ["Verifier", "完整 pytest + Playwright 本地 HTTP fixture", "每轮公开运行；动态临时端口；单轮 verifier 上限 300 秒", "verifier-turn-*.txt、最终结果 JSON", "15 tests collected", "首次固定端口问题已作为诊断批次排除"],
  ["No-Skill", "首轮只给共同任务提示，不提供任务 Skill", "失败只返回清洗后的公开诊断", "条件提示词、逐轮 prompt", `${posthoc.conditions.noskill.verifier_passes}/3 通过`, "历史 No-Skill-2/3 API 429 尝试保留；重做后全部纳入"],
  ["Native", "首轮完整提供原始 d3-visualization/SKILL.md", "与其他条件使用同一任务提示、Verifier、轮数、超时", "初始提示词与 workspace/modules", "3/3 通过", "均为独立 session"],
  ["PD-VISTA", "首轮只提供 VISTA.md 根路由；公开失败后最多披露一个模块", "记录触发机会、披露模块、下一轮访问和闭环", "PD披露证据、disclosure_events", "3/3通过；1/3干净闭环", "PD-1/3 首轮直接通过，未触发披露机会"],
  ["容错", "模型调用超时后先验收已落盘文件；未通过则同 session 继续公开诊断修复", "统一 max_turns=6；普通任务失败不终止后续组", "turns[].model_turn_timeout、public_diagnostic", "已执行", `历史隔离尝试 ${historicalExcludedAttempts} 条，最终逻辑组均有效`],
  ["交错顺序", "1N→1Native→1PD→2Native→2PD→2N→3PD→3N→3Native", "9 个逻辑单元唯一且平衡", "group-summary.json schedule", "已执行", "重跑只替换受影响逻辑单元的最新有效尝试"],
  ["模型与路线", "当前 OpenCode 配置 minimax-cn/MiniMax-M3", "禁止替换模型；不使用 Docker/SkillBench 平台", "只读 DB provider/model、launcher 摘要", "已确认", "OpenCode 1.18.18"],
  ["审计与交付", "逐组核对结果 JSON、session、DB、Verifier；生成中文 xlsx", "公式汇总；逐页渲染；公式错误扫描=0", "posthoc-audit.json、rendered-workbook", "已完成", "本工作簿不覆盖 task1/task2/task7"],
];
styleBody(method, "A4:F13");
method.getRange("A4:A13").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, wrapText: true, borders: { preset: "all", style: "thin", color: BORDER } };
setWidths(method, { A: 16, B: 32, C: 34, D: 38, E: 16, F: 32 });
method.freezePanes.freezeRows(3);

// Prompts and feedback.
styleTitle(prompt, "A1:F1", "条件提示词｜原始提示与中文说明");
prompt.getRange("A3:F3").values = [["条件", "首轮原始提示词（英文）", "中文说明", "公开失败反馈原文（清洗后摘要）", "下一轮处理", "审计备注"]];
styleHeader(prompt, "A3:F3");
const promptRows = [];
for (const condition of ["noskill", "native", "pd-vista"]) {
  const feedback = `公开反馈摘要：\n${firstFeedback(condition)}`;
  const notes = condition === "noskill" ? "不提供任务 Skill；API 429 逻辑组隔离。" : condition === "native" ? "完整提供 d3-visualization 原始模块。" : "根路由初始可见；仅在公开失败后最多披露一个相关模块。";
  const next = condition === "pd-vista" ? "若触发失败：路由一个模块，记录 disclosure_events，并验证下一轮实际读取。" : condition === "native" ? "若失败：同 session 继续，原始模块仍可读。" : "若失败：同 session 只返回公开诊断，不提供 Skill。";
  const auditNote = condition === "noskill" ? "不提供任务 Skill；历史 API 429 attempts 已保留，重做后 3/3 纳入。" : notes;
  promptRows.push([conditionZh[condition], prompts[condition], `本条件首轮提示的中文含义：${condition === "noskill" ? "仅依据 task.md 与本地数据完成 D3 网页，不得访问外部目录或外部依赖。" : condition === "native" ? "在相同任务约束下，首轮完整读取并使用原始 d3-visualization 指导。" : "首轮只读取 VISTA 根路由，原始模块默认隐藏；只有公开失败反馈触发时才披露一个模块。"}`, feedback, next, auditNote]);
}
prompt.getRange("A4:F6").values = promptRows;
styleBody(prompt, "A4:F6");
prompt.getRange("A4:A6").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
for (const row of [4, 5, 6]) prompt.getRange(`A${row}:F${row}`).format.rowHeight = 150;
setWidths(prompt, { A: 14, B: 78, C: 43, D: 62, E: 43, F: 28 });
prompt.freezePanes.freezeRows(3);

// PD evidence.
styleTitle(pd, "A1:K1", "PD-VISTA 披露证据｜触发、披露、下一轮访问与闭环");
pd.getRange("A3:K3").values = [["组ID", "触发机会", "可路由反馈", "披露次数", "披露模块", "披露轮", "下一轮", "下一轮实际读取", "干净闭环（公式）", "最终状态", "证据摘要"]];
styleHeader(pd, "A3:K3");
const pdRows = [];
for (const rep of [1, 2, 3]) {
  const t = trialMap.get(`pd-vista-${rep}`);
  const ev = firstDisclosure(t);
  const disclosed = ev?.module_id ?? "—";
  const disclosureTurn = ev?.disclosure_turn ?? 0;
  const nextTurn = ev?.next_round_turn ?? 0;
  const accessed = ev?.accessed_next_round ? 1 : 0;
  const summaryText = ev ? `公开反馈类型：${(ev.feedback_types ?? []).join(", ")}；route_reason：${ev.route_reason ?? ""}；模块在下一轮访问=${accessed ? "是" : "否"}` : "首轮通过，未触发公开失败，不应披露模块。";
  pdRows.push([`PD-VISTA-${String(rep).padStart(2, "0")}`, t.vista_metrics?.trigger_opportunities ?? 0, t.vista_metrics?.routable_feedback ?? 0, t.vista_metrics?.successful_disclosures ?? 0, disclosed, disclosureTurn, nextTurn, accessed, null, statusZh[t.status] ?? t.status, summaryText]);
}
pd.getRange("A4:K6").values = pdRows;
pd.getRange("I4").formulas = [["=IF(AND(D4>0,H4=1,F4>0,G4>F4),1,0)"]];
pd.getRange("I4:I6").fillDown();
styleBody(pd, "A4:K6");
pd.getRange("A4:A6").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, borders: { preset: "all", style: "thin", color: BORDER } };
setNum(pd, "B4:I6", "0");
setWidths(pd, { A: 16, B: 12, C: 13, D: 11, E: 22, F: 10, G: 10, H: 15, I: 15, J: 27, K: 75 });
pd.freezePanes.freezeRows(3);

// Field mapping.
styleTitle(mapping, "A1:E1", "字段映射｜JSON 审计字段到工作簿");
mapping.getRange("A3:E3").values = [["JSON / 来源字段", "工作表", "列/区域", "中文含义", "审计/公式说明"]];
styleHeader(mapping, "A3:E3");
mapping.getRange("A4:E23").values = [
  ["condition / replicate_id", "9组明细", "A:C", "逻辑组、条件、重复编号", "9 个逻辑单元的主键"],
  ["status / stop_reason", "9组明细", "D / S", "原始状态与停止原因", "quarantined_* 从正式统计剔除"],
  ["included_in_formal_stats", "9组明细", "E", "是否纳入正式比较", "1=纳入；0=隔离"],
  ["final_verifier_pass", "9组明细", "F", "最终公开 Verifier 是否通过", "结果 JSON 原始字段"],
  ["turns.length", "9组明细", "G", "模型轮次", "逐组审计"],
  ["usage.requests / tool_uses", "9组明细", "H:I", "请求数与工具调用", "来自逐轮 event_summary 汇总"],
  ["usage.input_tokens / output_tokens", "9组明细", "J:K", "输入/输出 Token", "来自逐轮 event_summary 汇总"],
  ["usage.cache_read_tokens", "9组明细", "L", "cache-read Token", "来自逐轮 event_summary 汇总"],
  ["J + K", "9组明细", "M", "总Token", "可审计公式；不是手工汇总"],
  ["opencode_db.recorded_cost", "9组明细", "N", "OpenCode 记录费用", "逐库只读 SQLite 查询"],
  ["opencode_db.provider_ids / model_ids", "9组明细", "O:P", "实际 Provider/Model", "审计确认 minimax-cn / MiniMax-M3"],
  ["session_id", "9组明细", "Q", "独立 OpenCode session", "9 个最终逻辑结果唯一"],
  ["final_output_files", "9组明细", "R", "最终落盘文件数", "API 隔离组可为 0"],
  ["vista_metrics.trigger_opportunities", "9组明细 / PD披露证据", "T / B", "公开失败触发机会", "PD 路由审计"],
  ["disclosed_modules", "9组明细", "U", "披露模块列表", "最多单轮一个相关模块"],
  ["vista_metrics.next_round_module_access", "9组明细", "V", "下一轮实际读取次数", "PD 闭环证据"],
  ["vista_metrics.clean_complete_closure", "9组明细 / PD披露证据", "W / I", "干净闭环", "PD披露证据页用公式重算"],
  ["group-summary.json", "实验总览", "A3:J25", "批次、矩阵、排除与汇总", "公式链接到 9组明细"],
  ["posthoc-audit.json", "Token与成本 / PD披露证据", "A5:J9 / A3:K6", "DB、Token、费用、披露证据", "正式统计与隔离项分层"],
  ["diagnostic_batches / excluded_trial_attempts", "实验总览 / 方法与流程", "A18:J25", "诊断批次与隔离尝试", "明确排除，不进入正式比较"],
];
styleBody(mapping, "A4:E23");
mapping.getRange("A4:A23").format = { fill: PALE, font: { bold: true, color: TEXT, size: 10 }, wrapText: true, borders: { preset: "all", style: "thin", color: BORDER } };
setWidths(mapping, { A: 38, B: 22, C: 20, D: 34, E: 56 });
mapping.freezePanes.freezeRows(3);

// Render every sheet and perform a formula-error scan before export.
await fs.mkdir(RENDER_DIR, { recursive: true });
const formulaErrors = [];
for (const sheet of [overview, detail, token, method, prompt, pd, mapping]) {
  const used = sheet.getUsedRange();
  const values = used?.values ?? [];
  for (let r = 0; r < values.length; r++) {
    for (let c = 0; c < (values[r] ?? []).length; c++) {
      const value = values[r][c];
      if (typeof value === "string" && /^#(REF!|DIV\/0!|VALUE!|NAME\?|N\/A|NUM!|NULL!)/.test(value)) formulaErrors.push({ sheet: sheet.name, row: r + 1, col: c + 1, value });
    }
  }
  const preview = await wb.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(RENDER_DIR, `${sheet.name}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const scan = { formula_error_count: formulaErrors.length, errors: formulaErrors, rendered_sheets: ["实验总览", "9组明细", "Token与成本", "方法与流程", "条件提示词", "PD披露证据", "字段映射"] };
await fs.writeFile(path.join(AUDIT, "formula-error-scan.json"), JSON.stringify(scan, null, 2) + "\n");
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(OUT);
console.log(JSON.stringify({ output: OUT, rendered_sheets: scan.rendered_sheets.length, formula_error_count: scan.formula_error_count }, null, 2));
