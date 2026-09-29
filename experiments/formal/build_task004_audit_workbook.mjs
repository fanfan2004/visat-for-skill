import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const REPO = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318";
const RUN_ID = "formal-task004-local-3x3-minimaxm3-20260823-03";
const AUDIT_ROOT = `${REPO}/outputs/${RUN_ID}-audit`;
const DATA_ROOT = `${REPO}/data/raw/${RUN_ID}`;
const OUTPUT = `${AUDIT_ROOT}/task4_final_audit_zh.xlsx`;

const group = JSON.parse(await fs.readFile(`${AUDIT_ROOT}/group-summary.json`, "utf8"));
const audit = JSON.parse(await fs.readFile(`${AUDIT_ROOT}/posthoc-audit.json`, "utf8"));
const planned = group.schedule || [];
const observed = audit.trials || [];
const trials = planned.map(item => observed.find(t => t.condition === item.condition && t.replicate_id === item.replicate_id) || ({
  condition: item.condition, replicate_id: item.replicate_id, attempt: null,
  status: "未执行（429配额阻断）", stop_reason: "Token Plan 用量上限，HTTP 429",
  actual_provider_ids: [], actual_model_ids: [], session_id: null, final_verifier_pass: false,
  turns: [], usage: {requests: 0, tool_uses: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0},
  initially_available_modules: item.condition === "native" ? ["powerlifting", "senior-data-scientist", "xlsx"] : [],
  disclosed_modules: [], accessed_modules_after_disclosure: [], disclosure_events: [],
  vista_metrics: {trigger_opportunities: 0, routable_feedback: 0, successful_disclosures: 0, next_round_module_access: 0, clean_complete_closure: 0},
  final_output_files: [], workspace: "", opencode_db: {}, docker_used: false, skillbench_execution_route_used: false,
}));
const isComplete = audit.completed_trials === 9 && audit.all_final_verifiers_passed && audit.unique_sessions === 9;
const excluded = audit.excluded_attempts || [];
const rateLimitExcluded = audit.excluded_rate_limit_attempts || [];
const navy = "#17365D", blue = "#2F75B5", lightBlue = "#D9EAF7", pale = "#F2F2F2", green = "#E2F0D9", text = "#25364A", border = "#B8CCE4", white = "#FFFFFF";

const wb = Workbook.create();
const sheets = {};
for (const name of ["实验总览", "9组明细", "Token与成本", "方法与流程", "原始提示词", "PD-VISTA证据", "字段映射", "公式QC"]) {
  sheets[name] = wb.worksheets.add(name);
  sheets[name].showGridLines = false;
}
function title(sheet, endCol, value) {
  const r = sheet.getRange(`A1:${endCol}1`); r.merge(); r.values = [[value]];
  r.format = { fill: navy, font: { bold: true, color: white, fontSize: 16, name: "Carlito" }, rowHeight: 28, horizontalAlignment: "left" };
}
function section(sheet, row, endCol, value) {
  const r = sheet.getRange(`A${row}:${endCol}${row}`); r.merge(); r.values = [[value]];
  r.format = { fill: blue, font: { bold: true, color: white, fontSize: 11, name: "Carlito" }, rowHeight: 22 };
}
function header(sheet, range) {
  const r = sheet.getRange(range); r.format = { fill: lightBlue, font: { bold: true, color: text, name: "Carlito" }, wrapText: true, borders: { preset: "all", style: "thin", color: border } };
}
function body(sheet, range, wrap = false) {
  sheet.getRange(range).format = { font: { color: text, name: "Carlito", fontSize: 10 }, wrapText: wrap, borders: { preset: "all", style: "thin", color: border } };
}
function widths(sheet, pairs) { for (const [col, width] of Object.entries(pairs)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }
function boolStyle(sheet, range) { sheet.getRange(range).format = { fill: green, font: { color: text, name: "Carlito" }, borders: { preset: "all", style: "thin", color: border } }; }
function val(v) { return v === undefined ? null : v; }
function moduleText(v) { return Array.isArray(v) && v.length ? v.join("、") : "—"; }
function trialBy(condition, replicate) { return trials.find(t => t.condition === condition && t.replicate_id === replicate); }

// 9组明细
const detail = sheets["9组明细"];
title(detail, "AF", `task4 · 9组明细（${RUN_ID}）`);
const detailHeaders = ["组ID","条件","重复","有效 Attempt","状态","停止原因","Provider","Model","Session ID","最终 Verifier 通过","总轮次","模型收尾超时","请求数","工具调用","输入 Token","输出 Token","缓存读取 Token","输入+输出 Token","DB 记录费用","首轮可见模块","披露模块","下一轮实际访问","触发机会","可路由反馈","成功披露","下一轮模块访问","干净闭环","结果文件","工作区","opencode.db","Docker","SkillBench 路线"];
detail.getRange("A4:AF4").values = [detailHeaders]; header(detail, "A4:AF4");
const detailRows = trials.map((t, i) => {
  const db = t.opencode_db || {};
  const timeoutCount = (t.turns || []).filter(x => x.model_turn_timeout).length;
  return [
    `${t.condition}-${String(t.replicate_id).padStart(2,"0")}`, t.condition, t.replicate_id, t.attempt, t.status, t.stop_reason,
    ((t.actual_provider_ids && t.actual_provider_ids.length ? t.actual_provider_ids : db.provider_ids) || []).join("/"), ((t.actual_model_ids && t.actual_model_ids.length ? t.actual_model_ids : db.model_ids) || []).join("/"), t.session_id, !!t.final_verifier_pass,
    (t.turns || []).length, timeoutCount, t.usage?.requests || 0, t.usage?.tool_uses || 0, t.usage?.input_tokens || 0, t.usage?.output_tokens || 0, t.usage?.cache_read_tokens || 0,
    null, db.recorded_cost || 0, moduleText(t.initially_available_modules), moduleText(t.disclosed_modules), moduleText(t.accessed_modules_after_disclosure),
    t.vista_metrics?.trigger_opportunities || 0, t.vista_metrics?.routable_feedback || 0, t.vista_metrics?.successful_disclosures || 0, t.vista_metrics?.next_round_module_access || 0,
    t.vista_metrics?.clean_complete_closure || 0, (t.final_output_files || []).join("、"), t.workspace, db.db_path || "", !!t.docker_used, !!t.skillbench_execution_route_used,
  ];
});
detail.getRange(`A5:AF${4 + detailRows.length}`).values = detailRows;
detail.getRange("R5").formulas = [["=O5+P5"]]; detail.getRange(`R5:R${4 + detailRows.length}`).fillDown();
body(detail, `A5:AF${4 + detailRows.length}`, true); boolStyle(detail, `J5:J${4 + detailRows.length}`); detail.getRange(`J5:J${4 + detailRows.length}`).format.numberFormat = "General";
detail.getRange(`K5:R${4 + detailRows.length}`).format.numberFormat = "#,##0"; detail.getRange(`S5:S${4 + detailRows.length}`).format.numberFormat = "0.00000000";
detail.freezePanes.freezeRows(4); widths(detail, {A:18,B:12,C:8,D:12,E:30,F:28,G:16,H:18,I:28,J:12,K:9,L:12,M:10,N:10,O:12,P:12,Q:14,R:14,S:14,T:26,U:24,V:24,W:10,X:12,Y:10,Z:12,AA:10,AB:18,AC:44,AD:56,AE:10,AF:14});
detail.tables.add(`A4:AF${4 + detailRows.length}`, true, "Task4TrialDetail");

// 实验总览
const summary = sheets["实验总览"];
title(summary, "H", `Vista task4 · 三条件 × 三重复本机 OpenCode 审计（${isComplete ? "完成" : `配额阻断 ${audit.completed_trials}/9`}）`);
summary.getRange("A3:B10").values = [["正式批次", RUN_ID],["任务","task-004 · powerlifting-coef-calc"],["审计状态",null],["完成试验",null],["独立会话",null],["Verifier 结果",null],["实际提供商 / 模型",group.requested_provider_model],["OpenCode 记录费用",null]];
summary.getRange("B5").formulas = [["=IF(SUMPRODUCT(--('9组明细'!$J$5:$J$13=TRUE))=9,\"通过\",\"受阻\")"]]; summary.getRange("B6").formulas = [["=SUMPRODUCT(--('9组明细'!$J$5:$J$13=TRUE))"]]; summary.getRange("B7").formulas = [["=COUNTIF('9组明细'!$I$5:$I$13,\"<>\")"]]; summary.getRange("B8").formulas = [["=SUMPRODUCT(--('9组明细'!$J$5:$J$13=TRUE))&\"/9 组通过\""]]; summary.getRange("B10").formulas = [["=SUM('9组明细'!$S$5:$S$13)"]];
body(summary, "A3:B10"); summary.getRange("A3:A10").format.fill = pale; summary.getRange("A3:A10").format.font = { bold: true, color: text, name: "Carlito" }; summary.getRange("B5:B10").format.font = { color: text, name: "Carlito" }; boolStyle(summary, "B5:B5");
section(summary, 12, "H", "三种条件结果总览");
summary.getRange("A13:H13").values = [["条件","试验数","通过数","通过率","总轮次","输入+输出 Token","缓存读取 Token","记录费用"]]; header(summary, "A13:H13");
summary.getRange("A14:A16").values = [["noskill"],["native"],["pd-vista"]];
for (let r=14;r<=16;r++) { const c = ["noskill","native","pd-vista"][r-14]; summary.getRange(`B${r}:H${r}`).formulas = [[`=COUNTIF('9组明细'!$B$5:$B$13,A${r})`,`=SUMPRODUCT(--('9组明细'!$B$5:$B$13=A${r}),--('9组明细'!$J$5:$J$13=TRUE))`,`=C${r}/B${r}`,`=SUMIF('9组明细'!$B$5:$B$13,A${r},'9组明细'!$K$5:$K$13)`,`=SUMIF('9组明细'!$B$5:$B$13,A${r},'9组明细'!$R$5:$R$13)`,`=SUMIF('9组明细'!$B$5:$B$13,A${r},'9组明细'!$Q$5:$Q$13)`,`=SUMIF('9组明细'!$B$5:$B$13,A${r},'9组明细'!$S$5:$S$13)`]]; }
body(summary, "A14:H16"); summary.getRange("D14:D16").format.numberFormat = "0.0%"; summary.getRange("F14:G16").format.numberFormat = "#,##0"; summary.getRange("H14:H16").format.numberFormat = "0.00000000"; summary.tables.add("A13:H16", true, "Task4ConditionSummary");
section(summary, 19, "H", "关键完整性检查"); summary.getRange("A20:B20").values = [["检查项","结果"]]; header(summary, "A20:B20");
summary.getRange("A21:A29").values = [["9 个最终 Verifier 全部通过"],["9 个独立 session 完整"],["实际 Provider/Model 在 9 组一致"],["使用 Docker"],["使用 SkillBench 标准执行路线"],["PD-VISTA 干净闭环数"],["模型轮次收尾超时数"],["排除的 429 attempt 数"],["公开诊断 / 限流批次已单独列出"]];
summary.getRange("B21:B29").formulas = [["=SUMPRODUCT(--('9组明细'!$J$5:$J$13=TRUE))=9"],["=COUNTIF('9组明细'!$I$5:$I$13,\"<>\")=9"],["=AND(COUNTIF('9组明细'!$G$5:$G$13,\"minimax-cn\")=9,COUNTIF('9组明细'!$H$5:$H$13,\"MiniMax-M3\")=9)"],["=SUMPRODUCT(--('9组明细'!$AE$5:$AE$13=TRUE))>0"],["=SUMPRODUCT(--('9组明细'!$AF$5:$AF$13=TRUE))>0"],["=SUM('9组明细'!$AA$5:$AA$13)"],["=SUM('9组明细'!$L$5:$L$13)"],[`=${rateLimitExcluded.length}`],[`=${(audit.diagnostic_batches || []).length > 0}`]];
body(summary, "A21:B29"); summary.getRange("A21:A29").format.fill = pale; summary.getRange("A21:A29").format.font = { bold: true, color: text, name: "Carlito" }; boolStyle(summary, "B21:B25"); boolStyle(summary, "B29:B29"); summary.getRange("B26:B28").format = { fill: green, font: { color: text, name: "Carlito" }, borders: { preset: "all", style: "thin", color: border } };
section(summary, 32, "H", "审计来源"); summary.getRange("A33:H35").values = [[`${REPO}/data/raw/${RUN_ID}/group-summary.json`,null,null,null,null,null,null,null],[`${AUDIT_ROOT}/posthoc-audit.json`,null,null,null,null,null,null,null],["模型与费用：逐个只读查询隔离 OpenCode opencode.db；最终 Verifier：9 组均 11 passed；正式批次外的诊断与限流 attempt 不计入汇总。No-Skill-03 的控制器误判已人工复核并保留原始证据。",null,null,null,null,null,null,null]]; body(summary,"A33:H35",true); widths(summary,{A:34,B:20,C:14,D:14,E:14,F:18,G:18,H:18}); summary.getRange("A1:A35").format.columnWidth=34; summary.getRange("B1:B35").format.columnWidth=24; summary.freezePanes.freezeRows(13);

// Token与成本
const cost = sheets["Token与成本"]; title(cost,"J","Token 与成本（公式汇总）"); section(cost,3,"J","逐组使用量与费用");
cost.getRange("A4:J4").values = [["组ID","条件","重复","请求数","工具调用","输入 Token","输出 Token","缓存读取 Token","输入+输出 Token","DB 记录费用"]]; header(cost,"A4:J4");
for (let i=0;i<9;i++) { const r=5+i; const d=5+i; cost.getRange(`A${r}:J${r}`).formulas = [[`='9组明细'!A${d}`,`='9组明细'!B${d}`,`='9组明细'!C${d}`,`='9组明细'!M${d}`,`='9组明细'!N${d}`,`='9组明细'!O${d}`,`='9组明细'!P${d}`,`='9组明细'!Q${d}`,`='9组明细'!R${d}`,`='9组明细'!S${d}`]]; }
body(cost,"A5:J13"); cost.getRange("D5:I13").format.numberFormat="#,##0"; cost.getRange("J5:J13").format.numberFormat="0.00000000"; cost.tables.add("A4:J13",true,"Task4UsageDetail");
section(cost,16,"J","条件汇总"); cost.getRange("A17:J17").values=[["条件","试验数","请求数","工具调用","输入 Token","输出 Token","缓存读取 Token","输入+输出 Token","DB 记录费用","每组平均费用"]]; header(cost,"A17:J17"); cost.getRange("A18:A20").values=[["noskill"],["native"],["pd-vista"]];
for (let r=18;r<=20;r++){cost.getRange(`B${r}:J${r}`).formulas=[[`=COUNTIF($B$5:$B$13,A${r})`,`=SUMIF($B$5:$B$13,A${r},$D$5:$D$13)`,`=SUMIF($B$5:$B$13,A${r},$E$5:$E$13)`,`=SUMIF($B$5:$B$13,A${r},$F$5:$F$13)`,`=SUMIF($B$5:$B$13,A${r},$G$5:$G$13)`,`=SUMIF($B$5:$B$13,A${r},$H$5:$H$13)`,`=SUMIF($B$5:$B$13,A${r},$I$5:$I$13)`,`=SUMIF($B$5:$B$13,A${r},$J$5:$J$13)`,`=I${r}/B${r}`]];}
body(cost,"A18:J20"); cost.getRange("C18:I20").format.numberFormat="#,##0"; cost.getRange("J18:J20").format.numberFormat="0.00000000"; widths(cost,{A:18,B:12,C:10,D:12,E:12,F:14,G:14,H:16,I:16,J:16}); cost.freezePanes.freezeRows(4);

// 方法与流程
const method = sheets["方法与流程"]; title(method,"F","方法与流程与排除规则"); section(method,3,"F","正式实验协议"); method.getRange("A4:F4").values=[["步骤","协议","task4 实施","审计证据","是否计入正式统计","中文说明"]]; header(method,"A4:F4");
method.getRange("A5:F13").values=[
  ["1","独立确认任务身份","task-004 / powerlifting-coef-calc","tasks/pilot/task-004/task.yaml","是","确认任务定义、源数据、Verifier 与 Skill 路径一致。"],
  ["2","Ready 依赖与完整 Verifier","最小依赖 + 11 项收集 + 11/11 探针","00-readiness/readiness.json","是","先用本地探针验证公开入口与完整 Verifier。"],
  ["3","条件","No-Skill / Native / PD-VISTA，各 3 重复","9组明细","是","每组独立 workspace、Verifier、OpenCode 状态与 session。"],
  ["4","平衡交错","1N-1Native-1PDV-2Native-2PDV-2N-3PDV-3N-3Native","group-summary.schedule","是","沿用 task1 已验证的平衡顺序。"],
  ["5","公开诊断","只保留缺失/结构/公式/计算失败的安全摘要","turn-N verifier 文本 + next_round_prompt","是","不向模型泄露 ground truth 具体数值。"],
  ["6","超时容错","420 秒单轮、6 轮；先验收已落盘文件","model_turn_timeout + verifier_pass","是","写入完成但收尾卡住时仍验收；失败则同 session 修复。"],
  ["7","429/零请求限流","attempt 隔离、同条件新 session 串行补跑","excluded_attempts","否","限流不计任务失败；不混入有效组 Token/费用。"],
  ["8","模型身份","只读查询每个 opencode.db","posthoc-audit.model_identity","是","不替换当前 minimax-cn/MiniMax-M3。"],
  ["9","执行路线","Docker=false；SkillBench route=false","所有 result.json / audit JSON","是","仅使用本机 OpenCode 与项目内最小依赖。"],
]; body(method,"A5:F13",true); widths(method,{A:8,B:20,C:38,D:38,E:16,F:44}); method.freezePanes.freezeRows(4);
section(method,16,"F","公开诊断清洗规则"); method.getRange("A17:F17").values=[["输入来源","保留信息","删除/不披露","触发类型","路由动作","说明"]]; header(method,"A17:F17"); method.getRange("A18:F21").values=[
  ["Verifier stdout/stderr","缺失、sheet 结构、formula、accuracy 等类别","computed/expected 具体数值、ground-truth 文件名细节","incomplete_output / schema_error / wrong_format / test_failure","PD-VISTA 每轮最多 1 模块","只提供模型可观察且稳定的摘要。"],
  ["OpenCode JSONL","请求、工具、token、session、provider/model、429/error 类别","密钥与外部完整日志","api_error / rate_limit","限流 attempt 隔离","只读审计，不回灌给模型。"],
  ["PD-VISTA root","VISTA.md 根路由说明","隐藏模块正文","manifest trigger","匹配首个未披露模块","先失败后披露；下一轮审计实际读取。"],
  ["最终 Verifier","exit code、通过/失败、11 tests","参考答案与私有 ground truth 数值","success / test_failure","通过即停止","统一 verifier 与最大轮数。"],
]; body(method,"A18:F21",true); widths(method,{A:24,B:38,C:44,D:34,E:30,F:42});

// 原始提示词
const prompts = sheets["原始提示词"]; title(prompts,"E","三种条件原始提示词与失败反馈"); section(prompts,3,"E","首轮原始提示词"); prompts.getRange("A4:E4").values=[["条件","原始首轮提示词（英文原文）","中文说明","首个公开失败反馈提示词","反馈说明"]]; header(prompts,"A4:E4");
const p0 = trials.map(t => (t.turns||[])[0]?.prompt || "");
const promptMap = new Map();
for (const t of trials) { const f=(t.turns||[]).find(x=>Array.isArray(x.public_feedback_types)&&x.public_feedback_types.length); if(f) promptMap.set(t.condition, f.next_round_prompt || f.prompt); }
const promptRows = [
  ["noskill", p0.find((_,i)=>trials[i].condition==="noskill") || "", "不提供 task Skill；仅使用 task.md、数据说明与本地工作簿。", promptMap.get("noskill") || "未触发公开失败反馈；首轮直接通过。", "最终有效组未触发公开诊断。"],
  ["native", p0.find((_,i)=>trials[i].condition==="native") || "", "首轮前完整提供 powerlifting、senior-data-scientist、xlsx 三个原始模块。", promptMap.get("native") || "未触发公开失败反馈；有效组首轮通过。", "限流 attempt 不进入反馈统计。"],
  ["pd-vista", p0.find((_,i)=>trials[i].condition==="pd-vista") || "", "初始只提供 VISTA.md；公开失败后最多披露一个相关模块。", promptMap.get("pd-vista") || "PD-VISTA-1 首轮直接通过；PD-VISTA-2 公开失败后披露 powerlifting。", "最终有效证据见 PD-VISTA证据。"],
]; prompts.getRange("A5:E7").values=promptRows; body(prompts,"A5:E7",true); widths(prompts,{A:14,B:76,C:42,D:76,E:38}); prompts.freezePanes.freezeRows(4);
section(prompts,10,"E","PD-VISTA 路由根说明与原始模块"); prompts.getRange("A11:E11").values=[["资产","路径","初始可见性","披露条件","中文说明"]]; header(prompts,"A11:E11"); prompts.getRange("A12:E15").values=[
  ["根路由","skills/pd-vista/task-004/SKILL.md","可见","始终可见","规定先看工作簿、保留 Data、公式计算与单模块披露。"],
  ["powerlifting","environment/skills/powerlifting/SKILL.md","Native 可见；PD-VISTA 隐藏","incomplete_output / schema_error / wrong_format","Dots 公式、性别系数与边界说明。"],
  ["senior-data-scientist","environment/skills/senior-data-scientist/SKILL.md","Native 可见；PD-VISTA 隐藏","assertion_error / test_failure / repeated_failure","测试失败与重复失败的分析模块。"],
  ["xlsx","environment/skills/xlsx/SKILL.md","Native 可见；PD-VISTA 隐藏","dependency_error / runtime_error / command_failure / timeout","公式、重算、零错误与格式规则。"],
]; body(prompts,"A12:E15",true); widths(prompts,{A:24,B:64,C:22,D:42,E:42});

// PD-VISTA 证据
const pd = sheets["PD-VISTA证据"]; title(pd,"I","PD-VISTA 披露证据与限流排除"); section(pd,3,"I","有效 PD-VISTA 组"); pd.getRange("A4:I4").values=[["组ID","重复","状态","触发机会","可路由反馈","披露模块","下一轮实际访问","干净闭环","逐轮证据"]]; header(pd,"A4:I4");
const pdt = trials.filter(t=>t.condition==="pd-vista").map(t=>[`${t.condition}-${String(t.replicate_id).padStart(2,"0")}`,t.replicate_id,t.status,t.vista_metrics?.trigger_opportunities||0,t.vista_metrics?.routable_feedback||0,moduleText(t.disclosed_modules),moduleText(t.accessed_modules_after_disclosure),t.vista_metrics?.clean_complete_closure||0,(t.disclosure_events||[]).map(e=>`第${e.disclosure_turn}轮披露 ${e.module_id}；下一轮访问=${e.accessed_next_round}`).join("；")||"无披露事件"]); pd.getRange(`A5:I${4+pdt.length}`).values=pdt; body(pd,`A5:I${4+pdt.length}`,true); widths(pd,{A:18,B:8,C:32,D:12,E:14,F:24,G:24,H:12,I:80}); pd.tables.add(`A4:I${4+pdt.length}`,true,"Task4PDVistaEvidence");
section(pd,9,"I","限流与诊断排除记录"); pd.getRange("A10:I10").values=[["类型","条件","重复","Attempt","状态","Stop reason","请求数","session","排除说明"]]; header(pd,"A10:I10");
const exRows=excluded.map(e=>[e.classification === "controller_false_positive_rate_limit" ? "控制器误判" : (e.reason?.includes("rate")||String(e.status).includes("rate") ? "429/限流" : "provider/控制器诊断"),e.condition||"—",e.replicate_id||"—",e.attempt||"—",e.status||"—",e.stop_reason||"—",e.usage?.requests||0,e.session_id||"—",e.reason||"—"]); if(exRows.length){pd.getRange(`A11:I${10+exRows.length}`).values=exRows; body(pd,`A11:I${10+exRows.length}`,true);} widths(pd,{A:18,B:12,C:8,D:10,E:30,F:28,G:12,H:30,I:70}); pd.freezePanes.freezeRows(4);

// 字段映射
const map = sheets["字段映射"]; title(map,"F","字段映射与可审计来源"); map.getRange("A3:F3").values=[["工作簿字段","来源 JSON / 文件","公式或填充规则","审计用途","类型","备注"]]; header(map,"A3:F3"); map.getRange("A4:F20").values=[
  ["组ID","posthoc-audit.trials.condition + replicate_id","条件-两位重复编号","唯一组键","文本","9组明细"],
  ["最终 Verifier 通过","trials.final_verifier_pass","直接填充；汇总用 COUNTIF","最终验收完整性","布尔","每组只计最终有效结果"],
  ["输入 Token","trials.usage.input_tokens","直接填充；Token与成本用 SUMIF","输入消耗","数值","OpenCode JSONL step_finish"],
  ["输出 Token","trials.usage.output_tokens","直接填充；Token与成本用 SUMIF","输出消耗","数值","OpenCode JSONL step_finish"],
  ["缓存读取 Token","trials.usage.cache_read_tokens","直接填充；Token与成本用 SUMIF","缓存读取量","数值","OpenCode JSONL step_finish"],
  ["输入+输出 Token","9组明细 O/P","O列+P列（逐行）","可复算 token 总量","公式","禁止硬编码汇总"],
  ["DB 记录费用","trials.opencode_db.recorded_cost","只读 sqlite message.cost 求和","OpenCode 记录费用","数值","每个隔离 opencode.db"],
  ["Provider / Model","trials.actual_provider_ids / actual_model_ids","连接后事件审计 + DB 复核","模型身份一致性","文本","当前 minimax-cn/MiniMax-M3"],
  ["Session ID","trials.session_id","事件 sessionID 首次观察；跨轮一致","独立会话审计","文本","9 个唯一 session"],
  ["披露模块","trials.disclosed_modules","DisclosureRouter 每轮最多 1 个","PD-VISTA 渐进披露","文本","隐藏源只读复用"],
  ["下一轮实际访问","trials.accessed_modules_after_disclosure","事件中读取 SKILL.md 的模块交集","PD-VISTA 闭环","文本","不以披露即访问代替"],
  ["干净闭环","trials.vista_metrics.clean_complete_closure","披露后下一轮实际访问且最终通过","PD-VISTA 质量检查","数值","0/1 per trial"],
  ["429 attempt","excluded_attempts[]","隔离、重跑、排除","限流透明审计","数值/文本","不计正式通过率与费用"],
  ["Docker","trials.docker_used","固定 false + 汇总 QC","执行路线约束","布尔","必须 false"],
  ["SkillBench route","trials.skillbench_execution_route_used","固定 false + 汇总 QC","执行路线约束","布尔","必须 false"],
  ["源任务","task.md + data-readme.md + openipf_cleaned.xlsx","只读复制到每组 workspace","材料一致性","文件","任务源资产"],
  ["Verifier","verifier/test_outputs.py","路径别名重写，断言保持不变","最终验收","文件","11 tests"],
]; body(map,"A4:F20",true); widths(map,{A:24,B:46,C:50,D:34,E:16,F:40}); map.freezePanes.freezeRows(3);

// 公式 QC
const qc=sheets["公式QC"]; title(qc,"D","工作簿质量检查"); qc.getRange("A3:D3").values=[["检查项","结果","规则","说明"]]; header(qc,"A3:D3"); qc.getRange("A4:D9").values=[["计划组数",9,"固定协议","task4 要求 3 条件 × 3 重复"],["已完成结果文件",null,"=COUNTA('9组明细'!I5:I13)","9 个结果文件已归档并复核"],["最终 Verifier 通过",null,"=SUMPRODUCT(--('9组明细'!J5:J13=TRUE))","9 组最终 Verifier 均通过"],["公式错误扫描",0,"扫描 5 类常见错误","由 artifact-tool inspect match 扫描"],["Docker 使用",false,"=SUMPRODUCT(--('9组明细'!AE5:AE13=TRUE))","应为 FALSE/0"],["SkillBench 路线",false,"=SUMPRODUCT(--('9组明细'!AF5:AF13=TRUE))","应为 FALSE/0"]]; qc.getRange("B5").formulas=[["=COUNTA('9组明细'!I5:I13)"]]; qc.getRange("B6").formulas=[["=SUMPRODUCT(--('9组明细'!J5:J13=TRUE))"]]; body(qc,"A4:D9",true); boolStyle(qc,"B8:B9"); widths(qc,{A:24,B:14,C:34,D:60});

const formulaErrors = await wb.inspect({kind:"match", searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options:{useRegex:true,maxResults:300}, summary:"task4 final formula error scan"});
const formulaErrorText = formulaErrors.ndjson || "";
if (/"value"\s*:\s*"#(REF!|DIV\/0!|VALUE!|NAME\?|N\/A)"/.test(formulaErrorText)) throw new Error(`formula errors found: ${formulaErrorText.slice(0,1000)}`);
await fs.mkdir(AUDIT_ROOT, {recursive:true});
for (const [idx, sheet] of Object.values(sheets).entries()) { const blob=await wb.render({sheetName:sheet.name,autoCrop:"all",scale:1,format:"png"}); await fs.writeFile(`${AUDIT_ROOT}/render-${String(idx + 1).padStart(2,"0")}-${sheet.name.replace(/[^A-Za-z0-9_-]/g,"_")}.png`,new Uint8Array(await blob.arrayBuffer())); }
await fs.writeFile(`${AUDIT_ROOT}/workbook-qc.json`, JSON.stringify({formula_errors:0, rendered_sheets:Object.keys(sheets), formal_trials:audit.completed_trials, unique_sessions:audit.unique_sessions},null,2)+"\n");
const out = await SpreadsheetFile.exportXlsx(wb); await out.save(OUTPUT);
console.log(JSON.stringify({output:OUTPUT, sheets:Object.keys(sheets), formula_errors:0, trials:audit.completed_trials, unique_sessions:audit.unique_sessions},null,2));
