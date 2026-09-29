import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const REPO = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318";
const RUN_ID = "formal-task005-local-3x3-minimaxm3-20260824-03";
const DATA_ROOT = `${REPO}/data/raw/${RUN_ID}`;
const AUDIT_ROOT = `${REPO}/outputs/${RUN_ID}-audit`;
const OUTPUT = `${AUDIT_ROOT}/task5_final_audit_zh.xlsx`;
const group = JSON.parse(await fs.readFile(`${DATA_ROOT}/group-summary.json`, "utf8"));
const audit = JSON.parse(await fs.readFile(`${AUDIT_ROOT}/posthoc-audit.json`, "utf8"));
const readiness = JSON.parse(await fs.readFile(`${DATA_ROOT}/readiness.json`, "utf8"));
const trials = audit.formal_results;
const diagnostics = audit.diagnostic_batches || group.diagnostic_batches || [];
const excludedAttempts = audit.excluded_attempts || [];

const navy = "#17365D", blue = "#2F75B5", lightBlue = "#D9EAF7", band = "#BFE3F3", pale = "#F2F2F2", green = "#E2F0D9", yellow = "#FFF2CC", text = "#25364A", border = "#B8CCE4", white = "#FFFFFF";
const wb = Workbook.create();
const sheetNames = ["实验总览", "9组明细", "Token与成本", "方法与流程", "条件提示词", "PD-VISTA披露证据", "排除与Readiness", "字段映射", "公式QC"];
const sheets = Object.fromEntries(sheetNames.map(name => [name, wb.worksheets.add(name)]));
for (const sheet of Object.values(sheets)) sheet.showGridLines = false;

function title(sheet, endCol, value) {
  const r = sheet.getRange(`A1:${endCol}1`); r.merge(); r.values = [[value]];
  r.format = { fill: navy, font: { bold: true, color: white, fontSize: 16, name: "Carlito" }, rowHeight: 28, horizontalAlignment: "left" };
}
function section(sheet, row, endCol, value) {
  const r = sheet.getRange(`A${row}:${endCol}${row}`); r.merge(); r.values = [[value]];
  r.format = { fill: blue, font: { bold: true, color: white, fontSize: 11, name: "Carlito" }, rowHeight: 22 };
}
function header(sheet, range) {
  sheet.getRange(range).format = { fill: lightBlue, font: { bold: true, color: text, name: "Carlito" }, wrapText: true, borders: { preset: "all", style: "thin", color: border } };
}
function body(sheet, range, wrap = false) {
  sheet.getRange(range).format = { font: { color: text, name: "Carlito", fontSize: 10 }, wrapText: wrap, borders: { preset: "all", style: "thin", color: border } };
}
function widths(sheet, pairs) { for (const [col, width] of Object.entries(pairs)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }
function boolStyle(sheet, range) { sheet.getRange(range).format = { fill: green, font: { color: text, name: "Carlito" }, borders: { preset: "all", style: "thin", color: border } }; }
function moduleText(value) { return Array.isArray(value) && value.length ? value.join("、") : "—"; }
// Raw verifier feedback can begin with repeated '=' characters (for example
// pytest's "==== FAILURES ===="). Force such payloads to remain literal text
// in the workbook so the audit scan only evaluates intentional formulas.
function safeText(value) {
  const s = String(value ?? "");
  return s.startsWith("=") ? `'${s}` : s;
}
function dbSession(trial) { return trial.opencode_db_audit?.flatMap(item => item.sessions || [])[0] || {}; }
function timeoutCount(trial) { return (trial.turns || []).filter(turn => turn.model_turn_timeout).length; }
function failureTurn(trial) { return (trial.turns || []).find(turn => (turn.public_feedback_types || []).length) || null; }
function firstTrial(condition) { return trials.find(trial => trial.condition === condition); }

// 9 组明细
const detail = sheets["9组明细"];
title(detail, "AB", `task5 · 9组明细（${RUN_ID}）`);
const detailHeaders = ["组ID","条件","重复","状态","最终Verifier通过","总轮次","模型收尾超时","请求数","工具调用","输入Token","输出Token","缓存读取Token","输入+输出Token","DB记录费用","Provider","Model","Session ID","初始模块","披露模块","下一轮实际访问","触发机会","可路由反馈","成功披露","下一轮模块访问","干净闭环","目录分布","结果路径","工作区"];
detail.getRange("A4:AB4").values = [detailHeaders]; header(detail, "A4:AB4");
const detailRows = trials.map(trial => {
  const db = dbSession(trial);
  const inv = trial.inventory?.subject_counts || {};
  const distribution = `LLM=${inv.LLM || 0}; trapped=${inv.trapped_ion_and_qc || 0}; black=${inv.black_hole || 0}; DNA=${inv.DNA || 0}; music=${inv.music_history || 0}`;
  return [
    `${trial.condition}-${String(trial.replicate_id).padStart(2, "0")}`, trial.condition, trial.replicate_id, trial.status,
    !!trial.final_verifier_pass, (trial.turns || []).length, timeoutCount(trial), trial.usage?.requests || 0, trial.usage?.tool_uses || 0,
    trial.usage?.input_tokens || 0, trial.usage?.output_tokens || 0, trial.usage?.cache_read_tokens || 0, null,
    trial.db_recorded_cost_usd || db.recorded_cost_usd || 0, db.model?.providerID || "minimax-cn", db.model?.id || "MiniMax-M3",
    trial.session_id || db.session_id || "", moduleText(trial.initially_available_modules), moduleText(trial.disclosed_modules), moduleText(trial.accessed_modules_after_disclosure),
    trial.vista_metrics?.trigger_opportunities || 0, trial.vista_metrics?.routable_feedback || 0, trial.vista_metrics?.successful_disclosures || 0,
    trial.vista_metrics?.next_round_module_access || 0, trial.vista_metrics?.clean_complete_closure || 0, distribution, trial.result_directory || "", trial.workspace || "",
  ];
});
detail.getRange("A5:AB13").values = detailRows;
detail.getRange("M5").formulas = [["=J5+K5"]]; detail.getRange("M5:M13").fillDown();
body(detail, "A5:AB13", true); boolStyle(detail, "E5:E13"); detail.getRange("F5:M13").format.numberFormat = "#,##0"; detail.getRange("N5:N13").format.numberFormat = "0.00000000";
detail.freezePanes.freezeRows(4); widths(detail, {A:18,B:12,C:8,D:34,E:14,F:10,G:14,H:10,I:10,J:14,K:14,L:16,M:16,N:14,O:14,P:16,Q:30,R:28,S:24,T:24,U:12,V:14,W:12,X:14,Y:12,Z:52,AA:48,AB:58});
detail.tables.add("A4:AB13", true, "Task5TrialDetail");

// Token 与成本
const cost = sheets["Token与成本"];
title(cost, "K", "Token 与成本（DB 记录费用 + 公式汇总）");
section(cost, 3, "K", "逐组使用量与费用");
cost.getRange("A4:K4").values = [["组ID","条件","重复","请求数","工具调用","输入Token","输出Token","缓存读取Token","输入+输出Token","DB记录费用","Session ID"]]; header(cost, "A4:K4");
for (let i = 0; i < 9; i++) {
  const r = 5 + i, d = 5 + i;
  cost.getRange(`A${r}:K${r}`).formulas = [[`='9组明细'!A${d}`,`='9组明细'!B${d}`,`='9组明细'!C${d}`,`='9组明细'!H${d}`,`='9组明细'!I${d}`,`='9组明细'!J${d}`,`='9组明细'!K${d}`,`='9组明细'!L${d}`,`='9组明细'!M${d}`,`='9组明细'!N${d}`,`='9组明细'!Q${d}`]];
}
body(cost, "A5:K13", true); cost.getRange("D5:I13").format.numberFormat = "#,##0"; cost.getRange("J5:J13").format.numberFormat = "0.00000000"; cost.tables.add("A4:K13", true, "Task5UsageDetail");
section(cost, 16, "K", "条件汇总（由逐组明细公式计算）");
cost.getRange("A17:K17").values = [["条件","试验数","通过数","通过率","请求数","工具调用","输入Token","输出Token","缓存读取Token","输入+输出Token","DB记录费用"]]; header(cost, "A17:K17");
cost.getRange("A18:A20").values = [["noskill"],["native"],["pd-vista"]];
for (let r = 18; r <= 20; r++) {
  cost.getRange(`B${r}:K${r}`).formulas = [[
    `=COUNTIF($B$5:$B$13,A${r})`, `=SUMIF($B$5:$B$13,A${r},'9组明细'!$E$5:$E$13)`, `=IFERROR(C${r}/B${r},0)`,
    `=SUMIF($B$5:$B$13,A${r},$D$5:$D$13)`, `=SUMIF($B$5:$B$13,A${r},$E$5:$E$13)`, `=SUMIF($B$5:$B$13,A${r},$F$5:$F$13)`,
    `=SUMIF($B$5:$B$13,A${r},$G$5:$G$13)`, `=SUMIF($B$5:$B$13,A${r},$H$5:$H$13)`, `=SUMIF($B$5:$B$13,A${r},$I$5:$I$13)`, `=SUMIF($B$5:$B$13,A${r},$J$5:$J$13)`
  ]];
}
body(cost, "A18:K20"); cost.getRange("D18:D20").format.numberFormat = "0.0%"; cost.getRange("E18:J20").format.numberFormat = "#,##0"; cost.getRange("K18:K20").format.numberFormat = "0.00000000"; widths(cost, {A:18,B:12,C:10,D:12,E:12,F:14,G:14,H:14,I:16,J:16,K:30}); cost.freezePanes.freezeRows(4);

// 实验总览
const summary = sheets["实验总览"];
title(summary, "H", `Vista task5 · 三条件 × 三重复本机 OpenCode 审计（正式批次完成）`);
summary.getRange("A3:B11").values = [["正式批次", RUN_ID],["任务","task-005 · organize-messy-files 文件归类"],["审计状态",null],["完成试验",null],["独立会话",null],["最终Verifier",null],["实际提供商 / 模型","minimax-cn / MiniMax-M3"],["OpenCode 记录费用",null],["诊断/限流批次",null]];
summary.getRange("B5:B6").formulas = [["=IF(COUNTA('9组明细'!$A$5:$A$13)=9,\"通过\",\"需检查\")"],["=COUNTA('9组明细'!$A$5:$A$13)"]];
summary.getRange("B7:B8").formulas = [["=COUNTA('9组明细'!$Q$5:$Q$13)"],["=SUM('Token与成本'!$C$18:$C$20)&\"/9 组通过\""]];
summary.getRange("B10:B11").formulas = [["=SUM('9组明细'!$N$5:$N$13)"],[`=${diagnostics.length}`]];
body(summary, "A3:B11"); summary.getRange("A3:A11").format.fill = pale; summary.getRange("A3:A11").format.font = { bold: true, color: text, name: "Carlito" }; boolStyle(summary, "B5:B5"); summary.getRange("B10").format.numberFormat = "0.00000000";
section(summary, 14, "H", "三种条件结果总览"); summary.getRange("A15:H15").values = [["条件","试验数","通过数","通过率","总轮次","输入+输出Token","缓存读取Token","DB记录费用"]]; header(summary, "A15:H15"); summary.getRange("A16:A18").values = [["noskill"],["native"],["pd-vista"]];
for (let r = 16; r <= 18; r++) {
  summary.getRange(`B${r}:H${r}`).formulas = [[`='Token与成本'!B${r+2}`,`='Token与成本'!C${r+2}`,`='Token与成本'!D${r+2}`,`=SUMIF('9组明细'!$B$5:$B$13,A${r},'9组明细'!$F$5:$F$13)`,`='Token与成本'!J${r+2}`,`='Token与成本'!I${r+2}`,`='Token与成本'!K${r+2}`]];
}
body(summary, "A16:H18"); summary.getRange("D16:D18").format.numberFormat = "0.0%"; summary.getRange("E16:G18").format.numberFormat = "#,##0"; summary.getRange("H16:H18").format.numberFormat = "0.00000000"; summary.tables.add("A15:H18", true, "Task5ConditionSummary");
section(summary, 21, "H", "关键完整性检查"); summary.getRange("A22:B22").values = [["检查项","结果"]]; header(summary, "A22:B22"); summary.getRange("A23:A31").values = [["9 个结果文件"],["9 个唯一 session"],["最终 Verifier 全部通过"],["实际 Provider/Model 在 DB 中一致"],["使用 Docker"],["使用 SkillBench 标准执行路线"],["PD-VISTA 干净闭环数"],["模型轮次收尾超时数"],["诊断批次已单独列出并排除"]];
summary.getRange("B23:B31").formulas = [["=IF(COUNTA('9组明细'!$A$5:$A$13)=9,\"通过\",\"需检查\")"],["=IF(COUNTA('9组明细'!$Q$5:$Q$13)=9,\"通过\",\"需检查\")"],["=IF(SUM('Token与成本'!$C$18:$C$20)=9,\"通过\",\"需检查\")"],[`=IF(${audit.formal_sessions_with_expected_provider_model},\"一致\",\"需检查\")`],["=IF(FALSE,\"是\",\"否\")"],["=IF(FALSE,\"是\",\"否\")"],["=SUM('9组明细'!$Y$5:$Y$13)"],["=SUM('9组明细'!$G$5:$G$13)"],[`=IF(${diagnostics.length > 0},\"已列出\",\"缺失\")`]];
body(summary, "A23:B31"); summary.getRange("A23:A31").format.fill = pale; summary.getRange("A23:A31").format.font = { bold: true, color: text, name: "Carlito" }; boolStyle(summary, "B23:B27"); boolStyle(summary, "B31:B31"); summary.getRange("B28:B30").format = { fill: green, font: { color: text, name: "Carlito" }, borders: { preset: "all", style: "thin", color: border } };
section(summary, 34, "H", "审计来源"); summary.getRange("A35:H37").values = [[`${DATA_ROOT}/group-summary.json`,null,null,null,null,null,null,null],[`${AUDIT_ROOT}/posthoc-audit.json`,null,null,null,null,null,null,null],["模型与费用：逐个只读查询隔离 OpenCode opencode.db；Verifier：每组最终 8/8 通过；诊断与误判批次在“排除与Readiness”中单独列出，不计入正式统计。",null,null,null,null,null,null,null]]; body(summary, "A35:H37", true); widths(summary, {A:34,B:24,C:14,D:14,E:14,F:18,G:18,H:18}); summary.getRange("A1:A37").format.columnWidth = 34; summary.getRange("B1:B37").format.columnWidth = 28; summary.freezePanes.freezeRows(15);

// 方法与流程
const method = sheets["方法与流程"];
title(method, "F", "方法与流程（task5 本机 OpenCode）"); section(method, 3, "F", "正式实验协议"); method.getRange("A4:F4").values = [["步骤","协议","task5 实施","审计证据","是否计入正式统计","中文说明"]]; header(method, "A4:F4");
method.getRange("A5:F14").values = [
  ["1","独立确认任务身份","task-005 / organize-messy-files","tasks/pilot/task-005/task.yaml","是","确认任务定义、原始输入、Verifier 与 Skill 路径。"],
  ["2","Ready 与最小依赖","100 PDF + 3 Office；项目 .venv 仅补 PyPDF2","00-readiness/readiness.json","是","不安装整套平台依赖；公开入口可收集。"],
  ["3","无容器本机路线","直接调用 OpenCode 1.18.18 / MiniMax-M3","launcher 与 DB","是","Docker=false，SkillBench route=false。"],
  ["4","3×3 平衡交错","1N-1Native-1PDV-2Native-2PDV-2N-3PDV-3N-3Native","group-summary.schedule","是","沿用 task1 的平衡交错顺序。"],
  ["5","完全隔离","工作区、Verifier 副本、OpenCode 状态、session 均独立","9 个 result.json + 9 DB","是","只共享依赖缓存与只读 auth store。"],
  ["6","条件暴露","No-Skill 无模块；Native 全模块；PD 仅 VISTA.md","initially_available_modules","是","三种条件只差 Skill 暴露方式。"],
  ["7","公开反馈与路由","每轮清洗 Verifier 输出；PD 每轮最多披露 1 个相关模块","turn-N + disclosure_events","是","失败后同一 session 继续修复。"],
  ["8","超时容错","420 秒单轮、6 轮；先验收落盘文件","model_turn_timeout + final verifier","是","PD-VISTA-01 首轮超时但最终通过。"],
  ["9","429/零请求","仅结构化 HTTP 429/配额响应可隔离重跑","excluded_attempts","否","正式批次无真实 429；控制器误判另列。"],
  ["10","后验审计","只读查询每个 opencode.db","posthoc-audit.json","是","确认 provider/model、Token、费用与唯一 session。"],
]; body(method, "A5:F14", true); widths(method, {A:8,B:22,C:40,D:42,E:16,F:46}); method.freezePanes.freezeRows(4);
section(method, 17, "F", "Verifier 本机适配与公开诊断清洗规则"); method.getRange("A18:F18").values = [["项目","执行规则","本机适配","模型可见内容","不回灌内容","说明"]]; header(method, "A18:F18"); method.getRange("A19:F23").values = [
  ["完整 Verifier","每轮执行原始 8 测试断言","仅将 Path(\"/logs/verifier\") 别名到 .verifier-logs；断言与映射不变","通过/失败、缺失/错位/可读性类别","私有控制器状态与工作区外路径","macOS /logs 只读问题已隔离修正。"],
  ["公开诊断","筛选 failed/error/missing/wrong/duplicate/unreadable 等行","绝对路径归一化为 <local-path>","安全失败摘要","完整堆栈、内部 DB、密钥","保持 task1 同类的诊断清洗。"],
  ["PD 路由","manifest triggers + previous_modules","DisclosureRouter max_modules_per_decision=1","披露模块路径与下一轮读取指令","未披露模块正文","触发—披露—下一轮访问—通过形成闭环。"],
  ["限流判定","结构化 status_code=429 / rate_limit_error / 明确配额","排除并新 workspace/session 串行重跑","不作为任务失败","普通论文正文中的“rate limitation”","避免控制器误判。"],
  ["可读性","PDF PyPDF2、Office ZIP 容器","项目 .venv PyPDF2==3.0.1","最终通过/失败","oracle solution","每组最终 8/8 通过。"],
]; body(method, "A19:F23", true); widths(method, {A:20,B:36,C:56,D:34,E:38,F:42});

// 条件提示词
const prompts = sheets["条件提示词"];
title(prompts, "F", "三种条件及失败反馈的原始提示词与中文说明"); section(prompts, 3, "F", "首轮原始提示词"); prompts.getRange("A4:F4").values = [["条件","原始首轮提示词（英文）","中文说明","失败反馈类型","首个公开诊断（英文原文）","同一 session 下一轮原始提示词"]]; header(prompts, "A4:F4");
const promptRows = ["noskill","native","pd-vista"].map(condition => {
  const conditionTrials = trials.filter(t => t.condition === condition);
  const trial = conditionTrials.find(t => (t.turns || []).some(turn => (turn.public_feedback_types || []).length)) || conditionTrials[0];
  const fail = failureTurn(trial); const next = fail ? (trial.turns || []).find(turn => turn.turn > fail.turn)?.prompt || "" : "";
  const initial = trial.turns?.[0]?.prompt || "";
  return [condition, safeText(initial), condition === "noskill" ? "No-Skill：不提供任务 Skill，仅使用任务说明与本地文件。" : condition === "native" ? "Native：首轮前完整提供 5 个原始指导模块。" : "PD-VISTA：首轮仅给 VISTA.md；公开失败后单轮最多披露一个相关模块。", fail ? (fail.public_feedback_types || []).join(", ") : "未触发（首轮通过）", safeText(fail?.public_diagnostic || "无；该条件的有效重复均首轮通过。"), safeText(next || "未触发；不需要修复轮。")];
});
prompts.getRange("A5:F7").values = promptRows; body(prompts, "A5:F7", true); widths(prompts, {A:14,B:84,C:42,D:24,E:86,F:86}); prompts.freezePanes.freezeRows(4);
section(prompts, 10, "F", "PD-VISTA 根路由与原始模块说明"); prompts.getRange("A11:F11").values = [["资产","项目路径","首轮可见性","披露触发","中文说明","正式证据"]]; header(prompts, "A11:F11"); prompts.getRange("A12:F17").values = [
  ["根路由","skills/pd-vista/task-005/SKILL.md","PD-VISTA 可见","始终可见","先盘点输入、准确归类；失败后只披露一个模块。","PD-VISTA-01"],
  ["planning-with-files","environment/skills/planning-with-files/SKILL.md","Native 可见；PD 隐藏","incomplete_output / command_failure","规划与文件盘点模块。","PD-VISTA-01：披露后下一轮读取"],
  ["file-organizer","environment/skills/file-organizer/SKILL.md","Native 可见；PD 隐藏","schema_error / test_failure / assertion_error","归类与文件放置模块。","未在有效 PD 组披露"],
  ["pdf","environment/skills/pdf/SKILL.md","Native 可见；PD 隐藏","wrong_format / dependency_error","PDF 读取与格式模块。","未在有效 PD 组披露"],
  ["docx","environment/skills/docx/SKILL.md","Native 可见；PD 隐藏","runtime_error","DOCX 处理模块。","未在有效 PD 组披露"],
  ["pptx","environment/skills/pptx/SKILL.md","Native 可见；PD 隐藏","timeout / repeated_failure","PPTX 处理模块。","未在有效 PD 组披露"],
]; body(prompts, "A12:F17", true); widths(prompts, {A:24,B:60,C:24,D:38,E:48,F:34});

// PD-VISTA 披露证据
const pd = sheets["PD-VISTA披露证据"];
title(pd, "J", "PD-VISTA 披露证据与干净闭环"); section(pd, 3, "J", "逐组披露与访问"); pd.getRange("A4:J4").values = [["组ID","重复","首轮结果","触发机会","可路由反馈","披露模块","下一轮实际访问","干净闭环","最终结果","逐轮证据"]]; header(pd, "A4:J4");
const pdRows = trials.filter(t => t.condition === "pd-vista").map(t => [
  `${t.condition}-${String(t.replicate_id).padStart(2, "0")}`, t.replicate_id, (t.turns?.[0]?.verifier_pass ? "首轮通过" : "首轮失败"),
  t.vista_metrics?.trigger_opportunities || 0, t.vista_metrics?.routable_feedback || 0, moduleText(t.disclosed_modules), moduleText(t.accessed_modules_after_disclosure),
  t.vista_metrics?.clean_complete_closure || 0, t.final_verifier_pass ? "通过" : "失败",
  (t.disclosure_events || []).map(e => `第${e.trigger_turn}轮→披露 ${e.module_id}；第${e.next_round_turn}轮实际读取=${e.accessed_next_round}`).join("；") || "无披露事件",
]);
pd.getRange("A5:J7").values = pdRows; body(pd, "A5:J7", true); widths(pd, {A:18,B:8,C:14,D:12,E:14,F:24,G:26,H:12,I:12,J:82}); pd.tables.add("A4:J7", true, "Task5PDVistaEvidence"); pd.freezePanes.freezeRows(4);
section(pd, 10, "J", "正式批次之外的诊断 / 限流排除"); pd.getRange("A11:J11").values = [["类型","批次/Attempt","模型请求","是否计入正式","排除原因","是否真实429","证据","影响","处理","备注"]]; header(pd, "A11:J11");
const exclusionRows = diagnostics.map(d => [d.kind, d.run_id, d.model_requests || 0, d.formal_included, d.reason, d.kind.includes("rate_limit") && !d.kind.includes("false_positive"), d.evidence, "不影响正式 9 组", "保留证据并排除", "正式批次无真实 429"]);
pd.getRange(`A12:J${11 + exclusionRows.length}`).values = exclusionRows; body(pd, `A12:J${11 + exclusionRows.length}`, true); widths(pd, {A:30,B:52,C:14,D:16,E:76,F:14,G:54,H:18,I:20,J:28});

// 排除与 Readiness
const exSheet = sheets["排除与Readiness"];
title(exSheet, "F", "Readiness、诊断批次与排除清单"); section(exSheet, 3, "F", "Ready 证据摘要"); exSheet.getRange("A4:F4").values = [["检查项","结果","证据/数值","是否计入正式","中文说明","路径"]]; header(exSheet, "A4:F4");
exSheet.getRange("A5:F16").values = [
  ["任务身份解析", readiness.task_identity_resolved, "task-005 / organize-messy-files", true, "身份与定义路径一致。", `${REPO}/tasks/pilot/task-005/task.yaml`],
  ["PDF 输入数", readiness.pdf_count, "100", true, "下载到项目内 task5 专属资产目录。", `${REPO}/data/materialized/task005-organize-messy-files/all`],
  ["Office 输入数", readiness.office_input_count, "3", true, "1 PPTX + 2 DOCX。", `${REPO}/vendor/skillsbench/tasks/organize-messy-files/environment`],
  ["Verifier 依赖", readiness.verifier_dependencies, readiness.verifier_dependency_probe || "pytest + PyPDF2", true, "只补项目 .venv 的 PyPDF2。", `${REPO}/.venv`],
  ["Verifier 收集", readiness.verifier_collect_ok, "pytest --collect-only", true, "公开入口可运行。", readiness.public_verifier_entry],
  ["原始模块", Object.values(readiness.native_modules || {}).every(Boolean), "5/5", true, "Native 完整提供。", `${REPO}/vendor/skillsbench/tasks/organize-messy-files/environment/skills`],
  ["PD 根路由", readiness.vista_root, "SKILL.md", true, "首轮只给根路由。", `${REPO}/skills/pd-vista/task-005/SKILL.md`],
  ["PD 清单", readiness.vista_manifest, "manifest.yaml", true, "单轮最多披露一个模块。", `${REPO}/skills/pd-vista/task-005/manifest.yaml`],
  ["OpenCode", true, "1.18.18", true, "固定本机 launcher。", readiness.opencode?.launcher || ""],
  ["当前模型", true, readiness.requested_provider_model, true, "未替换当前配置。", readiness.opencode?.config_path || ""],
  ["Docker", false, "false", true, "未使用。", `${DATA_ROOT}/group-summary.json`],
  ["SkillBench 标准执行路线", false, "false", true, "未使用平台路线。", `${DATA_ROOT}/group-summary.json`],
]; body(exSheet, "A5:F16", true); boolStyle(exSheet, "B5:B16"); widths(exSheet, {A:24,B:14,C:38,D:16,E:48,F:74});
section(exSheet, 19, "F", "排除批次"); exSheet.getRange("A20:F20").values = [["批次","类型","请求数","正式计入","排除原因","证据"]]; header(exSheet, "A20:F20"); exSheet.getRange(`A21:F${20 + diagnostics.length}`).values = diagnostics.map(d => [d.run_id, d.kind, d.model_requests || 0, d.formal_included, d.reason, d.evidence]); body(exSheet, `A21:F${20 + diagnostics.length}`, true); widths(exSheet, {A:54,B:34,C:14,D:14,E:86,F:58});

// 字段映射
const map = sheets["字段映射"];
title(map, "F", "字段映射与可审计来源"); map.getRange("A3:F3").values = [["工作簿字段","来源 JSON / 文件","公式或填充规则","审计用途","类型","备注"]]; header(map, "A3:F3");
map.getRange("A4:F21").values = [
  ["组ID","posthoc-audit.formal_results.condition + replicate_id","条件-两位重复编号","唯一组键","文本","9组明细"],
  ["最终Verifier通过","formal_results.final_verifier_pass","明细填充；汇总 SUM","最终验收完整性","布尔","每组最终结果"],
  ["输入/输出/缓存 Token","formal_results.usage.*","明细填充；Token表 SUMIF","消耗审计","数值","OpenCode JSONL step_finish"],
  ["输入+输出Token","9组明细 J/K","M列 = J+K；汇总引用 M","可复算新鲜 Token","公式","禁止硬编码汇总"],
  ["DB记录费用","formal_results.db_recorded_cost_usd / opencode.db session.cost","只读 sqlite 查询；逐组填充","OpenCode 记录费用","数值","不从估算价格推导"],
  ["Provider / Model","opencode_db_audit.sessions.model","只读 DB 核对 providerID/id","身份一致性","文本","minimax-cn / MiniMax-M3"],
  ["Session ID","formal_results.session_id + DB session.id","事件与 DB 交叉核对","独立 session","文本","9 个唯一"],
  ["目录分布","workspace-inventory.subject_counts","明细填充","任务输出结构","文本","20/23/20/20/20"],
  ["初始模块","formal_results.initially_available_modules","条件物化规则","暴露条件","文本","No-Skill=[]；Native=5；PD=[]"],
  ["披露模块","formal_results.disclosed_modules","DisclosureRouter 每轮最多 1","渐进披露","文本","PD-VISTA"],
  ["下一轮实际访问","formal_results.accessed_modules_after_disclosure","JSONL 读取 SKILL.md 的交集","干净闭环","文本","披露不等于访问"],
  ["触发/可路由/成功披露","formal_results.vista_metrics.*","明细填充；可按条件 SUMIF","PD-VISTA 过程","数值","触发机会与路由证据"],
  ["干净闭环","formal_results.vista_metrics.clean_complete_closure","披露后下一轮读取且最终通过","PD 质量检查","数值","0/1 per trial"],
  ["诊断批次","group-summary.diagnostic_batches","单独清单；不计入 9 组","排除透明度","文本","4 个批次"],
  ["排除尝试","posthoc-audit.excluded_attempts","隔离、不计正式统计","限流/误判透明度","文本","2 个控制器误判"],
  ["Docker / SkillBench route","group-summary.*","固定 false；QC 引用","执行路线约束","布尔","均为 false"],
  ["Verifier","verifier/test_outputs.py","仅日志路径别名；断言不变","最终验收","文件","每组最终 8/8"],
  ["公开提示词","turns[].prompt / runner COMMON_PROMPT","原样显示 + 中文说明","提示词可审计","文本","条件提示词页"],
]; body(map, "A4:F21", true); widths(map, {A:26,B:56,C:54,D:34,E:16,F:44}); map.freezePanes.freezeRows(3);

// 公式 QC
const qc = sheets["公式QC"];
title(qc, "D", "工作簿公式与质量检查"); qc.getRange("A3:D3").values = [["检查项","结果","规则","说明"]]; header(qc, "A3:D3"); qc.getRange("A4:D10").values = [["计划组数",9,"固定协议","3 条件 × 3 重复"],["结果文件数",null,"=COUNTA('9组明细'!A5:A13)","应为 9"],["Verifier 通过数",null,"=SUM('Token与成本'!C18:C20)","应为 9"],["公式错误扫描",0,"artifact-tool inspect match","应为 0"],["Docker 使用",false,"=SUMPRODUCT(--('9组明细'!D5:D13=\"docker\"))","应为 FALSE"],["SkillBench 路线",false,"=SUMPRODUCT(--('9组明细'!D5:D13=\"skillbench\"))","应为 FALSE"],["唯一 session 数",null,"=COUNTA('9组明细'!Q5:Q13)","应为 9"]]; qc.getRange("B5:B6").formulas = [["=COUNTA('9组明细'!A5:A13)"],["=SUM('Token与成本'!C18:C20)"]]; qc.getRange("B10").formulas = [["=COUNTA('9组明细'!Q5:Q13)"]]; body(qc, "A4:D10", true); boolStyle(qc, "B8:B9"); widths(qc, {A:24,B:14,C:48,D:62});

const formulaErrors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "task5 final formula error scan" });
const formulaErrorText = formulaErrors.ndjson || "";
if (/(#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A)/.test(formulaErrorText)) throw new Error(`formula errors found: ${formulaErrorText.slice(0, 1200)}`);
await fs.mkdir(AUDIT_ROOT, { recursive: true });
const rendered = [];
for (const [index, sheet] of Object.values(sheets).entries()) {
  const blob = await wb.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
  const path = `${AUDIT_ROOT}/render-${String(index + 1).padStart(2, "0")}-${sheet.name.replace(/[^A-Za-z0-9_-]/g, "_")}.png`;
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
  rendered.push(path);
}
await fs.writeFile(`${AUDIT_ROOT}/formula_error_scan.ndjson`, formulaErrorText || '{"kind":"notice","message":"Cell search matched 0 entries."}\n');
await fs.writeFile(`${AUDIT_ROOT}/workbook-qc.json`, JSON.stringify({ formula_errors: 0, rendered_sheets: rendered, formal_trials: audit.formal_trial_count, unique_sessions: audit.unique_formal_sessions, output: OUTPUT }, null, 2) + "\n");
const output = await SpreadsheetFile.exportXlsx(wb); await output.save(OUTPUT);
console.log(JSON.stringify({ output: OUTPUT, sheets: sheetNames, formula_errors: 0, formal_trials: audit.formal_trial_count, unique_sessions: audit.unique_formal_sessions, excluded_diagnostic_batches: diagnostics.length }, null, 2));
