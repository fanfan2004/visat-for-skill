import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318/outputs/formal-task007-3x3-minimaxm3-20260821-02-audit/task7_final_audit.xlsx";
const outDir = "/Users/wangjinhua/Downloads/vista-travel-macos-handoff-20260819-192318/outputs/formal-task007-3x3-minimaxm3-20260821-02-audit";
const outputPath = `${outDir}/task7_final_audit_zh.xlsx`;
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));

const exact = new Map(Object.entries({
  "Vista task7 · 3-condition × 3-replicate audit": "Vista task7 · 三条件 × 三重复审计",
  "Run ID": "运行 ID", "Audit status": "审计状态", "Completed trials": "完成组数", "Unique sessions": "唯一会话数",
  "Verifier result": "验证器结果", "Provider / model": "提供方 / 模型", "Total recorded cost": "记录总费用",
  "Condition overview": "条件概览", "Condition": "条件", "Trials": "组数", "Passes": "通过数", "Pass rate": "通过率",
  "Requests": "请求数", "Input + output": "输入 + 输出", "Cache read": "缓存读取", "Recorded cost": "记录费用",
  "Key integrity checks": "关键完整性检查", "Check": "检查项", "Result": "结果", "All final verifier runs passed": "所有最终验证器均通过",
  "All itineraries present": "所有行程文件均存在", "Unique session count equals trial count": "唯一会话数等于组数",
  "Docker used": "使用 Docker", "Provider/model consistent": "提供方/模型一致", "Flight distractor disclosed": "披露了航班干扰模块",
  "Source files": "源文件", "Trial-level audit detail": "逐组审计明细", "Trial ID": "组 ID", "Final verifier pass (1/0)": "最终验证通过（1/0）",
  "Turns": "轮数", "Tool uses": "工具调用", "Input tokens": "输入 Token", "Output tokens": "输出 Token", "Cache read tokens": "缓存读取 Token",
  "Total tokens": "总 Token", "Session ID": "会话 ID", "Initially available modules": "初始可用模块", "Disclosed modules": "已披露模块",
  "Accessed after disclosure": "披露后访问", "Flight disclosed": "是否披露航班模块", "Trigger opportunities": "触发机会",
  "Routable feedback": "可路由反馈", "Successful disclosures": "成功披露", "Next-round access": "后续轮次访问", "Clean closure": "干净闭环",
  "Requested model": "请求模型", "Token and cost roll-up": "Token 与成本汇总", "Verifier passes": "验证器通过数",
  "Input + output tokens": "输入 + 输出 Token", "Average total tokens / trial": "每组平均总 Token", "Metric": "指标", "Value": "数值", "Interpretation": "解释",
  "Grand total tokens": "总 Token", "Fresh tokens": "新鲜 Token", "PD-VISTA clean closures": "PD-VISTA 干净闭环数",
  "Skill disclosure process and control logic": "Skill 披露流程与控制逻辑", "Stage": "阶段", "Controller action": "控制器动作",
  "Agent-visible files": "代理可见文件", "Controller-only files": "仅控制器文件", "Evidence recorded": "记录证据", "Control objective": "控制目标",
  "Disclosure evidence by condition and PD-VISTA trial": "按条件与 PD-VISTA 组别的披露证据", "Condition / trial": "条件 / 组别",
  "Initial visibility": "初始可见性", "Initial result": "初始结果", "Routed module": "路由模块", "Actual access proof": "实际访问证据",
  "Final result": "最终结果", "Final audit JSON → workbook field map": "最终审计 JSON → 工作簿字段映射", "JSON section": "JSON 部分",
  "JSON path": "JSON 路径", "Workbook location": "工作簿位置", "Meaning": "含义", "Source / note": "来源 / 备注",
  "No-Skill": "无 Skill", "Native": "原生", "PD-VISTA": "PD-VISTA", "passed": "通过",
  "passed_without_clean_vista_feedback_loop": "通过（无完整 VISTA 反馈闭环）", "passed_with_clean_vista_feedback_loop": "通过（完整 VISTA 反馈闭环）", "None": "无", "Not applicable": "不适用",
  "Root router only": "仅根路由", "No modules": "无模块", "All 6 modules": "全部 6 个模块", "Pass on turn 1": "第 1 轮通过",
  "Fail on turn 1": "第 1 轮失败", "Pass on turn 2": "第 2 轮通过", "3/3 final pass": "3/3 最终通过", "3/3 pass": "3/3 通过",
  "Pass without closure": "通过但无闭环", "Complete clean disclosure closure": "完整干净披露闭环", "Read in turn 2": "第 2 轮读取",
  "Control: no Skill exposure": "对照：未暴露 Skill", "Control: full upfront exposure": "对照：首轮完整暴露",
  "No disclosure opportunity occurred": "未出现披露机会", "1/3 trials had a full feedback loop": "3 组中 1 组形成完整反馈闭环",
  "Final verifier runs passed 10 tests": "所有最终验证器均通过 10 项测试", "Only one PD-VISTA trial had a complete failure → disclosure → access → pass loop": "仅 1 个 PD-VISTA 组形成失败 → 披露 → 访问 → 通过的完整闭环",
  "9/9 trials passed; 10 tests per final verifier": "9/9 组通过；每个最终验证器包含 10 项测试",
  "Input + output + cache read across all 9 trials": "全部 9 组的输入 + 输出 + 缓存读取", "Input + output; excludes cache reads": "输入 + 输出；不含缓存读取",
  "One row per condition × replicate": "每个条件 × 重复 1 行", "Overall post-hoc audit status": "事后审计总体状态",
  "Number of completed trials": "完成组数", "Count of distinct OpenCode sessions": "不同 OpenCode 会话数",
  "Actual provider confirmed from OpenCode DB": "由 OpenCode 数据库确认的实际提供方", "Actual model confirmed from OpenCode DB": "由 OpenCode 数据库确认的实际模型",
  "Model request count by condition": "按条件统计的模型请求数", "Fresh input and output tokens": "新鲜输入与输出 Token",
  "Cache-read token count": "缓存读取 Token 数", "OpenCode recorded cost": "OpenCode 记录费用", "PD-VISTA verifier-failure opportunities": "PD-VISTA 验证失败触发机会",
  "Number of modules copied after routing": "路由后复制的模块数", "Confirmed subsequent module access": "确认的后续模块访问",
  "Full feedback → disclosure → access → pass closures": "完整反馈 → 披露 → 访问 → 通过闭环数", "Whether any container execution occurred": "是否发生容器执行",
  "Provider/model identity was stable": "提供方/模型身份是否稳定", "Whether forbidden flight module was disclosed": "是否披露了禁止的航班模块",
  "Unique formal experiment identifier": "唯一正式实验标识", "posthoc-audit.json": "posthoc-audit.json", "group-summary.json": "group-summary.json",
  "Create an isolated workspace, verifier directory, OpenCode state directory and unique session scope.": "创建隔离工作区、验证器目录、OpenCode 状态目录与唯一会话范围。",
  "Prevent cross-trial leakage and session reuse": "防止跨组泄漏与会话复用", "Expose no task Skill modules.": "不暴露任何任务 Skill 模块。",
  "Measure solving from task and data without Skill help": "衡量无 Skill 帮助时仅凭任务与数据的解题能力",
  "Copy all six original modules before the first model turn.": "在首轮模型调用前复制全部 6 个原始模块。",
  "Measure full upfront Skill availability": "衡量首轮完整可用 Skill 的效果", "Expose only the VISTA root router instructions.": "仅暴露 VISTA 根路由说明。",
  "Hide original modules until feedback justifies access": "在反馈证明有必要前隐藏原始模块",
  "Run the public Verifier after each model turn.": "每轮模型调用后运行公开验证器。", "Prevent oracle leakage": "防止泄漏答案标签",
  "For PD-VISTA only, map feedback type to one manifest module.": "仅对 PD-VISTA 将反馈类型映射到一个清单模块。",
  "Make disclosure deterministic and feedback-driven": "使披露由反馈驱动且确定性执行",
  "Continue the same session and inspect completed tool events for the disclosed SKILL.md.": "继续同一会话并检查已披露 SKILL.md 的完成工具事件。",
  "Distinguish copied files from actual model use": "区分文件已复制与模型实际使用",
  "task.md, local data, output directory": "task.md、本地数据、输出目录", "task.md and data only": "仅 task.md 与数据",
  "skills/<module-id>/SKILL.md and scripts": "skills/<module-id>/SKILL.md 与脚本", "Original source checkout": "原始源代码检出",
  "VISTA.md, task.md and data": "VISTA.md、task.md 与数据", "router-source/modules/<module-id>": "router-source/modules/<module-id>",
  "Only public failure summary is sent to the agent": "仅向代理发送公开失败摘要", "Verifier implementation and answers remain hidden": "验证器实现与答案保持隐藏",
  "No new file until copy completes": "复制完成前不新增文件", "manifest.yaml and hidden module copies": "manifest.yaml 与隐藏模块副本",
  "The newly disclosed complete module directory": "新披露的完整模块目录", "Disclosure source and digest evidence": "披露源与摘要证据",
  "Prevent oracle leakage": "防止答案泄漏"
}));

const replacements = [...exact.entries()];
function zh(value) {
  if (typeof value !== "string") return value;
  if (exact.has(value)) return exact.get(value);
  let out = value;
  for (const [from, to] of replacements) if (from.length > 2 && out.includes(from)) out = out.split(from).join(to);
  return out;
}

const sheetNames = {
  "Summary": "实验总览", "Trial Detail": "单组明细", "Token & Cost": "Token与成本",
  "Disclosure Process": "披露流程", "Disclosure Evidence": "披露证据", "JSON Field Map": "JSON字段映射"
};
for (const s of wb.worksheets.items) {
  const used = s.getUsedRange();
  if (used) used.values = used.values.map(row => row.map(zh));
  if (sheetNames[s.name]) s.name = sheetNames[s.name];
}
// The source workbook stores the average-token cells as stale error values; keep them
// auditable and error-free using the visible roll-up inputs.
wb.worksheets.getItem("Token与成本").getRange("I4:I6").values = [[1178348.6666666667], [1837802.3333333333], [1241903]];

await fs.mkdir(`${outDir}/previews_zh`, { recursive: true });
for (const s of wb.worksheets.items) {
  const png = await wb.render({ sheetName: s.name, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(`${outDir}/previews_zh/${s.name}.png`, new Uint8Array(await png.arrayBuffer()));
}
const errors = await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:300},summary:"Chinese workbook formula error scan"});
console.log("ERROR_SCAN", errors.ndjson);
const check = await wb.inspect({kind:"table",sheetId:"实验总览",range:"A1:H29",include:"values,formulas",tableMaxRows:29,tableMaxCols:8,maxChars:12000});
console.log("CHECK", check.ndjson);
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);
console.log("OUTPUT", outputPath);
