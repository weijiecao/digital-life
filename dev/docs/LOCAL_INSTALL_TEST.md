# 本地安装测试流程（Windows / AstrBot 4.29.3）

> 执行结果：[LOCAL_INSTALL_TEST_RESULT_2026-11-05.md](LOCAL_INSTALL_TEST_RESULT_2026-11-05.md)（2026-11-05，已通过）。
>
> 目标：在一台本机、隔离环境里验证 **安装包 → 安装器 → 配置 → 真实 AstrBot 加载 → 采集/注入/命令 → 备份/校验/轮转 → 回退**。
> 本文只覆盖“本地安装测试”，不替代真实 QQ 实聊、真实模型质量与 3 天 soak。
>
> **安全边界**：不要拿正在接 QQ/生产模型的 AstrBot 做本流程。用 `testbed/` 或一次性目录；
> `real_chain.py` 会**删除 testbed 的插件数据**（它每次从干净库开始），必须先备份。

---

## 0. 先确认测试对象（很重要）

当前工作树是 **v1.0.18 / 359 项离线回归**（本文的安装包构建于 1.0.16/352 项时点；之后 judge、候选池、近因缓冲、删除/媒体与静默化改动 +7 项，不影响安装流程结论），但：

| 产物 | 时间 | 是否含 2026-11-05 16:12 的备份 P0 修复 |
|---|---|---|
| `dist/astrbot_plugin_digital_memory_store-1.0.16.zip` | 15:07 | **否**（旧包，只适合装/加载冒烟） |
| 当前 `dev/astrbot_plugin_digital_memory_store/` 源码 | 16:12 后 | **是**（`immutable=1` + 轮转过滤 + 2 项回归） |

所以：

- **要验证安装流程本身**：用旧 1.0.16 包也可以，但**不要**拿它做“备份校验→轮转”验收；
- **要验证当前代码（含 P0 修复）**：从工作树重新构建一个测试包：

```powershell
$ws  = 'C:\Users\205\Desktop\数字生命'
$dev = Join-Path $ws 'dev'
$tb  = Join-Path $ws 'testbed'
$py  = Join-Path $tb '.venv\Scripts\python.exe'
$stage = Join-Path $ws 'dist-test'

New-Item -ItemType Directory -Force -Path $stage | Out-Null
Push-Location $dev
& $py scripts\build_package.py --out-dir $stage
$zip = Get-ChildItem "$stage\*.zip" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-FileHash $zip.FullName -Algorithm SHA256
Get-Content "$($zip.FullName).sha256"
Pop-Location
```

> 这个包仍写 `1.0.16`，但 SHA 与 15:07 的旧包不同，只用于测试。
> 如果要发版：先把 `metadata.yaml` 升到 `1.0.17`、更新 CHANGELOG，再 `build_package.py` 输出到 `dist/`，
> 不要用同名不同内容的包覆盖已分发件。

---

## 1. 前置检查

| 项 | 检查命令/标准 |
|---|---|
| AstrBot | 4.29.3（testbed 已就绪）；其他 4.x 未验证 |
| Python | 3.12（`$py --version`） |
| 依赖 | 语义检索需要 torch + sentence-transformers；testbed `.venv` 已装 |
| 模型 | `testbed\models\bge-small-zh-v1.5\config.json` 存在 |
| 端口 | 9101（mock provider）、6199（WebUI）、6201（NapCat WS）空闲 |
| 磁盘 | 至少 1 GB 余量（模型/依赖另算） |
| 进程 | 先确认没有旧的 testbed `serve.py` / `mock_openai.py` 在跑 |

```powershell
& $py --version
Test-Path (Join-Path $tb 'models\bge-small-zh-v1.5\config.json')
Get-NetTCPConnection -State Listen -LocalPort 9101,6199,6201 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess

# 只查看，不自动杀；确认 CommandLine 是 testbed 的 harness 再处理
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'testbed.*harness\\(serve|mock_openai)\.py' } |
  Select-Object ProcessId,CommandLine
```

---

## 2. 测试 A：干净安装到一次性目录（不启动 AstrBot）

目的：验证 **zip 结构、install.ps1、配置生成、verify_install、升级/回退**，不碰真实实例。

### 2.1 解压测试包

```powershell
$root = Join-Path $env:TEMP ('DLM-InstallTest-' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path "$root\astrbot\data","$root\pkg" | Out-Null
Expand-Archive $zip.FullName -DestinationPath "$root\pkg" -Force
$plugin = Get-ChildItem "$root\pkg" -Directory | Select-Object -First 1
$plugin.FullName
```

期望：`...\pkg\astrbot_plugin_digital_memory_store\metadata.yaml` 存在，目录名不能改。

### 2.2 用 `self` 预设安装

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$($plugin.FullName)\install.ps1" `
  -AstrBotRoot "$root\astrbot" -QQ 123456 -Preset self -SkipDeps -SkipModel
```

期望输出包含：插件已复制、配置已写入、下一步重启 AstrBot。

### 2.3 自检

```powershell
& $py scripts\verify_install.py --astrbot-root "$root\astrbot" `
  --json "$root\install_verify.json"
```

期望 JSON：

```json
{
  "ok": true,
  "version": "1.0.16",
  "checks": {
    "plugin_dir": true,
    "files": true,
    "schema_keys": 36,
    "config_file": true,
    "presets": ["off", "self", "debug"],
    "model_dir": null
  },
  "problems": []
}
```

> `schema_keys` 当前是 36；如果配置 schema 变了，以实际 `_conf_schema.json` 的 key 数为准，不把 36 写死。

### 2.4 核对配置

```powershell
$cfg = Get-Content "$root\astrbot\data\config\astrbot_plugin_digital_memory_store_config.json" -Raw |
       ConvertFrom-Json
$cfg.enabled
$cfg.automatic_memory_enabled
$cfg.allowed_sender_ids
$cfg.context_budget_chars
$cfg.source_similarity_percent
```

期望：`True / True / 123456 / 450 / 60`。

### 2.5 验证 `off` 预设

```powershell
New-Item -ItemType Directory -Force -Path "$root\astrbot-off\data" | Out-Null
powershell -NoProfile -ExecutionPolicy Bypass -File "$($plugin.FullName)\install.ps1" `
  -AstrBotRoot "$root\astrbot-off" -QQ 123456 -Preset off -SkipDeps -SkipModel
& $py scripts\verify_install.py --astrbot-root "$root\astrbot-off"
```

期望：`ok=true`；`enabled=false`、`always_context_enabled=false`（装好但不启用）。

### 2.6 升级 / 回退

```powershell
& $py scripts\upgrade.py --astrbot-root "$root\astrbot"
& $py scripts\rollback.py --astrbot-root "$root\astrbot"
& $py scripts\verify_install.py --astrbot-root "$root\astrbot"
```

期望：升级先把旧插件移到 `data\plugins_backup\`；配置不变；回退后 `verify_install` 仍 `ok=true`。

**测试 A 通过标准**：`install.ps1` 无异常、`verify_install.ok=true`、3 个预设可用、
升级备份存在、回退后自检通过。

---

## 3. 测试 B：装进真实 AstrBot（testbed）并跑真实链路

目的：验证 **AstrBot 4.29.3 真正加载插件、钩子注册、采集落库、注入进入 provider 请求、`/dlm` 命令可用**。

### 3.1 先备份 testbed 现场

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$tbRoot = Join-Path $tb 'instance'
$tbPluginData = Join-Path $tbRoot 'data\plugin_data\astrbot_plugin_digital_memory_store'

New-Item -ItemType Directory -Force -Path (Join-Path $tbRoot 'data\plugins_backup') | Out-Null
Copy-Item (Join-Path $tbRoot 'data\plugins\astrbot_plugin_digital_memory_store') `
  (Join-Path $tbRoot "data\plugins_backup\preinstall_$stamp") -Recurse
Copy-Item (Join-Path $tbRoot 'data\config\astrbot_plugin_digital_memory_store_config.json') `
  (Join-Path $tbRoot "data\config\astrbot_plugin_digital_memory_store_config.json.bak-$stamp")
if (Test-Path $tbPluginData) {
  Copy-Item $tbPluginData (Join-Path $tbRoot "data\plugin_data_backup_$stamp") -Recurse
}
```

> `real_chain.py` 第 317 行会 `rmtree(PLUGIN_DATA)`；第 3.4 步会清空当前插件库。
> 没有 3.1 的备份就不要跑。

### 3.2 安装当前测试包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$($plugin.FullName)\install.ps1" `
  -AstrBotRoot $tbRoot -QQ "1508236662" -Preset self `
  -ModelDir (Join-Path $tb 'models\bge-small-zh-v1.5') -SkipDeps -SkipModel
```

说明：

- `install.ps1` 的 `-QQ` 只接受**纯数字**；这里传真实主号，`tester` 由已有配置保留。
  `real_chain.py` 用 `USER='tester'` 发 WebChat 消息，所以**必须确认白名单里仍有 tester**（见 3.3）；
- `1508236662` 是 testbed 里真实 QQ 小号对应的主号（按你当前配置保留）；
- `-SkipDeps`：testbed `.venv` 已有 torch/sentence-transformers；
- `-SkipModel` + `-ModelDir`：用已有 BGE，不重新下载；
- `install.ps1` 会先把旧插件目录移到 `data\plugins_backup\`，保留已有配置（除非 `-Force`）。

### 3.3 安装后自检

```powershell
& $py scripts\verify_install.py --astrbot-root $tbRoot `
  --json (Join-Path $dev "reports\install_verify_testbed_$stamp.json")

# 确认 tester 仍在白名单；若不在，手工补上（保持 UTF-8 无 BOM）
$configPath = Join-Path $tbRoot 'data\config\astrbot_plugin_digital_memory_store_config.json'
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
if (($cfg.allowed_sender_ids -split ',') -notcontains 'tester') {
  $cfg.allowed_sender_ids = (($cfg.allowed_sender_ids, 'tester') -join ',')
  [IO.File]::WriteAllText($configPath, (($cfg | ConvertTo-Json -Depth 10) + "`n"),
    (New-Object System.Text.UTF8Encoding($false)))
}
(Get-Content $configPath -Raw | ConvertFrom-Json).allowed_sender_ids
```

期望：`ok=true`、`version=1.0.16`、`model_dir=true`、`problems=[]`；白名单含 `tester` 与 `1508236662`。

**确认装进去的是当前代码（不是 15:07 的旧包）**：

```powershell
$installed = Join-Path $tbRoot 'data\plugins\astrbot_plugin_digital_memory_store'
Select-String (Join-Path $installed 'recovery.py') -Pattern 'immutable=1'
Select-String (Join-Path $installed 'panel.py') -Pattern 'BACKUP_NAME'
```

两处都应命中；若没有，说明装的是旧包，回到第 0 节重建再装。

### 3.4 启动 mock provider（新开一个 PowerShell 窗口）

```powershell
Set-Location $tb
& $py harness\mock_openai.py --port 9101 --log logs\llm_requests.jsonl
```

保持窗口运行。确认端口：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 9101 |
  Select-Object LocalPort,OwningProcess
```

> `real_chain.py` 固定读 `testbed\logs\llm_requests.jsonl`；如果旧日志很大，先改名备份。

### 3.5 跑真实链路探针

另开一个 PowerShell：

```powershell
Set-Location $tb
& $py harness\real_chain.py --out (Join-Path $dev "reports\real_env_installtest_$stamp.json")
```

它会：启动真实 AstrBot 4.29.3 → 加载 testbed 插件 → 通过 WebChat 队列发种子消息/查询/`/dlm 记忆状态`
→ 记录 mock 收到的完整请求体 → 停止实例。

期望控制台大致输出：

```text
插件：True [...]
采集：1 条 ['我喜欢吃苹果']
注入：1/1 轮 prefix=True
钩子到 provider：0.0x s
命令回复：本会话...
```

### 3.6 核对证据

```powershell
$facts = Get-Content (Join-Path $dev "reports\real_env_installtest_$stamp.json") -Raw | ConvertFrom-Json
$facts.plugin.loaded
$facts.plugin.handlers
$facts.capture_after_seed.rows
$facts.injection_attempts[0].found
$facts.injection_attempts[0].contains_memory_prefix
$facts.hook_to_provider_s
$facts.command.text
$facts.bge.dimension
```

通过标准：

| 字段 | 期望 |
|---|---|
| `plugin.loaded` | `true` |
| `plugin.handlers` | 含 `capture_private`、`add_memory_context`、`dlm` |
| `capture_after_seed.rows` | ≥1 |
| `injection_attempts[0].found` | `true` |
| `contains_memory_prefix` | `true`（请求体含 `[DL_MEMORY_V040]`） |
| `command.text` | 非空，且含“会话/采集/事件”等统计 |
| `bge.dimension` | `512`（使用真实 BGE 时） |

> 没有 BGE/模型时，`injection` 仍可能走字面/背景路径；`bge.dimension` 可能缺失，这不等于安装失败。

### 3.7 只读审计

```powershell
$db = Join-Path $tbRoot 'data\plugin_data\astrbot_plugin_digital_memory_store\manual_test_memory.sqlite3'
& $py scripts\memory_audit.py --db $db
```

期望：`captures=... errors=0`；如果有 `duplicate_content`，只是提示，不是 error。

### 3.8 备份 P0 验收（当前代码）

在 testbed 库上手动做一次“备份 → 校验 → 轮转”：

```powershell
# 用已安装插件的 Store，而不是 dev 副本：确保测的是装进去的代码
$installedParent = Join-Path $tbRoot 'data\plugins'
& $py -c @"
import sys
sys.path.insert(0, r'$installedParent')
from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.panel import rotate_backups

root = r'$tbPluginData'
s = Store(root, '')
try:
    names = []
    for _ in range(3):
        name = s.backup()
        r = s.verify_stored_backup(name)
        assert r['ok'], r['errors']
        names.append(name)
    result = rotate_backups(root, keep=2)
    from pathlib import Path
    files = sorted(p.name for p in (Path(root) / 'backups').iterdir())
    real = [n for n in files if n.endswith('.sqlite3')]
    side = [n for n in files if n.endswith(('-wal', '-shm', '-journal'))]
    print({'real': len(real), 'sidecar': side, 'remaining': result['remaining']})
finally:
    s.close()
"@
```

期望：`{'real': 2, 'sidecar': [], 'remaining': 2}`。若 `real=0` 或 `sidecar` 非空，
说明装的是旧包或修复未生效，回到第 0 节重建。

> 这是对 2026-11-05 P0 修复的现场验收；也可以用 `dev/verification/v12_backup.py` 在 dev 上复跑。

---

## 4. 测试 C：手工冒烟（可选）

如果这是你自己的 AstrBot（不是 testbed），且 WebUI 可用：

1. 重启 AstrBot；日志应出现 `Loading plugin astrbot_plugin_digital_memory_store`；
2. WebUI → 插件 → 数字生命记忆库 → 配置：确认 `enabled=true`、`automatic_memory_enabled=true`、白名单；
3. 用白名单号私聊发送：
   - `/dlm 上下文状态`（看模型/开关/预算）
   - `/dlm 记忆状态`（看采集/事件/任务）
   - `/dlm 备份`（生成备份）
   - `/dlm 备份校验 <备份文件名>`（校验）
4. 插件页 **panel** → 轮转 `keep=2`，确认 `backups` 里仍有 2 个 `.sqlite3`、无 `-wal/-shm`；
5. 发 3–5 条普通私聊，再发一条相关追问；确认回复没有被记忆内容当成指令，且记忆片段带“未确认”。

> testbed 的 dashboard 是关闭的（避免和生产 6185 冲突），所以不要期待 `http://127.0.0.1:6199`
> 一定有 WebChat 页面；testbed 用手工冒烟时优先用 QQ/NapCat，或用 `real_chain.py` 的自动化证据。

---

## 5. 验收记录表

| # | 检查 | 期望 | 实际 | 证据 |
|---|---|---|---|---|
| A1 | zip 结构/版本/SHA | 目录一层、`metadata.yaml`、无 `__pycache__`；SHA 与 `.sha256` 一致 | | `$stage` 包 + `Get-FileHash` |
| A2 | 干净安装 | `install.ps1` 成功；插件目录存在 | | `install_verify.json` |
| A3 | 配置 | `enabled/automatic/白名单/预算` 正确；`schema_keys` 完整 | | `verify_install` |
| A4 | `off` 预设 | `enabled=false`，不采集 | | `verify_install` |
| A5 | 升级/回退 | 旧插件进 `plugins_backup`；配置保留；回退后自检通过 | | `plugins_backup` + `verify_install` |
| B1 | 真实加载 | `plugin.loaded=true`、3 个 handler | | `real_env_installtest_*.json` |
| B2 | 采集 | `capture_after_seed.rows ≥1` | | 同上 |
| B3 | 注入 | `found=true`、`contains_memory_prefix=true` | | 同上 + `logs/llm_requests.jsonl` |
| B4 | 命令 | `/dlm 记忆状态` 返回真实统计 | | 同上 `command.text` |
| B5 | BGE | `dimension=512`（有模型时） | | 同上 `bge` |
| B6 | 审计 | `errors=0` | | `memory_audit` 输出 |
| B7 | 备份 P0 | 3 次备份+校验后 `rotate(2)` → 2 个真实备份、0 旁文件 | | 3.8 脚本输出 |
| C1 | 手工命令 | `/dlm 备份`、`/dlm 备份校验` 正常 | | 聊天记录/截图 |

**判定**：A1–A5、B1–B7 全过 → 本地安装测试通过；C1 可选。
任一 P0（加载失败、注入完全不可见、备份轮转删光）→ 停，保留日志，不继续接 QQ。

**证据目录**：

- `$root\install_verify.json`（测试 A）
- `$dev\reports\install_verify_testbed_$stamp.json`（测试 B 安装自检）
- `$dev\reports\real_env_installtest_$stamp.json`（真实链路）
- `$tb\logs\llm_requests.jsonl`（provider 请求体，含注入前缀）
- `$tbRoot\data\plugin_data\...\manual_test_memory.sqlite3`（采集库）
- `scripts/memory_audit.py --json` 的输出（建议另存 `reports/memory_audit_installtest.json`）

---

## 6. 清理与回退

```powershell
# 1) 停掉 mock provider（确认 PID 是 testbed 的 mock_openai）
Get-NetTCPConnection -State Listen -LocalPort 9101 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Get-Process -Id $_ | Select-Object Id,ProcessName }

# 2) 恢复 testbed 现场
$tbRoot = Join-Path $tb 'instance'
if (Test-Path (Join-Path $tbRoot "data\plugin_data_backup_$stamp")) {
  Remove-Item (Join-Path $tbRoot 'data\plugin_data\astrbot_plugin_digital_memory_store') -Recurse -Force -ErrorAction SilentlyContinue
  Copy-Item (Join-Path $tbRoot "data\plugin_data_backup_$stamp") `
    (Join-Path $tbRoot 'data\plugin_data\astrbot_plugin_digital_memory_store') -Recurse
}
Copy-Item (Join-Path $tbRoot "data\config\astrbot_plugin_digital_memory_store_config.json.bak-$stamp") `
  (Join-Path $tbRoot 'data\config\astrbot_plugin_digital_memory_store_config.json') -Force
# 插件目录回退：用 install.ps1 的 plugins_backup/preinstall_* 覆盖，或留新版本

# 3) 删除一次性测试目录
Remove-Item $root -Recurse -Force
```

> 恢复插件目录前先停 AstrBot，避免 Windows 文件占用；数据目录/配置的恢复顺序：
> 先停进程 → 恢复 `plugin_data` → 恢复 config → 恢复 plugin → 再启动。

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `install.ps1` 说“不像 AstrBot 根” | 目录没有 `data\` / `main.py` / `astrbot` | 用真实 AstrBot 根，或至少先建 `data` |
| `verify_install` 报 `model_dir` 无效 | 路径不是 SentenceTransformer 目录（缺 `config.json`） | 指向 `testbed\models\bge-small-zh-v1.5` 或留空 |
| 插件列表不出现 | 目录名被改、没重启、AstrBot 版本不对 | 目录必须是 `astrbot_plugin_digital_memory_store`；重启；看启动日志 |
| `/dlm` 没反应 | 发送者不在白名单 / 不是私聊 / `enabled=false` | 检查三项；testbed 要把 `tester` 加进白名单 |
| 没有注入 | 首轮 BGE 仍在加载、没有历史、查询 >400 字、上下文开关未开 | 等 10–15 s；先发种子消息；查 `/dlm 上下文状态` |
| `real_chain` 端口冲突 | 旧 `serve.py`/mock 还在跑 | 先停 testbed 进程；确认 9101/6201 空闲 |
| `real_chain` 后数据变少 | 它按设计清空 `plugin_data` | 用 3.1 的备份恢复；这是测试行为，不是安装失败 |
| 备份校验后又出现 `-wal/-shm` | 装的是 15:07 的旧 1.0.16 包 | 用第 0 节工作树重建的测试包；或先修/升级 |
| 轮转后备份变 0 | 同上，旧轮的 P0 | 用当前代码；3.8 验收必须 `real=2, sidecar=[]` |

---

## 8. 这次测试**不能**证明什么

- 不能证明真实 QQ/NapCat 链路（testbed 是 WebChat 事件总线；QQ 需 `docs/QQ_RUNBOOK.md`）；
- 不能证明真实模型回复质量（provider 是本地 mock，只验证请求体）；
- 不能证明 3 天稳定性 / P95（需 `L0_ACCEPTANCE` + soak）；
- 不能证明群聊、图片/语音、多设备（Non-goals）；
- 不能证明真实语料上的 R@1/误注入/纠错（需 V4 真实语料盲评）。

**安装测试通过只代表：装得上、加载得了、采集/注入/命令/备份链路通。**
