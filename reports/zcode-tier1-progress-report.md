# ZCode Tier 1 阶段报告 — task-011 / 012 / 013（glm53-flash）

运行日期：2026-09-30 ｜ 后端：ZCode CLI 0.16.9 headless（`ELECTRON_RUN_AS_NODE=1`）｜ 模型：`new-provider/glm53-flash` ｜ Docker：未使用 ｜ 本地验证器：SkillsBench `test_outputs.py` 路径改写副本

协议：`{task}-zcode-local-3x3-glm53-noskill-native-pd-vista-pd-self-3x3` — 每任务 **4 条件 × 3 复现 = 12 试验**（新增 pd-self：给 PD 格式 skill + 全部模块预先可见，由代理自行决定读哪些，无反馈披露步骤），交错调度，每回合上限 1800 秒 / 最多 6 回合，控制器逐回合跑隐藏验证器并回传脱敏诊断。批次前缀 `-20260930-15`。

**本报告为进行中的阶段快照**（截至 15:08，三组仍在后台运行）。

## 1. 任务来源与预测依据

Tier 1 三任务由 task-002 的 VISTA 成功预测器（5 特征）从 SkillsBench 87 任务中选出：

| 任务 | Benchmark | 域 | 预测特征匹配 |
|---|---|---|---|
| task-011 | sec-financial-report | Finance | 难任务、失败可诊断、skill 含修复知识、验证器快 |
| task-012 | invoice-fraud-detection | Finance | 同上（rapidfuzz 模糊匹配 + PDF/Excel 抽取） |
| task-013 | financial-modeling-qa | Finance | 数值约束任务（answer.txt 恒等于 "23"） |

## 2. 工程事件记录（本轮修复）

1. **task-011 unzip 崩溃**：`prepare_trial` 的 move 循环遍历 `destination.iterdir()` 时把 `move_top_to` 目标目录（`2025-q2`，建在 workspace 内部）也遍历到，空目录被 `rmdir()` 删除，后续移动 13F 文件报 ENOENT。已在循环加 `extracted != target` 守卫修复。
2. **磁盘写满（ENOSPC）**：task-011 每试验 workspace 827MB（zip 拷贝 168MB + 解压 ~660MB），12 试验约 10GB，三组全部中途死于 ENOSPC。处理：
   - 清理死证据：13 个 task005 时代 attempt 目录（~3.9GB）+ 三任务被污染的残缺试验目录与 raw 根；
   - runner 改为**共享解压缓存 + APFS clone**：zip 只解压一次进 `.shared-inputs/`（660MB 一次性），各试验用 `cp -c` 零空间 clone 进工作区（实测每次 prepare 开销 ~16KB vs copy2 的 84276KB），跨卷/失败自动回退 `copy2`；
   - 缓存 payload 解析修正：zip 内部顶目录名（`01JUN2025-31AUG2025_form13f`）与 `move_top_to` 目标名（`2025-q2`）不同，取缓存里唯一顶层目录。
3. 原 minimax/OpenCode 批次数据未做任何修改。

## 3. 阶段结果（截至 15:08）

### task-011 sec-financial-report（输出 answers.json；q1 ±0.1%、q2/q3 精确、q4 名称列表）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 6 | 27 | failed（6 回合未过） | — |
| native | 1 | 6 | 36 | failed（6 回合未过） | 全量 2 模块可用（读了 13f-analyzer + fuzzy-name-search） |
| pd-vista | 1 | 进行中 | 27+（turn 1-2 失败） | — | turn 2 披露 13f-analyzer |

### task-012 invoice-fraud-detection（输出 fraud_report.json；rapidfuzz 匹配）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 6 | 36 | failed（6 回合未过） | — |
| native | 1 | 6 | 26 | failed（6 回合未过） | 全量 3 模块可用（读了 fuzzy-match + pdf + xlsx） |
| pd-vista | 1 | 进行中 | 26+（turn 1-5 失败） | — | 依次披露 fuzzy-match → pdf → xlsx |

### task-013 financial-modeling-qa（输出 answer.txt = "23"）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 1 | 19 | passed | — |
| native | 1 | 1 | 19 | passed | 全量 2 模块可用（读了 pdf + xlsx） |
| pd-vista | 1 | 2 | 40 | passed_with_clean_vista_feedback_loop | turn 2 披露 pdf |

## 4. 早期信号

- **task-011/012 基线即失败**（noskill 与 native 各 1 试验 6 回合全败）——正是 Tier 1 预测器匹配的"基线 turn-1 失败 + 可诊断失败"特征。skill 是否带来准确率增益，取决于 pd-vista 能否在基线全败处通过；task-012 的 pd-vista rep1 已依次披露全部 3 个模块仍未过（截至 turn 5），形势偏紧。
- **task-013 与 task-002 同模式**：简单任务基线 turn 1 直通；pd-vista 出现完整干净闭环（turn 1 失败 → 披露 1 个模块 → turn 2 通过）。
- native 在两个难任务上重度消费 skill（读了全部模块）仍 6 回合全败——与论文失败模式 B（skill 挤占更强的 native 策略）方向一致。

## 5. 待完成

- 三组剩余试验（各 12 试验计划；截至 15:08 已完成/进行中 3-4 个/组）。
- 全部完成后：四条件（含 pd-self）汇总、与 task-002 特征签名对照（预期 pd-vista < pd-self < noskill < native）、更新审计 xlsx 与持久记忆。

数据目录：`data/raw/formal-task-01{1,2,3}-zcode-local-3x3-glm53-20260930-15/`
