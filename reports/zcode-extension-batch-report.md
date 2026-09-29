# ZCode 扩展批次报告 — task-008 / 009 / 010（glm53-flash）

运行日期：2026-09-29 ｜ 后端：ZCode CLI 0.16.9 headless（`ELECTRON_RUN_AS_NODE=1`）｜ 模型：`new-provider/glm53-flash`（宿主 provider_config 钉扎，无 `--model` 旗标）｜ Docker：未使用 ｜ 本地验证器：SkillsBench `test_outputs.py` 的路径改写副本（`/root` → 工作区，逐字节等价）

协议：`{task}-zcode-local-glm53-noskill-native-pd-vista-3x3` — 每任务 3 条件 × 3 复现 = 9 试验，交错调度，每回合上限 1800 秒 / 最多 6 回合；控制器逐回合跑隐藏验证器并回传脱敏诊断。

## 1. 条件汇总（每任务 3×3）

| 任务 | Benchmark | 条件 | 通过 | 请求 | 输入 tokens | 输出 tokens |
|---|---|---|---|---:|---:|---:|
| task-008 | adaptive-cruise-control | noskill | 3/3 | 107 | 6,284,741 | 156,882 |
| task-008 | adaptive-cruise-control | native | 3/3 | 134 | 10,602,351 | 216,014 |
| task-008 | adaptive-cruise-control | pd-vista | 3/3 | 130 | 10,925,111 | 261,729 |
| task-009 | r2r-mpc-control | noskill | 3/3 | 83 | 4,773,990 | 164,230 |
| task-009 | r2r-mpc-control | native | 3/3 | 84 | 4,433,659 | 139,872 |
| task-009 | r2r-mpc-control | pd-vista | 3/3 | 107 | 7,400,127 | 216,436 |
| task-010 | econ-detrending-correlation | noskill | 3/3 | 35 | 882,463 | 26,902 |
| task-010 | econ-detrending-correlation | native | 3/3 | 32 | 760,228 | 15,895 |
| task-010 | econ-detrending-correlation | pd-vista | 3/3 | 30 | 785,129 | 29,515 |

**准确率结论**：27/27 试验全部通过。这三个论文负收益任务（各 −5.6pp skill delta）在 glm53-flash 下基线即全通过，无准确率增益空间；skill 的作用只剩成本面。

**成本结论**：
- task-008：native 比 noskill 多花 25% 请求 / 69% 输入 tokens —— 重任务上完整 skill 包是纯负担；pd-vista（130 请求）未低于 noskill，披露节省被重任务的修复回合抵消。
- task-009：native ≈ noskill（84 vs 83 请求）；pd-vista 比 noskill 多花 29% 请求 —— 三次干净闭环都以额外回合为代价，但换来了 3/3 稳定通过。
- task-010：pd-vista 请求数最低（30 vs 35/32）—— 简单任务上 VISTA 开销可忽略，2/3 试验 turn 1 直接通过、无需披露。

## 2. 逐试验明细

### task-008 adaptive-cruise-control（输出 5 文件 + 12 pytest）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 2 | 43 | passed | — |
| noskill | 2 | 1 | 25 | passed | — |
| noskill | 3 | 1 | 39 | passed | — |
| native | 1 | 1 | 27 | passed | 全量 5 模块可用 |
| native | 2 | 2 | 48 | passed | 全量 5 模块可用 |
| native | 3 | 3 | 59 | passed | 全量 5 模块可用 |
| pd-vista | 1 | 2 | 37 | passed_with_clean_vista_feedback_loop | vehicle-dynamics |
| pd-vista | 2 | 1 | 28 | passed_without_clean_vista_feedback_loop | （turn 1 直接通过） |
| pd-vista | 3 | 2 | 65 | passed_with_clean_vista_feedback_loop | vehicle-dynamics |

### task-009 r2r-mpc-control（输出 2 文件 + 6 pytest）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 2 | 33 | passed | — |
| noskill | 2 | 2 | 34 | passed | — |
| noskill | 3 | 1 | 16 | passed | — |
| native | 1 | 2 | 36 | passed | 全量 4 模块可用 |
| native | 2 | 1 | 16 | passed | 全量 4 模块可用 |
| native | 3 | 2 | 32 | passed | 全量 4 模块可用 |
| pd-vista | 1 | 2 | 26 | passed_with_clean_vista_feedback_loop | state-space-linearization |
| pd-vista | 2 | 2 | 35 | passed_with_clean_vista_feedback_loop | state-space-linearization |
| pd-vista | 3 | 2 | 46 | passed_with_clean_vista_feedback_loop | state-space-linearization |

### task-010 econ-detrending-correlation（输出 1 文件 + 4 unittest）

| 条件 | rep | 回合 | 请求 | 状态 | 披露模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 1 | 16 | passed | — |
| noskill | 2 | 1 | 10 | passed | — |
| noskill | 3 | 2 | 9 | passed | — |
| native | 1 | 1 | 12 | passed | 全量 1 模块可用 |
| native | 2 | 1 | 10 | passed | 全量 1 模块可用 |
| native | 3 | 1 | 10 | passed | 全量 1 模块可用 |
| pd-vista | 1 | 2 | 16 | passed_with_clean_vista_feedback_loop | timeseries-detrending |
| pd-vista | 2 | 1 | 6 | passed_without_clean_vista_feedback_loop | （turn 1 直接通过） |
| pd-vista | 3 | 1 | 8 | passed_without_clean_vista_feedback_loop | （turn 1 直接通过） |

## 3. VISTA 反馈闭环指标

| 任务 | 触发机会（失败回合数） | 干净闭环 | turn-1 直通（无需披露） |
|---|---:|---:|---:|
| task-008 | 3 | 2/3 | 1/3 |
| task-009 | 3 | 3/3 | 0/3 |
| task-010 | 1 | 1/3 | 2/3 |

task-009 展示了最完整的渐进披露模式：每个 pd-vista 试验都是 turn 1 失败 → 披露 `state-space-linearization` → 代理读取 → turn 2 通过。task-008 同模式但披露 `vehicle-dynamics`，其中 rep3 的 turn 1 请求高达 49（修复回合消耗大）。task-010 任务太简单，2/3 试验无需披露即通过。

## 4. Native 模块访问（post-hoc 补扫）

运行中的代码只追踪 pd-vista 披露后的访问；native 条件的模块读取由证据目录中保留的 model-io rollout post-hoc 扫描补齐（`data/materialized/zcode-extension-batch/native-module-access-posthoc.json`）：

| 任务 | rep1 | rep2 | rep3 |
|---|---|---|---|
| task-008 | 全部 5 模块 | 未读任何模块 | 全部 5 模块 |
| task-009 | 全部 4 模块 | 全部 4 模块 | 全部 4 模块 |
| task-010 | timeseries-detrending | timeseries-detrending | timeseries-detrending |

native 确实在重度消费 skill（task-009 三次复现全部读完 4 个模块），但并未带来额外收益——与论文失败模式 B（skill 挤占更强的 native 策略）的观察方向一致。

## 5. 与 task-001 批次（OpenCode / minimax M3）的对照

| 维度 | task-001（OpenCode, minimax M3） | 本批次（ZCode, glm53-flash） |
|---|---|---|
| 通过率 | 9/9 | 27/27 |
| native 成本 | +74% 请求、0 准确率增益（纯负收益） | task-008 native +25% 请求、0 准确率增益；task-009 ≈ 持平 |
| pd-vista 成本 | 最低（116 请求，披露 2/6 模块） | task-010 最低；task-008/009 略高于 noskill |
| 反馈闭环 | 干净闭环，披露 2/6 模块 | task-009 3/3 干净闭环，每次只披露 1 个模块 |

两批一致的模式：**准确率饱和时 skill 的价值只体现在成本面，而完整 skill 包（native）在重任务上是纯负担；PD-VISTA 的按需披露把开销控制在最小（每次只路由 1 个模块），在简单任务上成本甚至低于 noskill。**

## 6. 运行参数与工程记录

- 回合超时从 420s（沿用 task-001 的 minimax 配置）上调至 1800s：glm53-flash 重任务单回合 13–49 次请求，420s/900s 均不够。
- 并发安全配置为「3 任务组后台并行 × 组内 jobs=1」；6 路并发会挤压 API 吞吐并引发 launcher 版本探测超时（已修复为可重试的 `LocalGateError`）。
- 遥测：rollout 的 usage 在 `response.usage`（`parse_model_io_rollout` 已修正；修正前首轮试验 usage 记为 0）。
- 过程中被隔离的 4 个首轮试验（420s/900s 超时所致）证据已全部删除重跑，最终 27 个 result.json 均为有效证据。

数据目录：`data/raw/formal-task00{8,9,10}-zcode-local-3x3-glm53-20260929-04/`（各含 readiness.json、group-summary.json、逐试验 result.json + turn 工件 + 验证器日志 + 输出文件副本）
