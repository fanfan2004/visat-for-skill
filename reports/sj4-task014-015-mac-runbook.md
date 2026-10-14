# task-014 / task-015：Mac 双端口执行手册

准备日期：2026-10-14。协议：`sj4-extension-v1`。

## 1. 本轮做什么

| 项目 | 配置 |
|---|---|
| task-014 | `energy-unit-commitment`：48 时段机组组合、运行约束和成本 |
| task-015 | `xlsx-recover-data`：四张表、15 个缺失值及跨表依赖 |
| 条件 | `noskill / flat / pd / pd-m / noskill-multi / vista` |
| 每条件重复 | 默认 5 次；不是 10 次 |
| trial 总数 | 每任务 30 次，两个任务共 60 次 |
| A 端点 | `new-provider`，task-014，最多 10 个并发 trial |
| B 端点 | `new-provider-2`，task-015，最多 10 个并发 trial |
| 模型 | 两端均由现有 fail-closed gate 核对为 `glm53-flash` |
| 执行方式 | 本地 ZCode，使用现有两个 provider 配置；不使用 Docker |

这里“10 线程”是 runner 的 `ThreadPoolExecutor` worker 数，一个 worker
负责一个 trial。它不是单个模型请求的线程数，也不是求解器内部的线程数。
20 个 trial 可以同时工作，但各自可能在等待 API、工具或本地计算资源。
两个 provider 仍可能共享供应商的上游速率限制。

## 2. 16GB 或更少内存会不会卡死

有可能明显卡顿、产生大量交换内存，不能承诺不会卡死。
主要压力来自最多 20 个 ZCode 进程、agent 的工具子进程、MILP 和 LibreOffice。
模型推理在远端并不意味着本地没有内存和 CPU 开销。

本次默认保护是两个端点共用的：

- 验证器同时最多 **1 个**；排队时间另记，不占验证器自身 900 秒超时。
- 正常 Python 中的 `scipy.optimize.milp` 同时最多 **2 个**，包括 agent 和验证器。
- BLAS/OpenMP/HiGHS 每个数值进程设为 **1 线程**。
- 各 trial 使用独立 LibreOffice profile，避免多个 `recalc.py` 抢同一 profile。

保护通过外部 `sitecustomize.py` 和环境变量实施，对所有六个条件一致。
它没有增加求解方法、改变目标函数、修改数据、缩短 solver 原有时间上限或修改判题容差。
[SciPy MILP](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)
使用 HiGHS；`threads`、`parallel` 是
[HiGHS 选项](https://ergo-code.github.io/HiGHS/dev/options/definitions/)。

限制不是整机内存硬上限：模型组装发生在调用 MILP 之前，其他求解器、
`python -S`、独立二进制和主动覆盖环境变量可以绕过该 Python hook。
LibreOffice 同时运行数也未限制。因此“各 10 并发”仍是一次有风险的尝试。

运行时打开“活动监视器 → 内存”。内存压力持续黄/红、界面严重卡顿时，
在启动实验的终端按 **Control+C**。启动器会停止两个 controller 的进程组，
保留已完成 trial；随后按第 6 节降到每端点 3 或 4 个 worker 续跑。
不要为了维持 10 并发忽视内存压力。

## 3. 准备环境

以下命令用于 **Mac 的 Terminal（zsh/bash）**，不要原样放到 Windows PowerShell。
进入本仓库的 `visat-for-skill` 根目录；路径换成你 Mac 上实际的路径。

```bash
cd /你的实际路径/visat-for-skill
git pull --ff-only
.venv/bin/python --version
```

`git pull` 若提示本地修改冲突，先保留那些修改；不要 reset 或删除实验结果。
本次两个任务的固定源快照、输入、验证器和 Skill 工件已随代码提交，
**不需要重新构造 Skill，也不依赖 vendor 任务版本**。

源环境及本地数值验证使用 Python 3.11/3.12、SciPy 1.13.1。
已有 `.venv` 若是 Python 3.11/3.12，可以安装本轮固定依赖：

```bash
.venv/bin/python -m pip install -r experiments/formal/requirements-sj4-extension.txt
```

如果原 `.venv` 是 Python 3.13 或更新版本，使用已安装的 `python3.12`
新建独立环境，保留原环境。不要在不支持的 Python 上强行编译旧版 SciPy：

```bash
python3.12 -m venv .venv-sj4ext
.venv-sj4ext/bin/python -m pip install -r experiments/formal/requirements-sj4-extension.txt
export VISTA_VERIFIER_PYTHON="$PWD/.venv-sj4ext/bin/python"
```

若提示 `python3.12: command not found`，在已有 Homebrew 的 Mac 上先运行
`brew install python@3.12`，再执行上述命令。
以下各节命令中的 `.venv/bin/python` 同样替换成 `.venv-sj4ext/bin/python`。
`VISTA_VERIFIER_PYTHON` 必须保留，它同时让 agent 工具优先使用这个环境。

task-015 的原始 Skill 假设 LibreOffice 可用。未安装时，在已安装 Homebrew 的
Mac 上可运行以下命令；已经有 LibreOffice 的无需重复安装：

```bash
brew install --cask libreoffice
```

runner 会识别 `/Applications/LibreOffice.app/Contents/MacOS/soffice`，
无需修改全局 PATH。ZCode 与之前两端 provider 配置继续沿用；
`new-provider-2` 需要现有的 `~/.zcode/v2/provider_config_v3_corrected.json`。

## 4. 先预检，不调用模型

选一个批次标签，并在本次运行、查询和续跑中始终用同一个。
下面用 `20261014-2130` 举例；可以替换成你的开跑日期时间。

```bash
.venv/bin/python experiments/formal/build_sj4_extension_artifacts.py --check
.venv/bin/python experiments/formal/run_sj4_extension_pair.py \
  --stamp 20261014-2130 --workers 10 --replicates 5 --dry-run
```

第一条检查源快照与工件 hash，不重建、不调模型。
第二条检查两端 provider/model、ZCode launcher、输入、工件、依赖、LibreOffice
及资源 hook。资源 hook 只解一个单变量小问题，**不会求解正式网络或调用模型**。

两端应都显示 `exit=0`。详细日志位于：

```text
data/derived/sj4ext-logs-20261014-2130/task-014-preflight.log
data/derived/sj4ext-logs-20261014-2130/task-015-preflight.log
```

打开日志分别确认 `"ready": true`，再运行下一节。
如未通过，保留日志并排查对应 `false` 项，不要重新构造工件来绕过 hash 检查。

## 5. 正式运行：两个端点各 10 个 worker

一个终端即可启动两端并行，日志分别保存，不用手动开两个 runner：

```bash
.venv/bin/python experiments/formal/run_sj4_extension_pair.py \
  --stamp 20261014-2130 --workers 10 --replicates 5
```

启动时打印两个 PID 和日志路径。运行日志为同目录下的
`task-014-run.log` 与 `task-015-run.log`。可在另一个 Terminal 查看：

```bash
tail -f data/derived/sj4ext-logs-20261014-2130/task-014-run.log
```

这里按 Control+C 只停止 `tail`；停止实验需要在第一个启动器终端按 Control+C。
运行期间保持 Mac 唤醒，避免合盖或网络切换。总耗时暂不估计：MILP、API 限流
和资源排队均可能明显拉长时间。

只读查看已经完成的 trial：

```bash
.venv/bin/python experiments/formal/run_sj4_extension_pair.py \
  --stamp 20261014-2130 --replicates 5 --status
```

## 6. 中断与续跑

使用**原批次标签与原重复数**重跑，已有 `result.json` 会核对身份后跳过。
中断 trial 的工作目录和未完成结果目录会单独归档，不删除。

内存不足时可以只降低 worker 数，例如每端点 3：

```bash
.venv/bin/python experiments/formal/run_sj4_extension_pair.py \
  --stamp 20261014-2130 --workers 3 --replicates 5
```

记录什么时候从 10 改为 3；成功率可以分析，但不要把混合并发下的墙钟时间
当作统一并发条件的性能结果。solver 与 verifier 的全局额度保持不变。
不要同时启动两个使用相同 `--stamp` 的启动器。

`quarantined_*` 是基础设施问题，不作为任务能力失败。它们也会被自动跳过，
所以需先手动把**一个明确 trial 目录**移到组根目录的 `_quarantine` 下，再续跑。
一次只处理一个目录，保留原因，不能直接覆盖。示例：

```bash
mkdir -p data/raw/formal-task-014-sj4ext-six-x5-glm53-20261014-2130/_quarantine
mv data/raw/formal-task-014-sj4ext-six-x5-glm53-20261014-2130/pd-m/replicate-02 \
   data/raw/formal-task-014-sj4ext-six-x5-glm53-20261014-2130/_quarantine/pdm-rep02-api-error
```

组内汇总不会读取 `_quarantine` 和 `_interrupted`。

## 7. 协议及研究解释

- `noskill / flat / pd` 固定单回合。
- `pd-m / noskill-multi / vista` 最多 6 回合，沿用同一 session。
- `pd` 与 `pd-m` 首轮工件、提示相同；三个首轮无 Skill 的组使用相同首轮提示。
- 多轮组三者接收相同的公开失败类别和 test 名。pytest 参数化 ID、断言数值、
  oracle 源码和错误消息中的真值都不进入新任务的公开诊断。
- 三个多轮组使用相同停滞规则：失败层签名连续两次未改变便停止。
  新披露不额外延长预算。回合上限相同不代表实际 token 和计算预算相同。
- VISTA 按失败 test 路由到相应 PD reference；PD-M 与 NoSkill-Multi 不运行披露。

这版采用类别反馈及统一停滞规则，与 011/012 的旧 sj4 批次**不合并统计**。
主要比较：PD 与 PD-M 的反馈收益；NoSkill-Multi 与 VISTA 的披露收益；
PD-M 与 VISTA 在最终通过率和总成本上的差异。

task-015 有一个事先识别的基准冲突：源 `xlsx` Skill 要求保留 Excel 公式，
但原验证器 `openpyxl.load_workbook(path)` 没有 `data_only=True`，
恢复单元格的检查要求数字。保留公式即使有计算缓存，也可能被读成公式字符串。
**此轮完整保留源 Skill、原题及原判题规则，不额外告诉 agent 如何绕过。**
结果应单列公式类型失败与数值错误，不能把它直接解释为模型推理或披露能力不足。

固定源快照没有上游 Git metadata；以提交的文件 hash 标识版本，不虚构 upstream SHA。
工件采用确定性原文拆分/内联，未调用构造模型；此前 011/012 使用的模型构造
工件是不同构造方式，也需在比较中注明。

## 8. 保留的证据与成本口径

每个 trial 除原结果外，新增实际 `prompt-turn-N.txt`、逐轮输出快照、
session 的 `model-io.jsonl`（若 ZCode 产生）、solver 耗时/排队日志及资源配置。
`agent_net_seconds` 仍为模型请求耗时之和，**不代表全部本地求解墙钟时间**。
`verifier_seconds` 不含 verifier 外部排队；`verifier_queue_seconds` 单列。
solver 排队在模型回合超时内，可能影响完成率，分析时必须报告。
原始 verifier 日志含真值，只用于 controller 的离线审计。

完成后每任务应有 30 份结果及一个 `group-summary.json`；汇总提供有效数量、
隔离数量、有效首轮/最终通过数，避免把基础设施失败混入通过率分母。
本次没有在 Windows 启动真实模型试验；Mac 的真实结果尚待你执行。

## 9. 跑完提交结果

先执行第 5 节的 `--status`，确认每任务 30 次都已有结果，检查是否存在隔离试验。
`model-io.jsonl` 保存完整模型及工具轨迹，提交前检查是否意外含凭据等不宜上传的信息。
只提交本轮两个组目录，日志、Mac 缓存和 Python 环境无需提交：

```bash
git add data/raw/formal-task-014-sj4ext-six-x5-glm53-20261014-2130 \
        data/raw/formal-task-015-sj4ext-six-x5-glm53-20261014-2130
git commit -m "Run task-014/015 six-condition sj4 extension x5"
git push
```

回传：两组目录名、是否从 10 降过并发、隔离及补跑情况、两端 `--status` 输出。
