# task-014 / task-015：Windows 本机执行手册（Mac 手册的补充）

准备日期：2026-10-14。协议：`sj4-extension-v1`。本手册是
[reports/sj4-task014-015-mac-runbook.md](sj4-task014-015-mac-runbook.md) 的 Windows
补充：协议、条件、重复数、资源额度、预检与续跑逻辑两份手册一致，本文只写差异。
以下路径以 `E:\zcode` 安装 ZCode、仓库在 `E:\git-hub\vista-mac\visat-for-skill`
为例；换成实际路径即可。命令示例用 **cmd.exe**（不是 PowerShell）。

## 0. 本机已核实的事实（2026-10-14）

| 项目 | 结果 |
|---|---|
| Python | `py` = 3.12.2，满足 3.11/3.12 要求 ✓ |
| ZCode launcher | `E:\zcode\ZCode.exe` + `E:\zcode\resources\glm\zcode.cjs`，版本探针返回 **0.16.9**，与 `EXPECTED_VERSION` 一致 ✓ |
| 宿主默认 provider 配置 | `~/.zcode/v2/provider_config.json`：providerOrder 以 `new-provider` 开头、providerRules 首项 `new-provider`、modelOrder 以 `glm53-flash` 开头 ✓ |
| new-provider-2 配置 | `~/.zcode/v2/provider_config_v3_corrected.json` **缺失** ✗ |
| LibreOffice | 已安装：`C:\Program Files\LibreOffice\program\soffice.exe` ✓ |
| 全局 Python 依赖 | numpy 2.5.3 / scipy 1.18.1（与固定版本 1.26.4 / 1.13.1 不符），**pytest 缺失** ✗ → 必须建 venv |
| git | `core.autocrlf=true`：已加 `.gitattributes`（`experiments/artifacts/** -text`、`experiments/benchmarks/** -text`）防 CRLF 破坏 construction-evidence 哈希 |

关键结论：**task-014 可以在 Windows 跑；task-015 建议仍按 Mac 手册执行**（见第 5 节）。

## 1. 与 Mac 的差异总览

1. pair launcher（`run_sj4_extension_pair.py`）是 macOS-only，Windows 上手动开两个
   终端分别跑 `run_sj4_zcode_group.py`（或顺序跑）。
2. ZCode backend 的默认路径是 macOS 的（`/Applications/ZCode.app/...`），Windows 必须
   用环境变量覆盖 `VISTA_ZCODE_SCRIPT` / `VISTA_ZCODE_BINARY`；`freeze_launcher()` 会
   校验传入路径与冻结路径一致，探针版本必须等于 0.16.9。
3. 结果目录默认 `~/Library/Caches/VistaExperiments`（macOS 风格），Windows 必须设
   `VISTA_EXPERIMENT_ROOT`。
4. runner 找 soffice 用 `shutil.which("soffice")`；LibreOffice 安装目录默认不在 PATH，
   需要手动加（第 5 节）。
5. task-015 的 `recalc.py` 在 Windows 有两处断裂（第 5 节），不建议在 Windows 跑。

## 2. 建 venv（全局依赖版本不符，必须独立环境）

```bat
cd /d E:\git-hub\vista-mac\visat-for-skill
git pull --ff-only
py -m venv .venv-sj4ext
set PIP_CACHE_DIR=E:\pip-cache
set TMP=E:\pip-tmp
set TEMP=E:\pip-tmp
.venv-sj4ext\Scripts\python -m pip install -r experiments\formal\requirements-sj4-extension.txt
.venv-sj4ext\Scripts\python -m pip install -e .
```

- Windows 布局是 `.venv-sj4ext\Scripts\python.exe`（不是 Mac 的 `bin/`）。
- `pip install -e .` 把 `vista` 包（src-layout）装进 venv；否则 runner 里的
  `from vista.runtime.zcode_local_backend import ...` 会 ImportError
  （`sj4_extension_tasks` 等同目录模块会自动导入，`vista` 不会）。
- `PIP_CACHE_DIR` / `TMP` / `TEMP` 指到 E 盘：pip 缓存和临时文件默认走
  `%LOCALAPPDATA%`（C 盘），不想占 C 盘就改到 E 盘。固定版本 numpy 1.26.4 /
  scipy 1.13.1 在 Windows 3.12 上有预编译 wheel，无需编译。
- 2026-10-14 实测：以上安装一次通过，版本精确命中固定版本。

## 3. 每个终端都要设的环境变量

runner 和 backend 读取的关键变量（cmd 语法；持久化可用 `setx`）：

```bat
set VISTA_VERIFIER_PYTHON=%CD%\.venv-sj4ext\Scripts\python.exe
set VISTA_ZCODE_SCRIPT=E:\zcode\resources\glm\zcode.cjs
set VISTA_ZCODE_BINARY=E:\zcode\ZCode.exe
set VISTA_EXPERIMENT_ROOT=E:\vista-experiments
```

- `VISTA_VERIFIER_PYTHON`：默认值 `.venv/bin/python` 在 Windows 不存在，必须显式设。
  它同时让 agent 工具优先使用这个环境。
- `VISTA_ZCODE_SCRIPT` / `VISTA_ZCODE_BINARY`：backend 默认是 Mac 路径，不设会在
  `freeze_launcher()` 的 "frozen ZCode electron binary or script is missing" 处失败。
  设好后与冻结路径一致，版本 0.16.9 已核实。
- `VISTA_EXPERIMENT_ROOT`：不设会写到 `~/Library/Caches/...`（在 Windows 上会真的
  建出这个字面目录）。runner 会在其下放工作目录、资源守卫和 LibreOffice profile；
  trial 证据本身仍写在仓库 `data/raw/<前缀>` 下（E 盘）。不想占 C 盘就设 E 盘路径。
- 其余（`VISTA_RESOURCE_GUARD_ROOT`、`VISTA_LO_PROFILE`、`VISTA_SOLVER_LOG` 等）由
  runner 内部按 trial 生成，不需要手动设。
- 不需要设 `PYTHONPATH`：第 2 节的 `pip install -e .` 已解决包导入（runner；
  pytest 见第 4 节）。

## 4. 预检（不调用模型）与单元测试

选一个批次标签并在本次运行、查询、续跑中始终用同一个（下面用 `20261014-2130` 举例）。

```bat
set PYTHONPATH=%CD%\experiments\formal
.venv-sj4ext\Scripts\python -m pytest tests\test_sj4_extension.py -q --basetemp=E:\pip-tmp\pytest
%VISTA_VERIFIER_PYTHON% experiments\formal\build_sj4_extension_artifacts.py --check

set VISTA_RUN_PREFIX=formal-task-014-sj4ext-six-x5-glm53-20261014-2130
%VISTA_VERIFIER_PYTHON% experiments\formal\run_sj4_zcode_group.py --task task-014 ^
  --provider new-provider --conditions noskill flat pd pd-m noskill-multi vista ^
  --replicates 5 --workers 4 --dry-run
```

- 第一条跑 9 项单元测试（多回合控制、反馈不泄真值、slot 跨进程序列化、
  工件证据与打包文件一致等），pytest 共 12 个用例。测试模块从
  `experiments\formal` 导入 runner，需要 `PYTHONPATH`（conftest 只加了 src）。
  pytest 的临时目录默认在 C 盘 `%TEMP%`，本机实测会被拒（WinError 5），
  用 `--basetemp` 指到 E 盘。
- 第二条核对源快照与工件 hash，不重建、不调模型；应输出
  `task-014: source/artifact hashes OK`（和 `task-015: ... OK`）。
  **若 FAILED：先确认 `.gitattributes` 生效、重新 `git pull` 拿到 LF 原文；
  不要重新构造工件绕过 hash 检查。**
- 第三条 dry-run 依次通过 provider/model gate（host 默认配置 → `new-provider` +
  `glm53-flash`）、launcher gate（0.16.9）、依赖与资源 hook 检查，输出
  `"ready": true` 才进正式运行。资源 hook 只解一个单变量小问题，不调用模型。
  2026-10-14 实测三项全部通过。
- 注意：`--conditions` 传全部 6 个时结果目录前缀直接用 `VISTA_RUN_PREFIX`（与本例
  一致）；只传部分条件时前缀会变成 `custom` 后缀，续跑时必须保持同一组条件。

## 5. 正式运行

### task-014（可在 Windows 跑）

```bat
set VISTA_RUN_PREFIX=formal-task-014-sj4ext-six-x5-glm53-20261014-2130
%VISTA_VERIFIER_PYTHON% experiments\formal\run_sj4_zcode_group.py --task task-014 ^
  --provider new-provider --conditions noskill flat pd pd-m noskill-multi vista ^
  --replicates 5 --workers 4
```

Windows 机器建议从 **4 个 worker** 起步（Mac 手册的 10 是按两端点、大内存估的）。
内存压力持续黄/红时 Ctrl+C，会保留已完成 trial；用同一 `VISTA_RUN_PREFIX` 与
`--replicates` 以更低 `--workers`（如 3）重跑即可续跑，已有 `result.json` 会核对
身份后跳过，中断 trial 归档进 `_interrupted`。**不要同时启动两个使用相同前缀的
runner。** 隔离处理（`_quarantine`）与 Mac 手册第 6 节一致。

### task-015（建议 Mac；在 Windows 有两处断裂）

已核实的断裂：

1. **宏路径**：`recalc.py` 的 `setup_libreoffice_macro()` 非 Darwin 分支写
   `~/.config/libreoffice/4/user/...`（Linux 布局），而 Windows LibreOffice 实际读
   `%APPDATA%\LibreOffice\4\user`。宏写进 Linux 路径后 soffice 读不到，
   `RecalculateAndSave` 不会生效。
2. **guard 的 soffice shim 无效**：`sj4_guard_site/soffice` 是无扩展名 POSIX 脚本，
   Windows 的 `CreateProcess` 不会选中它（会落到真 `soffice.exe`），profile 隔离
   （`VISTA_LO_PROFILE`）和 profile 隔离下的 `soffice` 调用在 Windows 不生效。

也就是说 task-015 的 LibreOffice 公式重算路径在 Windows 不可用，六个条件会**同等**
受影响。若坚持在 Windows 跑 task-015：公式重算失败必须单列记录，不能混入能力对比；
结果只用于相对比较并明确注明平台。正常做法是按 Mac 手册在 Mac 跑 task-015。

若 Windows 上还有验证器之外的用途需要 soffice 进 PATH（runner 的
`shutil.which("soffice")` 探测）：

```bat
set PATH=%PATH%;C:\Program Files\LibreOffice\program
```

task-014 不需要 LibreOffice，跳过这条。

## 6. new-provider-2 的配置缺失问题

`--provider new-provider-2`（task-015 的指派端点）要求
`~/.zcode/v2/provider_config_v3_corrected.json` 存在，本机缺失。两个选择：

1. **推荐**：从 Mac 复制 `%USERPROFILE%\.zcode\v2\provider_config_v3_corrected.json`
   （若其中引用凭据文件，一并复制）。runner 会把它同时设为
   `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` 和 `VISTA_ZCODE_PROVIDER_CONFIG`。
2. 或在 Windows 上把 task-015 也跑在 `new-provider` 上（`--provider new-provider`），
   但这改变了 Mac 手册的端点指派（task-014→new-provider，task-015→new-provider-2），
   结果分析时要注明。

不要手写一个新的 v3 配置来绕过；端点身份是实验的一部分。

## 7. CRLF 与哈希（本仓库特有风险）

本机 `core.autocrlf=true`。`construction-evidence.json` 与
`build_sj4_extension_artifacts.py --check` 的比对是字节级 SHA256：SKILL.md 一旦被
checkout 成 CRLF，全部 hash 检查失败。已随本手册提交 `.gitattributes`：

```text
experiments/artifacts/** -text
experiments/benchmarks/** -text
```

`-text` 关闭这两个目录的行尾转换，checkout 保持仓库里的 LF 原文。已检出的工作区
文件当前是 LF（hash 校验通过）；如果将来某次 `git checkout` 后 `--check` 报 FAILED，
删掉工作区中这两个目录的文件重新 checkout 即可恢复 LF，**不要改工件内容**。

## 8. 跑完提交

与 Mac 手册第 9 节一致：`--status` 确认每任务 30 份结果、检查隔离与补跑，检查
`model-io.jsonl` 不含凭据，只提交两个组目录（在 `VISTA_EXPERIMENT_ROOT` 下的
`data/raw/<前缀>`；若在仓库外，先拷回仓库 `data/raw/` 再 add）。提交信息注明平台
与 worker 数变化，例如：

```bat
git add data/raw/formal-task-014-sj4ext-six-x5-glm53-20261014-2130
git commit -m "Run task-014 six-condition sj4 extension x5 on Windows (workers 4)"
git push
```

回传：组目录名、是否降过并发、隔离及补跑情况、`--status` 输出。
task-015 若在 Mac 跑，按 Mac 手册第 9 节单独提交。
