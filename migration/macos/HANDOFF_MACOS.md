# VISTA travel-planning macOS 迁移与实验交接

## 1. 交接目标

在另一台 Mac 上继续运行 `task-007-travel-planning` 的两轮 PD-VISTA 实验。运行方式是 macOS 系统 OpenCode，不使用 Docker、WSL 或容器。每个 replicate 使用独立 workspace 和独立 OpenCode session；同一 replicate 内保持同一 session。

当前正式有效样本数仍为 0。Windows 上的 `v6.5r2` 因 verifier 默认编码污染而排除；`v6.5r3`、`v6.5r4` 因 CodexZH 非重试型 HTTP 403 中断而隔离。Mac 实验必须使用新 RunId，不得续接这些 session。

## 2. 必须迁移的文件

| 路径 | 用途 |
| --- | --- |
| `pyproject.toml` | 项目元数据与基本依赖 |
| `src/` | Feedback Parser、Router、OpenCode 本机后端 |
| `tests/` | 实现回归与路由资格测试 |
| `skills/pd-vista/task-007-travel-planning/` | PD-VISTA 根 skill、manifest、来源说明 |
| `experiments/formal/run_travel_planning_vista_group.py` | 两轮正式实验运行器 |
| `experiments/formal/vista_macos_preflight.py` | Mac 零模型预检 |
| `experiments/formal/update_opencode_global_codexzh.py` | 可选的 OpenCode 配置合并工具 |
| `vendor/skillsbench/tasks/travel-planning/` | 原任务、约 32 MB 数据、6 个原 skill、10 项 verifier |
| `migration/macos/` | 本文档、依赖清单、脱敏配置模板 |

可选迁移：

- `data/raw/formal-vista-v65r2-...`、`v65r3-...`、`v65r4-...`：历史诊断证据，总计不足 2 MB。
- `outputs/formal_trial_detail_20260819_travel_diagnostics/formal_trial_detail_with_travel_diagnostics.xlsx`：已标注排除原因的结果表。

## 3. 不要迁移

- 不要复制整个 `data/raw/`；当前约 17.9 GB，其中 `_opencode_builds` 约 16.3 GB、`_pytest_runs` 约 1.0 GB，与 Mac 本机 travel 实验无关。
- 不要复制 `.venv/`、`node_modules/`、`__pycache__/`、`.pytest_cache/`、`.codex-artifacts/`。
- 不要复制 Windows `%LOCALAPPDATA%\VistaExperiments` 工作区。
- 不要复制 Docker image、Docker volume、WSL 数据或 `_opencode_builds`。
- 不要复制 `.env`、真实 API key、shell history 或含凭据的配置。
- 不要把 Windows 的 `C:\Users\...\.config\opencode\opencode.json` 原样复制到 Mac。

## 4. Mac 初始安装

以下命令均在 macOS Terminal 执行。建议项目路径使用 `~/vista`，不要放在 iCloud Drive 中。

### 4.1 安装 Homebrew、Node.js 和 Python

如果尚未安装 Homebrew，按照 <https://brew.sh/> 的官方命令安装。然后执行：

```bash
brew install node python@3.12
python3.12 --version
npm --version
```

### 4.2 安装与 Windows 实验一致的 OpenCode

```bash
npm install -g opencode-ai@1.18.18
which opencode
opencode --version
```

必须显示 `1.18.18`。Apple Silicon 通常位于 `/opt/homebrew/bin/opencode`，Intel Mac 通常位于 `/usr/local/bin/opencode`；运行器会通过 `PATH` 自动发现。

### 4.3 解压迁移包

```bash
mkdir -p ~/vista
cd ~/vista
unzip ~/Downloads/vista-travel-macos-handoff-*.zip
```

确认目录中直接存在 `src`、`experiments`、`skills` 和 `vendor`，不要多套一层无关目录。

### 4.4 创建 Python 虚拟环境

```bash
cd ~/vista
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r migration/macos/requirements-macos.txt
```

## 5. 配置 OpenCode 与环境变量

### 5.1 设置用户环境变量

不要把 key 写进项目文件。先在当前 Terminal 临时设置：

```bash
export OPENAI_API_KEY='在这里粘贴真实key'
export OPENAI_BASE_URL='https://api.codexzh.com/v1'
```

长期保存时可写入 macOS Keychain 或个人 shell 配置，但不得提交到项目。检查变量时只检查是否存在，不要打印 key：

```bash
test -n "$OPENAI_API_KEY" && echo 'API key present'
test -n "$OPENAI_BASE_URL" && echo 'Base URL present'
```

### 5.2 创建 OpenCode 配置

```bash
mkdir -p ~/.config/opencode
cp ~/vista/migration/macos/opencode.json.example ~/.config/opencode/opencode.json
```

模板只通过 `{env:OPENAI_API_KEY}` 引用 key；`baseURL` 是非敏感的固定地址，不包含真实 key。

## 6. 零模型预检

```bash
cd ~/vista
source .venv/bin/activate
export PYTHONPATH="$PWD/src:$PWD"
python experiments/formal/vista_macos_preflight.py | tee macos-preflight.json
```

只有最后的 `ready_for_health_check` 为 `true` 才能继续。预检必须同时证明：

- 平台为 macOS，Python >= 3.11；
- OpenCode 版本为 1.18.18；
- 依赖齐全；
- travel 任务数据、6 个原 skill、PD-VISTA manifest 均存在；
- macOS directory symlink 可用；
- verifier 收集到 10 项测试；
- 两个 API 环境变量存在。

然后运行项目回归测试：

```bash
python -m pytest -q tests/test_feedback_parser.py tests/test_router.py \
  tests/test_travel_planning_vista.py tests/test_opencode_local_backend.py
```

## 7. 最小 API 健康检查

在正式实验前，仅执行一次：

```bash
mkdir -p ~/Library/Caches/VistaExperiments/api-health
opencode run --pure --auto \
  --dir ~/Library/Caches/VistaExperiments/api-health \
  --format json \
  --model openai/gpt-5.6-terra \
  'Reply exactly READY. Do not use tools.'
```

必须出现 `step_start`、文本 `READY`、`step_finish`，且不能出现 403、502 或 `APIError`。

## 8. 运行两轮正式实验

为避免与 Windows 结果混淆，RunId 必须包含 `macos` 和新日期时间：

```bash
cd ~/vista
source .venv/bin/activate
export PYTHONPATH="$PWD/src:$PWD"
export VISTA_REPO_ROOT="$PWD"
export VISTA_EXPERIMENT_ROOT="$HOME/Library/Caches/VistaExperiments"
export VISTA_VERIFIER_PYTHON="$PWD/.venv/bin/python"
export VISTA_RUN_PREFIX="formal-vista-v65-macos-travel-gpt56terra-$(date +%Y%m%d-%H%M%S)"
export VISTA_PROTOCOL="vista-v65-macos-real-verifier-travel-utf8"
python experiments/formal/run_travel_planning_vista_group.py
```

不要同时启动第二个相同实验窗口。不要在运行中修改 `vendor/`、`skills/`、全局 OpenCode 配置或环境变量。

## 9. 成功验收标准

结果位于 `data/raw/$VISTA_RUN_PREFIX/`。检查 `group-summary.json`：

1. `planned_replicates = 2` 且 `completed_replicates = 2`；
2. 两个结果都不是 `quarantined_*`；
3. 每个 replicate 使用不同 `session_id`，同一 replicate 各回合 session 不变；
4. `docker_used = false`；
5. `distractor_flight_disclosed = false`；
6. `trigger_opportunities`、`routable_feedback`、`successful_disclosures`、`next_round_module_access` 分开报告；
7. 只有 `final_verifier_pass = true` 且发生披露与后续真实访问，才可记为 `clean_complete_closure = 1`；
8. verifier 日志不得出现 `UnicodeDecodeError` 或基础设施缺包错误。

## 10. 已知风险与停止条件

CodexZH 在 Windows 上多次出现：短 `READY` 请求成功，但长 OpenCode session 在多次工具调用后返回非重试型 HTTP 403“异常客户端”。Mac 可能仍会遇到同一服务端策略。

出现以下任一情况立即停止当前组，不要将其计入论文成功率：

- HTTP 403 provider policy rejection；
- HTTP 502 或持续 socket 断开；
- session ID 在同一 replicate 中变化；
- verifier `UnicodeDecodeError`、缺包或无法收集 10 项测试；
- `search-flights` 在没有 `flight_required` 的情况下被披露；
- Docker、容器或 WSL 被意外使用。

## 11. 给下一位执行者的实验口径

- 当前任务不是原六项正式任务中的 task-006，而是新增替代任务 `task-007-travel-planning`。
- `search-flights` 是禁止航班约束下的干扰模块；普通交通失败路由到 `search-driving-distance`。
- 原 skill 文件不能改写；披露时复制完整模块目录，包括 `scripts/`。
- 第一次有诊断价值的失败后立即披露，不等待第二次重复失败。
- 不允许把“成功复制模块”当作“模型真实访问模块”；必须从完成的工具事件中证明读取 `SKILL.md`。
- 被编码问题或 provider 错误污染的批次只能作为诊断记录，不能用于效能统计。
