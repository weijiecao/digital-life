# 已有 AstrBot 机器人：一键配置环境（v1.0.17）

> 适用：已经有一个能正常运行的 AstrBot（4.29.3），已有其他插件；现在只新增/升级
> `astrbot_plugin_digital_memory_store`，不重装 AstrBot、不动其他插件。
>
> 目标：把“插件文件 + Python 语义依赖 + BGE 模型 + 插件配置”一次配好。
>
> **一键文件**：`dist\configure_env_1.0.17.ps1`（只改插件配置/依赖/模型，不重装 AstrBot）。
>
> ```powershell
> powershell -NoProfile -ExecutionPolicy Bypass -File .\configure_env_1.0.17.ps1 `
>   -AstrBotRoot "C:\AstrBot" -QQ "123456" `
>   -WithDeps -ModelDir "D:\models\bge-small-zh-v1.5"
> ```

## 0. 先确认 4 个路径

```powershell
$root = "C:\AstrBot"          # AstrBot 根目录
$py   = "$root\.venv\Scripts\python.exe"
$zip  = "C:\path\to\astrbot_plugin_digital_memory_store-1.0.17.zip"
$model = "D:\models\bge-small-zh-v1.5"   # 已有模型就填，没有可留空
```

检查：

```powershell
Test-Path "$root\data\plugins"
Test-Path $py
& $py --version
```

- 必须用**运行 AstrBot 的那个 Python**，不是系统 Python；
- 如果 `$py` 不存在，先找 AstrBot 实际使用的解释器（`venv\Scripts\python.exe` 或 `python\python.exe`）。

## 1. 停服并备份（只备份这个插件）

```powershell
# 先停 AstrBot
$stamp = Get-Date -Format yyyyMMdd_HHmmss
$bk = "$root\data\plugins_backup"
New-Item -ItemType Directory -Force $bk | Out-Null

if (Test-Path "$root\data\plugins\astrbot_plugin_digital_memory_store") {
  Copy-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
    "$bk\dlm_plugin_$stamp" -Recurse
}
if (Test-Path "$root\data\plugin_data\astrbot_plugin_digital_memory_store") {
  Copy-Item "$root\data\plugin_data\astrbot_plugin_digital_memory_store" `
    "$bk\dlm_data_$stamp" -Recurse
}
```

其他插件不需要备份；不要动 `data\plugins` 下别的目录。

## 2. 解压新包

```powershell
$stage = "$env:TEMP\dlm-1.0.17"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $zip -DestinationPath $stage -Force
cd "$stage\astrbot_plugin_digital_memory_store"
```

校验版本：

```powershell
Select-String .\metadata.yaml -Pattern '^version:'
# version: 1.0.17
```

## 3. 一键安装（推荐）

### 已有 BGE 模型

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot $root `
  -QQ "123456" `
  -WithDeps `
  -ModelDir $model
```

### 没有模型，让脚本下载

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot $root `
  -QQ "123456" `
  -WithDeps `
  -WithModel
```

### 先不装语义，只保证记录/查看/备份/删除

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot $root `
  -QQ "123456" `
  -SkipDeps `
  -SkipModel
```

脚本会：

1. 把旧插件目录移到 `data\plugins_backup\`；
2. 复制 1.0.17 到 `data\plugins\astrbot_plugin_digital_memory_store`；
3. 保留旧配置，只补 `enabled` / `automatic_memory_enabled` / 白名单；
4. `-WithDeps` 时把 **CPU 版 torch + sentence-transformers** 装进 `$py`；
5. `-ModelDir` 时回填 `model_dir`；`-WithModel` 时从 ModelScope 下载。

参数含义：

| 参数 | 作用 |
|---|---|
| `-AstrBotRoot` | AstrBot 根目录 |
| `-QQ` | 白名单 QQ，多个用英文逗号 |
| `-WithDeps` / `-SkipDeps` | 装 / 不装语义依赖 |
| `-ModelDir` | 指向已有 BGE 目录，跳过下载 |
| `-WithModel` / `-SkipModel` | 下载 / 不下载模型 |
| `-Force` | 重建配置；一般不要加，会覆盖已有配置 |

## 4. 手工配置（脚本失败时）

### 4.1 复制插件

```powershell
if (Test-Path "$root\data\plugins\astrbot_plugin_digital_memory_store") {
  Move-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
    "$root\data\plugins\_old_dlm" -Force
}
Copy-Item "$stage\astrbot_plugin_digital_memory_store" `
  "$root\data\plugins\astrbot_plugin_digital_memory_store" -Recurse
```

### 4.2 装依赖（只影响语义召回）

```powershell
& $py -m pip install --index-url https://download.pytorch.org/whl/cpu torch
& $py -m pip install -r "$root\data\plugins\astrbot_plugin_digital_memory_store\requirements.txt"
```

验证：

```powershell
& $py -c "import torch, sentence_transformers; print(torch.__version__)"
```

如果机器已有 CUDA 版 torch，不要重复装 CPU 版；能 `import` 即可。

### 4.3 准备模型

目录至少要有：

```text
config.json
modules.json
model.safetensors
tokenizer.json
tokenizer_config.json
vocab.txt
1_Pooling\config.json
```

没有模型时：记录/查看/备份/删除仍可用，语义召回退化为字面/背景路径。

### 4.4 改配置

WebUI → 插件 → 数字生命记忆库 → 配置，至少：

```json
{
  "enabled": true,
  "automatic_memory_enabled": true,
  "allowed_sender_ids": "123456",
  "model_dir": "D:\\models\\bge-small-zh-v1.5"
}
```

或直接改：

```text
$root\data\config\astrbot_plugin_digital_memory_store_config.json
```

不要删其他插件的配置；只改这个文件。

## 5. 重启与自检

重启 AstrBot，用**白名单号私聊**：

```text
/dlm 上下文状态
/dlm 记忆状态
/dlm 采集状态
```

期望：

- `上下文状态`：本地模型“就绪/加载中/未配置”，预算、本轮是否注入；
- `采集状态`：采集开关、缺口、`不支持/跳过的非纯文本` 计数；
- `记忆状态`：采集数、事件数、后台任务。

再发几条普通私聊文本：

```text
/dlm 上下文状态
```

应看到本轮是否注入；注入内容只是“未确认线索”，不是事实。

## 6. 验证删除与备份

```text
/dlm 待审
/dlm 删除 1
/dlm 清空记忆 确认
/dlm 备份
```

- 删除会清理原话及派生数据；
- **不会自动删除历史备份文件**；
- 只想停用不删数据：`enabled=false`。

## 7. 可选：打开大模型候选筛选

默认关闭。确认隐私边界后再开：

```json
{
  "model_judge_enabled": true,
  "judge_provider_id": "你的 provider ID"
}
```

只在本地未命中 + 短指代 + 2–5 候选时调用；会发送当前问题、最近最多 8 条用户消息和候选原话。

## 8. 回退

```powershell
# 停 AstrBot
$old = "$root\data\plugins\_old_dlm"   # 或 plugins_backup\dlm_plugin_<stamp>
Move-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
  "$root\data\plugins\_failed_1.0.17" -Force
Move-Item $old "$root\data\plugins\astrbot_plugin_digital_memory_store"
# 重启
```

数据目录不变，回退后仍读原数据。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| `import torch` 失败 | 确认用的是 `$root\.venv\Scripts\python.exe` |
| 模型一直“加载中” | 首次加载 10–13 s；看日志，不要重复重启 |
| `上下文状态` 显示未配置模型 | `model_dir` 路径不存在或缺 `config.json` |
| 插件装了但没反应 | `enabled=false`、白名单为空、或不是私聊 |
| `install.ps1` 闪退 | 用 `powershell -NoProfile -ExecutionPolicy Bypass -File ...` |
| 想保留旧配置 | 不要加 `-Force` |
