# ZCode Tier 1 批次报告 — task-011 / 012 / 013（glm53-flash）

运行日期：2026-09-30 ｜ 后端：ZCode CLI 0.16.9 headless（`ELECTRON_RUN_AS_NODE=1`）｜ 模型：`new-provider/glm53-flash`（宿主 provider_config 钉扎）｜ Docker：未使用 ｜ 本地验证器：SkillsBench `test_outputs.py` 路径改写副本（逐字节等价）

协议：`{task}-zcode-local-3x3-glm53-noskill-native-pd-vista-pd-self-3x3` — 每任务 **4 条件 × 3 复现 = 12 试验**（pd-self：PD 格式 skill + 全部模块预先可见，代理自选读取，无反馈披露步骤），交错调度，每回合上限 1800 秒 / 最多 6 回合，控制器逐回合跑隐藏验证器并回传脱敏诊断。批次前缀 `-20260930-15`。

任务来源：Tier 1 三任务由 task-002 的 VISTA 成功预测器（5 特征：基线难、失败可诊断、skill 含修复知识、验证器快、程序性缺口）从 SkillsBench 87 任务中选出。

## 1. 条件汇总（每任务 4×3）

| 任务 | Benchmark | 条件 | 通过 | 请求 | 输入 tokens | 输出 tokens |
|---|---|---|---|---:|---:|---:|
| task-011 | sec-financial-report | noskill | 0/3 | 89 | 3,673,430 | 88,676 |
| task-011 | sec-financial-report | native | 1/3 | 112 | 4,616,728 | 76,056 |
| task-011 | sec-financial-report | pd-vista | 1/3 | 154 | 10,665,104 | 240,031 |
| task-011 | sec-financial-report | pd-self | 0/3 | 142 | 7,773,373 | 236,700 |
| task-012 | invoice-fraud-detection | noskill | 1/3 | 109 | 5,971,079 | 133,577 |
| task-012 | invoice-fraud-detection | native | 1/3 | 76 | 3,575,641 | 118,987 |
| task-012 | invoice-fraud-detection | pd-vista | 0/3 | 103 | 6,987,583 | 183,433 |
| task-012 | invoice-fraud-detection | pd-self | 0/3 | 96 | 5,065,937 | 166,755 |
| task-013 | financial-modeling-qa | noskill | 3/3 | 72 | 2,201,666 | 57,475 |
| task-013 | financial-modeling-qa | native | 3/3 | 117 | 7,191,543 | 167,766 |
| task-013 | financial-modeling-qa | pd-vista | 3/3 | 87 | 3,888,150 | 89,174 |
| task-013 | financial-modeling-qa | pd-self | 3/3 | 112 | 4,798,414 | 146,739 |

**36/36 试验全部有效**。准确率结论：task-011 与 task-012 是基线即大部分失败的难任务（skill 有增益空间）；task-013 基线全通过（无增益空间）。

## 2. 逐试验明细

### task-011 sec-financial-report（输出 answers.json；q1 ±0.1%、q2/q3 精确、q4 名称列表）

| 条件 | rep | 回合 | 请求 | 状态 | 访问模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 6 | 27 | failed_max_turns | — |
| noskill | 2 | 6 | 35 | failed_max_turns | — |
| noskill | 3 | 6 | 27 | failed_max_turns | — |
| native | 1 | 6 | 36 | failed_max_turns | 读了 13f-analyzer + fuzzy-name-search |
| native | 2 | 3 | 41 | passed | 读了 13f-analyzer + fuzzy-name-search |
| native | 3 | 6 | 35 | failed_max_turns | 读了 13f-analyzer + fuzzy-name-search |
| pd-vista | 1 | 6 | 61 | passed | 披露 13f-analyzer（t2）+ fuzzy-name-search（t3） |
| pd-vista | 2 | 6 | 42 | failed_max_turns | 披露 13f-analyzer + fuzzy-name-search |
| pd-vista | 3 | 6 | 51 | failed_max_turns | 披露 13f-analyzer + fuzzy-name-search |
| pd-self | 1 | 6 | 35 | failed_max_turns | 自读 13f-analyzer + fuzzy-name-search |
| pd-self | 2 | 6 | 59 | failed_max_turns | 自读 13f-analyzer + fuzzy-name-search |
| pd-self | 3 | 6 | 48 | failed_max_turns | 自读 13f-analyzer + fuzzy-name-search |

### task-012 invoice-fraud-detection（输出 fraud_report.json；rapidfuzz 匹配）

| 条件 | rep | 回合 | 请求 | 状态 | 访问模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 6 | 36 | failed_max_turns | — |
| noskill | 2 | 5 | 45 | passed | — |
| noskill | 3 | 6 | 28 | failed_max_turns | — |
| native | 1 | 6 | 26 | failed_max_turns | 读了 fuzzy-match + pdf + xlsx |
| native | 2 | 2 | 20 | passed | 读了 fuzzy-match + pdf |
| native | 3 | 6 | 30 | failed_max_turns | 读了 fuzzy-match |
| pd-vista | 1 | 6 | 28 | failed_max_turns | 依次披露 fuzzy-match → pdf → xlsx |
| pd-vista | 2 | 6 | 35 | failed_max_turns | 同上 |
| pd-vista | 3 | 6 | 40 | failed_max_turns | 同上 |
| pd-self | 1 | 6 | 32 | failed_max_turns | 自读全部 3 模块 |
| pd-self | 2 | 6 | 36 | failed_max_turns | 同上 |
| pd-self | 3 | 6 | 28 | failed_max_turns | 同上 |

### task-013 financial-modeling-qa（输出 answer.txt = "23"）

| 条件 | rep | 回合 | 请求 | 状态 | 访问模块 |
|---|---|---|---:|---:|---|
| noskill | 1 | 1 | 19 | passed | — |
| noskill | 2 | 3 | 21 | passed | — |
| noskill | 3 | 2 | 32 | passed | — |
| native | 1 | 1 | 19 | passed | 读了 pdf + xlsx |
| native | 2 | 2 | 71 | passed | — |
| native | 3 | 2 | 27 | passed | — |
| pd-vista | 1 | 2 | 40 | passed_with_clean_vista_feedback_loop | 披露 pdf |
| pd-vista | 2 | 1 | 14 | passed（turn 1 直通） | — |
| pd-vista | 3 | 3 | 33 | passed_with_clean_vista_feedback_loop | 披露 xlsx |
| pd-self | 1 | 2 | 35 | passed | 自读 xlsx |
| pd-self | 2 | 2 | 39 | passed | 自读 xlsx |
| pd-self | 3 | 2 | 38 | passed | 自读 xlsx |

## 3. 结论

### 3.1 预测器验证：1/3 命中（弱验证）

- **task-011（sec-financial-report）**：预测器命中——这是 **task-002 之后第二个 noskill 全败（0/3）而 skill 条件通过（native 1/3、pd-vista 1/3）的任务**。VISTA 的准确率增益重现了，但很脆弱：三次复现只成一次，且成本最高（154 请求 / 10.67M in-tokens）。增益的机制仍然成立：pd-vista rep1 的通过发生在披露两个模块之后（t2 披露 13f-analyzer、t3 披露 fuzzy-name-search、t6 通过），而 pd-self 同样读了两个模块却 0/3——反馈回路把 0/3 变成了 1/3。
- **task-012（invoice-fraud-detection）**：预测器失效——pd-vista 0/3 通过且最贵（三次复现每次披露全部 3 模块，六回合全败），native 反而最省（76 请求）。"skill 含修复知识"特征在这里不充分：fuzzy-match/pdf/xlsx 模块解决的是机制层，任务的失败模式（欺诈判定逻辑）不可由这些模块修复。
- **task-013（financial-modeling-qa）**：与 task-002/008/009/010 的简单任务形态一致——基线全通过，skill 只剩成本面；pd-vista（87 请求）明显低于 native（117），两次干净闭环。

### 3.2 反馈回路的贡献（pd-vista vs pd-self）

| 任务 | pd-vista | pd-self | 回路贡献 |
|---|---|---|---|
| task-011 | 1/3，154 req | 0/3，142 req | **准确率**：0/3 → 1/3（代价 +12 请求） |
| task-012 | 0/3，103 req | 0/3，96 req | 无 |
| task-013 | 87 req | 112 req | **成本**：−25 请求 / −0.91M tokens |

反馈回路在 task-011 上是唯一把失败变成通过的变量（pd-self 读了完全相同的模块但没通过）——这把 task-002 中"回路贡献小（4-11%）"的结论修正为：**回路贡献在难任务上可以是准确率级的，在简单任务上是成本级的**。

### 3.3 与既有批次的衔接

- 两个后端、15 个任务的总图景不变：VISTA 的独有增益仍然稀有（task-002、task-011 两例）；native 全量包多数情况是负担（task-008、task-013），少数情况最省（task-012）。
- task-002 的 5 特征预测器方向正确但判别力不足：Tier 1 三个任务都匹配了特征，只有 task-011 出现增益。缺失的判别特征疑似是**"skill 模块修复的失败层"**——模块必须修复导致验证器失败的同一层（task-011 的数据解析/名称匹配层），而不是任务的外围机制（task-012 的 PDF/Excel 抽取层）。

## 4. 工程记录

- **task-011 unzip 崩溃**：move 循环遍历 `destination.iterdir()` 时把 `move_top_to` 目标目录也遍历到并被 `rmdir()`，报 ENOENT；加 `extracted != target` 守卫修复。
- **磁盘写满（ENOSPC）**：13F 任务每试验 workspace 827MB（zip 拷贝 168MB + 解压 ~660MB），12 试验约 10GB，首批三组全部死于 ENOSPC。runner 改为**共享解压缓存 + APFS clone**：zip 只解压一次进 `~/Library/Caches/VistaExperiments/.shared-inputs/`，各试验用 `cp -c` 零空间 clone 进工作区（每次 prepare 开销 ~16KB vs copy2 的 84MB）；清理 task005 时代 13 个 attempt 死目录（~3.9GB）与被污染的残缺目录。
- 并发安全配置：3 任务组后台并行 × 组内 jobs=1；task-011 曾中途中止（用户暂停），恢复时 runner 自动跳过 10 个已完成试验、只补跑剩余 pd-self ×2。
- 原 minimax/OpenCode 批次数据未做任何修改。

数据目录：`data/raw/formal-task-01{1,2,3}-zcode-local-3x3-glm53-20260930-15/`（各含 readiness.json、group-summary.json、逐试验 result.json + turn 工件 + 验证器日志 + 输出文件副本）
