# sj4 `pd-m` 补充实验：task-011/012 × 5

`pd-m` 首轮与原 `pd` 条件完全相同：完整 PD 工件从开始就在 workspace，
初始提示也相同。首轮失败后，它与 `vista` 使用同一套脱敏公共诊断、
同一 session、最多 6 回合的预算；`pd-m` 不运行路由，也不新增披露模块。
本实验只新增 `pd-m` 的 10 个 trial，不重跑已有的四条件 x5 批次。

## Mac 端运行

先更新含 `--conditions pd-m` 的 runner，并确认现有的 `vendor/skillsbench`、
`.venv/bin/python` 和 ZCode provider 配置可用。在仓库根目录打开两个终端：

```bash
# 终端 A：task-011
VISTA_RUN_PREFIX=formal-task-011-sj4-pdm-x5-glm53-<stamp> \
python experiments/formal/run_sj4_zcode_group.py \
  --task task-011 --provider new-provider --conditions pd-m \
  --replicates 5 --workers 4
```

```bash
# 终端 B：task-012
VISTA_RUN_PREFIX=formal-task-012-sj4-pdm-x5-glm53-<stamp> \
python experiments/formal/run_sj4_zcode_group.py \
  --task task-012 --provider new-provider-2 --conditions pd-m \
  --replicates 5 --workers 4
```

把两个命令中的 `<stamp>` 换成同一个开跑时间，例如 `20261015-10`。
正式运行前，可给上述命令末尾加 `--dry-run` 做预检；应看到 `"ready": true`。
续跑时使用原来的 `VISTA_RUN_PREFIX`，已有 `result.json` 的 trial 会跳过。
不要把 `pd-m` 放进旧 `sj4-x5` 目录；runner 会校验已有汇总的条件、
复现数、provider 和模型。

## 完成检查

每个目录应有 5 份 `pd-m/replicate-*/result.json`。检查：

- `group-summary.json` 显示 `conditions: ["pd-m"]`、`completed_trials: 5`；
- 每份结果的 `initially_available_modules` 为 `["skill/SKILL.md"]`，
  `disclosed_modules` 始终为空；
- `first_turn_pass` 与首轮 `verifier_pass` 一致；
- 隔离状态（`quarantined_*`）先排查并补跑，不计入有效通过率；
- 最终分析同时报告首轮通过、最终通过、回合数、agent 净时间、
  verifier 时间和输入/输出 Token。与旧 `pd`、`vista` x5 对比时标明这是独立新批次。

原始 `verifier-turn-*.txt` 含真值，只供离线审计；agent 收到的是
`public_diagnostic()` 产生的掩码文本。
