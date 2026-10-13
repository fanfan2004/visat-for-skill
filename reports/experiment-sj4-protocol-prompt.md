# 新实验设计全面提示词：四条件（noskill / flat / pd / vista）× 3 重复

> 用途：本提示词是 PD-VISTA 项目新实验设计的完整协议说明，可直接交给执行端（agent 会话或 runner 作者）作为唯一的实施依据。
> 项目：E:\git-hub\vista-mac\visat-for-skill（PD-VISTA 三条件试点演进的第四代设计，下称 sj4 设计）。

## 一、背景与目标

- 研究问题：agent 技能的组织方式（flat vs pd）与失败反馈驱动的披露机制（vista）分别对任务表现、成本、行为有什么影响。
- 任务：SkillsBench 本地化任务（task-011 = sec-financial-report 财报分析、task-012 = invoice-fraud-detection 发票欺诈检测等，沿用 zcode-local-minimal-dependencies 后端，**全程无 Docker**）。
- 运行模型：glm53-flash（ZCode 本地后端）。
- 参考协议：SkillJuror（github.com/zhiyuchen-ai/skill-juror，本地 E:\git-hub\skilljuror\skill-juror）的 flat（origin-flat）与 pd（progressive-disclosure）变体；SkillsBench 的 no-skill 控制组。

## 二、四条件协议

### 1. noskill（控制组）
- **单回合**：一个 headless ZCode session，agent 自由多步工作直到自己宣布完成，步数不限，只受 wall-clock 预算约束。
- 无技能、无反馈。验证器在回合结束后跑一次；**失败即停止**，无第二回合。
- 等价于 SkillsBench/SkillJuror 的 no-skill：单 episode、验证器末端一次、二值通过。

### 2. flat（flatten-source，严格按 SkillJuror）
- **单回合，无反馈**，协议同 noskill。
- 技能：原始 modules bundle 经 `skill-for-skill-flatten-source` transform 压平——每个技能根只保留一个 SKILL.md，全部支持内容（代码、脚本、schema、模板、参考文档）内联进该文件，语义零损失。
- 投放：工件完整复制进 workspace，初始 prompt 点名技能位置；agent 一读全读。

### 3. pd（progressive-disclosure，严格按 SkillJuror）
- **单回合，无反馈**，协议同 noskill。
- 技能：按 SkillJuror 链构造——bundle → single-file → progressive-disclosure。入口 SKILL.md ≤150 行（概览、能力地图、关键约束、具体加载触发器），references/ 放详细操作内容，每个 reference 必须在入口里有具体触发器（何时打开、为什么、支持什么决策）。
- 投放：工件（入口+references 树）完整复制进 workspace，全程在场；**agent 自主导航**——读入口、按触发器自己决定展开哪些 references。无任何运行时披露机制。

### 4. vista（唯一多回合组）
- **多回合**：最多 MAX_TURNS 回合；回合失败 → 脱敏后的公共诊断 + 失败层签名 → 路由 → 披露对应模块（PD 结构，与 pd 条件同源）→ 下一回合（同 session resume）读模块修复 → 直到通过或超限。
- 无前置技能（第一回合在信息态上等价于 noskill）。
- 停止条件：验证器通过 / MAX_TURNS / 模型回合超时 / 验证器基础设施错误 / 披露后连续 2 轮无进展（stall）。
- 保留现有 vista_metrics 全套指标。

## 三、Runner 必改项（前置条件）

1. **反馈数值脱敏**：`public_diagnostic` 必须把 pytest 断言失败行 `E assert X == Y` 的两侧数值脱敏（如 `E assert <actual> == <expected>`）。教训：20261013 批次 noskill rep1 在 turn-2 直接采纳诊断里泄露的标准答案 2451。`verifier`/`ground_truth`/`expected_output` 路径片段的现有脱敏保留。
2. **回合结构分条件**：noskill/flat/pd 固定 1 回合，代码里不进反馈循环；MAX_TURNS 只对 vista 生效。
3. **工件投放**：flat/pd 的工件复制进 workspace（替代现 native 的模块复制方式）；初始 prompt 点名技能位置。
4. **现有保护保留**：验证器目录在 agent 不可触及位置；验证器路径片段从公开诊断清洗；模型身份一致性 fail-closed。
5. **vista 的披露模块与 pd 同源**：vista 披露的模块用 PD 结构（入口+references），保证 pd vs vista 的唯一差异是"披露驱动者"（agent 自主导航 vs 失败反馈路由）。

## 四、工件构造

- **构造模型**：用项目可用的最强模型（不用 glm53-flash 跑构造），构造模型记录进报告（构造质量是混淆源）。
- **transform 技能来源**：`E:\git-hub\skilljuror\skill-juror\skill-for-skill\transform-skills\`（skill-for-skill-single-file、skill-for-skill-progressive-disclosure、skill-for-skill-flatten-source）。
- **链式顺序**：`modules/` bundle → single-file（先合成任务级单文件）→ pd；flat 直接从 bundle 压平。
- **粒度**：每任务一个工件（把该任务全部模块合并为任务级单技能；references 按原模块组织），符合 SkillJuror 的单技能形态。
- **校验（必须全部通过）**：`behavior-unit-diff.mjs` 对源头语义 diff（risk=high 必须修到消除；medium 检查真实遗漏）；pd 另过 `pd-path-hygiene.mjs`（路径一致性、无重复副本、无 __pycache__）。脚本在 `E:\git-hub\skilljuror\skill-juror\scripts\`。

## 五、记录指标（公平口径 + 机制口径）

每 trial 记录：

1. **首轮通过 first_turn_pass**——公平口径：flat/pd 的单回合结果、vista 的第一回合结果、noskill 的单回合结果三者同口径可比。
2. **最终通过 final_verifier_pass**——机制口径：含 vista 多回合换来的通过。
3. **token 消耗**：输入/输出分列；**技能阅读 token 单列**（flat/pd 记录 agent 读了哪些技能文件、各多少 token——flat 一读全读 vs pd 按需读是组织方式对比的核心观测）。
4. **轮次 turns**（noskill/flat/pd 恒为 1；vista 记录实际回合数）。
5. **时间**：agent 净时间（回合内 session 时间）与 verifier 时间**分列**；cost per pass 用 agent 净时间（vista 多回合的 verifier 累积时间不得计入 agent 成本）。
6. **vista 披露指标**（沿用 vista_metrics）：trigger_opportunities、routable_feedback、unroutable_feedback、successful_disclosures、disclosure_to_pass_gap、clean_complete_closure、stall 事件。

## 六、实验规模与统计口径

- 4 条件 × 3 重复 = 12 trials/任务；**pd vs vista 关键对比建议 5 次重复**。
- 单任务 3 次只做描述性结论（通过率只能取 0/3~3/3，区分度有限）；任务级结论用多任务聚合分布（对齐 SkillJuror 的"变强/持平/变弱"口径）。
- 通过率同时报告两个口径：首轮通过率（公平）与最终通过率（机制 + 成本）。

## 七、数据保留与批次命名

- **`data/raw/` 旧批次原样不动**，三代归档：20260822~27（MiniMax-M3 旧 3×3）、20260929~30（ZCode 扩展，含已隔离的 verifier 泄漏批次，QUARANTINE.txt 在案）、20261013（旧 4cond：native/pd-self + 多轮反馈全员）。
- 旧批次是旧设计下的有效结果（除已隔离者），保留其结论（"准确率饱和时价值在成本面""完整包在重任务是负担"等引用旧数据）。
- **新批次命名**：`formal-task-<id>-sj4-x3-glm53-<date>`；协议字符串写明 `single-episode-for-noskill-flat-pd / multi-turn-feedback-vista-only`，新旧协议一眼可分。
- **新旧数据不合并统计**；清单表格给每批次标协议版本（旧 3×3 / 旧 4cond / 新 sj4×3）。

## 八、实施顺序

1. 修 runner：反馈数值脱敏 + 分条件回合结构（noskill/flat/pd 单回合、无反馈；vista 多回合）。
2. 构造 flat/pd 工件（强模型 + SkillJuror transform 链）并过全部校验。
3. 跑 4 条件 × 3 重复，按第五节记录全部指标。
4. 更新清单表格（标代际、双口径通过率），产出实验报告。

## 九、已知教训（必须避免）

1. 验证器 ground truth 与 agent 同目录可达 → agent 伪造自证（20260930-15 批次 4 次假通过）→ 验证器已移出 agent 可触及范围，保留。
2. 公共诊断泄露断言数值 → agent 抄答案（20261013 noskill rep1）→ 本设计第三节第 1 条强制脱敏。
3. 失败反馈对所有条件无差别回传 → 反馈信息与披露机制混淆 → 本设计只有 vista 有反馈路径。
4. 多回合只在 vista 存在 → 成功率对比结构性偏 vista → 必须双口径报告（首轮通过 + 最终通过 + 成本）。
