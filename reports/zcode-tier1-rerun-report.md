# ZCode Tier 1 重跑批次报告（4cond-x5-glm53-20261013）

> **⚠ 2026-10-14 更正：本批次仅首轮结果有效。**
> 复核发现重跑时的 `public_diagnostic` 未掩断言数值：26 个多轮试验（task-011 noskill 5、pd-vista 5；task-012 noskill 5、native 4、pd-self 3、pd-vista 4）的失败诊断**全部**把真值回传给了 agent（如 `E assert 3457 == 2451`、`E assert 'PO-INVALID' == None`），task-011 noskill r1 下一轮直接写入 2451。因此第 3 节的"四条件 5/5""noskill −48% 请求""pd-vista 4/5 干净闭环"及第 4 节依赖多轮的结论**撤回**。
>
> 有效的首轮通过率：
>
> | 首轮通过 | noskill | native | pd-self | pd-vista（首轮无 skill） |
> |---|---|---|---|---|
> | task-011 | 0/5 | 5/5 | 5/5 | 0/5 |
> | task-012 | 0/5 | 1/5 | 2/5 | 1/5 |
>
> task-011 首轮 native/pd-self 5/5 vs noskill 0/5 是目前最干净的 skill 准确率效应，保留。数值掩码已在 `run_sj4_zcode_group.py` 修复（保留字段名、掩值，188 份历史失败日志回放零泄漏）。


任务来源：task-011（sec-financial-report）、task-012（invoice-fraud-detection）因旧批次验证器构建缺陷全部作废，按外部评审指南修复后全条件重跑；task-013 验证器自包含、结果有效，维持 20260930-15 批次不变。

后端：ZCode CLI 0.16.9 headless + glm53-flash（宿主 provider_config 钉扎），本地验证器（/root 路径改写副本），无 Docker。

## 1. 旧批次作废原因（评审核实 + 新发现）

外部评审四条主张全部核实为真：manifest 触发词同质导致固定顺序披露、routable_feedback 虚高、模块放完后空转、干净闭环过松。核查中另发现两个更严重的问题：

1. **验证器基础设施 bug**：`write_local_verifier` 只拷贝 `test_outputs.py`，未拷贝伴随 ground truth（task-011 的 `expected_output.json`、task-012 的 `ground_truth.json`）→ 质量/内容检查每轮 `FileNotFoundError`。**全部 24 个旧试验无效**：19 个失败为验证器伪影；4 个"通过"（task-011 native rep2/pd-vista rep1、task-012 noskill rep2/native rep2）为 agent 用 yolo 权限自建 ground truth 文件自证通过（rollout 取证确认）。
2. **Oracle 泄漏链**：公开诊断泄露 `verifier/expected_output.json` 路径片段 → agent 推断出验证器目录（原为 workspace 兄弟目录）→ 自证通过。

旧批次已改名 `-invalid-verifier-leak` 并附 QUARANTINE.txt（d01b53c 已推送）。其他任务（task-002/008/009/010/013）验证器均自包含，且 rollout 扫描证实 agent 零次触碰验证器路径，结果有效。

## 2. 修复清单（全部落地并校准）

- 验证器：ground truth 伴随文件随 `test_outputs.py` 同拷；验证器目录移至 `~/.verifiers/`（agent 不可达）；诊断脱敏所有 `verifier/ground_truth/expected_output` 路径片段。
- 失败层签名路由：runner 从验证器 traceback 提取任务专属签名（task-011：`13f_value_mismatch`→13f-analyzer、`13f_name_list_mismatch`→fuzzy-name-search、`13f_missing_output`；task-012：`fraud_missed`→pdf、`fraud_false_positive`→xlsx、`fraud_field_mismatch`→fuzzy-match、`missing_output_file`→pdf），manifest 触发词同步改写；**无匹配不披露**，计入新增 `unroutable_feedback`。签名规则用修好的验证器模拟真实失败校准通过。
- 停滞检测：披露开始后签名变化/新披露/模块读取均算进展，连续 2 轮无进展提前停止（`stalled_after_full_disclosure`）；放完后改为点名重读已披露模块。
- 指标修正：`routable_feedback` 只算选出新模块的轮次；新增 `unroutable_feedback`、`disclosure_to_pass_gap`、`access_after_last_disclosure`、`first_turn_pass`；干净闭环收紧为「披露后下一轮有读取 + gap ≤ 1」。
- 复现扩到 5：task-011/012 每条件 5 复现（per-task schedule），task-013 维持 3。

## 3. 重跑结果（task-011 / task-012，各 20 试验）

### task-011（SEC 13F 财报多跳检索）

| 条件 | 通过 | 请求 | 输入+输出 Token | 干净闭环 |
|---|---|---|---|---|
| noskill | 5/5 | 261 | 14,479,888 | — |
| native | 5/5 | 106 | 3,178,166 | — |
| pd-vista | 5/5 | 135 | 4,744,530 | 4/5 |
| pd-self | 5/5 | 111 | 2,926,685 | — |

- **四条件全部 5/5 通过**——真实验证器（ground truth 在场、oracle 不可达）下任务并非不可过。
- noskill 显著最重（261 请求 / 14.48M token）；native/pd-self/pd-vista 都远低于它：**模块对无技能基线帮助显著**（−48% 请求、−79% token）。
- pd-vista 5 个复现：r1/r2/r3/r5 全部「turn 1 失败 → 披露 13f-analyzer → 下一轮读取 → 通过」干净闭环（gap=1、unroutable=0）；r4 为 3 轮通过、gap=2、unroutable=1，严格闭环指标如实记为 `passed_without_clean_vista_feedback_loop`（不虚报）。
- 过程记录：noskill r3/r5 首轮补跑时在 turn 2 遇到间歇性 API 挂起（30 分钟 0 请求记录），隔离后补跑均通过（r3：74 请求 3 轮；r5：55 请求 3 轮）。隔离结果备份为 `result.json.quarantined-attemptN.bak`。

### task-012（发票欺诈检测）

| 条件 | 通过 | 请求 | 输入+输出 Token | 干净闭环 |
|---|---|---|---|---|
| noskill | 5/5 | 81 | 2,279,842 | — |
| native | 5/5 | 76 | 2,783,763 | — |
| pd-vista | 5/5 | 72 | 2,289,078 | 4/5 |
| pd-self | 5/5 | 66 | 2,288,379 | — |

- 四条件全部 5/5 通过，全部在真实验证器下取得。
- pd-vista 请求最少（72 vs noskill 81）：r1-r4 全部「披露 fuzzy-match → 下一轮读取 → gap=1」干净闭环，unroutable 全 0；r5 为 turn 1 直通（agent 直接写出正确报告，无需披露）。
- native 模块读取的输入 token 成本最高（2.78M，全量三模块前置可见）。

## 4. 结论变化（相对旧批次报告）

1. **旧「task-011 noskill 0/3 → pd-vista 1/3 翻盘」结论作废**：旧数据是验证器伪影。真实结果为四条件全通过，VISTA 的价值体现为成本面（相对 noskill −48% 请求）而非能力翻盘。
2. **预测器验证从「1/3 命中」重算**：三任务（含 task-013）在真实验证器下均为「基线可过、skill 剩成本面」形态；task-002 的翻盘结论本身有效（rollout 无篡改），预测器命中需以 task-002 + 有效批次数重算。
3. **失败层签名路由 + 严格闭环指标可复现地工作**：两个任务的 pd-vista 复现共 10 次披露，9 次干净闭环、1 次 gap=2 如实降级；unroutable 计数捕获了 task-011 r4 的 1 次空转轮。
4. **干净闭环收紧有效**：不再把「披露了但没读/隔轮才过」计为干净闭环（旧 task-013 r3 类虚报不再出现）。

## 5. 审计表

- [outputs/formal-task-011-zcode-local-4cond-x5-glm53-20261013-audit/task011_final_audit_zh.xlsx](../outputs/formal-task-011-zcode-local-4cond-x5-glm53-20261013-audit/task011_final_audit_zh.xlsx)（7 表，20 试验，程序化验证 0 issues，明细请求数 613 与 raw 对账一致）
- [outputs/formal-task-012-zcode-local-4cond-x5-glm53-20261013-audit/task012_final_audit_zh.xlsx](../outputs/formal-task-012-zcode-local-4cond-x5-glm53-20261013-audit/task012_final_audit_zh.xlsx)（7 表，20 试验，0 issues，295 与 raw 一致）
- task-013 沿用 [outputs/formal-task-013-zcode-local-3x3-glm53-20260930-15-audit](../outputs/formal-task-013-zcode-local-3x3-glm53-20260930-15-audit)（已验证）
