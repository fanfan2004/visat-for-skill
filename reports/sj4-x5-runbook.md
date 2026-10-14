# sj4 x5 批次执行手册（Mac 端）

> 目的：用修正后的 runner 把 sj4 四条件（noskill / flat / pd / vista）扩到每条件 5 复现，
> task-011（sec-financial-report）+ task-012（invoice-fraud-detection），共 40 个试验。
> 现有 x3 批次（`formal-task-01{1,2}-sj4-x3-glm53-20261013-21`）保留作「旧版掩码」对照，不动。

## 0. 这一版 runner 与 x3 批次的差异

| 项 | x3 批次（7adfe2c） | 本批次 |
|---|---|---|
| 诊断掩码 | 断言两侧整体掩掉，agent 看不到哪个字段错 | 保留字段名、只掩值（含 Expected/Got 裸值） |
| ZCode 报错（如 API ENOTFOUND） | 照常跑验证器并进入反馈 | 记 `api_error` 立即停止，状态 `quarantined_api_error` |
| 技能读取 token | 记的是请求整段上下文输入（口径错） | 被读文件本身的估计 token |
| 回合用量 | 无 | 每回合 `turn_usage`（请求数/输入/输出/缓存读） |
| 复现数 | 写死 3 | `--replicates N`，目录名 `sj4-x<N>` |
| 结果目录 | 每次调用取当前小时，跨整点会分裂 | 启动时冻结一次 |
| 补跑覆盖 | 残留旧 verifier-turn-N | 写结果前清理旧回合证据 |

因为掩码变了，**x5 是新批次，不要和 x3 混算**。

## 1. 准备

```bash
cd <仓库路径>/visat-for-skill
git pull
```

环境与 x3 批次相同：`.venv`（验证器 Python）、`vendor/skillsbench/tasks`、ZCode.app、
两套 provider 配置（`new-provider` 宿主默认；`new-provider-2` 用
`~/.zcode/v2/provider_config_v3_corrected.json`）。

预检（不调模型，两条都应输出 `"ready": true`）：

```bash
python experiments/formal/run_sj4_zcode_group.py --task task-011 --provider new-provider --replicates 5 --dry-run
python experiments/formal/run_sj4_zcode_group.py --task task-012 --provider new-provider-2 --replicates 5 --dry-run
```

## 2. 正式运行（两个终端并行，与 x3 相同的双端点分工）

**务必显式设定 `VISTA_RUN_PREFIX`**，这样中断后可以用同一条命令续跑（已有 `result.json` 的 trial 自动跳过）。
把下面的 `<stamp>` 换成开跑时间，例如 `20261015-10`，两个终端用同一个。

终端 A（task-011，端点 new-provider）：

```bash
VISTA_RUN_PREFIX=formal-task-011-sj4-x5-glm53-<stamp> \
python experiments/formal/run_sj4_zcode_group.py --task task-011 --provider new-provider --replicates 5 --workers 4 \
  2>&1 | tee task-011-x5.log
```

终端 B（task-012，端点 new-provider-2）：

```bash
VISTA_RUN_PREFIX=formal-task-012-sj4-x5-glm53-<stamp> \
python experiments/formal/run_sj4_zcode_group.py --task task-012 --provider new-provider-2 --replicates 5 --workers 4 \
  2>&1 | tee task-012-x5.log
```

- 启动后第一行会打印 `result root: .../data/raw/formal-task-01X-sj4-x5-glm53-<stamp> (replicates=5)`，核对一下。
- 预计每个任务 1–1.5 小时（x3 批次 12 trial 约 40–60 分钟）。
- 中断（网络、合盖）后直接重跑同一条命令即可续跑；半成品工作目录会被自动归档为 `*-abandoned-<时间>`。

## 3. 跑完后的检查

### 3.1 每个 trial 的状态

```bash
python - <<'EOF'
import json, glob
for f in sorted(glob.glob("data/raw/formal-task-01*-sj4-x5-glm53-*/*/replicate-*/result.json")):
    d = json.load(open(f))
    turns = [(t["turn"], t["return_code"], t["requests"]) for t in d["turns"]]
    print(d["task_id"], d["condition"], d["replicate_id"], d["status"], turns)
EOF
```

正常状态：`passed`、`passed_with_clean_vista_feedback_loop`、`passed_without_clean_vista_feedback_loop`、
`failed_single_episode_failed`、`failed_max_turns`、`failed_stalled_after_full_disclosure`。

**需要补跑的状态**：`quarantined_api_error`、`quarantined_model_turn_timeout`、
`quarantined_verifier_infrastructure_error`、`quarantined_session_identity_changed`。

### 3.2 补跑隔离的 trial

不要直接覆盖，先把旧证据移到组目录下的 `_quarantine/`（不能留在 `<condition>/` 下，否则汇总脚本会把它当成试验）：

```bash
G=data/raw/formal-task-011-sj4-x5-glm53-<stamp>
mkdir -p $G/_quarantine
mv $G/vista/replicate-02 $G/_quarantine/vista-replicate-02-api_error
```

然后用**同一个 `VISTA_RUN_PREFIX`** 重跑第 2 节的命令，只会补跑缺失的 trial。
在提交信息里写明补跑了哪个 trial、原因是什么。

### 3.3 掩码抽查（确认反馈里没有真值）

```bash
PYTHONPATH=src:experiments/formal python - <<'EOF'
import glob, re
import run_sj4_zcode_group as m
leaks = 0
files = glob.glob("data/raw/formal-task-01*-sj4-x5-glm53-*/*/replicate-*/verifier-turn-*.txt")
for f in files:
    raw = open(f, encoding="utf-8", errors="replace").read()
    if "FAILED" not in raw:
        continue
    fed = m.public_diagnostic(raw)
    values = {v for t in re.findall(r"Expected '([^']+)'|Got '([^']+)'|assert (\d{2,}) == (\d{2,})", raw) for v in t if v}
    hit = [v for v in values if v in fed]
    if hit:
        leaks += 1
        print("LEAK", f, hit)
print("verifier outputs:", len(files), "leaks:", leaks)
EOF
```

期望 `leaks: 0`。不为 0 就先停下，不要提交，把输出发回来。

## 4. 生成派生数据（可选，Windows 端也会做）

```bash
python experiments/formal/recompute_sj4_tokens.py --tag x5 \
  --group task-011=data/raw/formal-task-011-sj4-x5-glm53-<stamp> \
  --group task-012=data/raw/formal-task-012-sj4-x5-glm53-<stamp>
python experiments/formal/estimate_sj4_cache_cost.py --tag x5
```

输出在 `data/derived/sj4_x5_*`（gitignored，不提交）。`all_turn_receipts_reconcile` 应全部为 `True`。

## 5. 提交

```bash
git add data/raw/formal-task-011-sj4-x5-glm53-<stamp> data/raw/formal-task-012-sj4-x5-glm53-<stamp>
git commit -m "完成 sj4 4×5 批次（新版掩码 + api_error 隔离）：task-011/012"
git push
```

不要提交：`task-01X-x5.log`（可附在提交说明里摘要）、`~/Library/Caches/VistaExperiments` 下的工作目录。
push 若遇 TLS 断开，可用 `git -c http.version=HTTP/1.1 push` 重试。

## 6. 回传给 Windows 端的信息

- 两个组目录名（含 `<stamp>`）
- 3.1 的状态列表、是否有补跑及原因
- 3.3 的泄漏检查结果
- 运行中的异常（API 挂起、超时、报错）
