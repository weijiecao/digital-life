# v1.0.17 本地安装流程

> 适用：Windows + AstrBot 4.29.3 + Python 3.12（其他 4.x 未验证）。
> 这是**有条件初验版**，适合白名单自用；不建议直接接正式机器人。

## 0. 包信息

| 项 | 值 |
|---|---|
| 文件 | `dist/astrbot_plugin_digital_memory_store-1.0.17.zip` |
| SHA256 | `65203fa8bffa7a037eec719195a886ff2ee0522b5222459b51ef2bae54234cb7` |
| 大小 | 约 0.23 MB / 73 个文件 |
| 版本 | `metadata.yaml` 1.0.17 |

先校验：

```powershell
$zip = "C:\path\to\astrbot_plugin_digital_memory_store-1.0.17.zip"
Get-FileHash $zip -Algorithm SHA256
# 应等于：
# 65203FA8BFFA7A037EEC719195A886FF2EE0522B5222459B51EF2BAE54234CB7
```

## 1. 停服并备份

```powershell
$root = "C:\AstrBot"
# 先停 AstrBot（WebUI 停止，或结束它的进程）

$stamp = Get-Date -Format yyyyMMdd_HHmmss
New-Item -ItemType Directory -Force "$root\data\plugins_backup" | Out-Null
if (Test-Path "$root\data\plugins\astrbot_plugin_digital_memory_store") {
  Copy-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
    "$root\data\plugins_backup\dlm_plugin_$stamp" -Recurse
}
if (Test-Path "$root\data\plugin_data\astrbot_plugin_digital_memory_store") {
  Copy-Item "$root\data\plugin_data\astrbot_plugin_digital_memory_store" `
    "$root\data\plugins_backup\dlm_data_$stamp" -Recurse
}
```

数据在 `data\plugin_data\astrbot_plugin_digital_memory_store\`，是明文 SQLite；备份目录自己保管。

## 2. 解压到临时目录

不要直接覆盖旧插件目录，先解压到临时目录：

```powershell
$stage = "$env:TEMP\dlm-1.0.17"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $zip -DestinationPath $stage -Force
cd "$stage\astrbot_plugin_digital_memory_store"
```

解压后应看到 `metadata.yaml`、`main.py`、`_conf_schema.json`、`install.ps1`、`presets\`。

## 3. 安装

### 方式 A：一键脚本（推荐）

已有 BGE 模型目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" `
  -QQ "123456" `
  -SkipDeps `
  -ModelDir "D:\models\bge-small-zh-v1.5"
```

没有模型、先只跑字面/背景路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ "123456" -SkipDeps -SkipModel
```

让脚本装 CPU 依赖并下载模型（约 92 MB）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ "123456" -WithDeps -WithModel
```

> `-QQ` 是白名单，多个用英文逗号。脚本会保留旧配置，只补关键开关；`-Force` 才会重建配置。

### 方式 B：手工

```powershell
# 旧目录先移走（已在上一步备份过）
Move-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
  "$root\data\plugins\_old_dlm" -Force -ErrorAction SilentlyContinue
Copy-Item "$stage\astrbot_plugin_digital_memory_store" `
  "$root\data\plugins\astrbot_plugin_digital_memory_store" -Recurse
```

然后 WebUI → 插件 → 数字生命记忆库 → 配置，至少改：

```json
{
  "enabled": true,
  "automatic_memory_enabled": true,
  "allowed_sender_ids": "123456",
  "model_dir": "D:\\models\\bge-small-zh-v1.5"
}
```

`model_dir` 可留空：记录/查看/备份/删除照常，语义召回退化为字面/背景。

## 4. 重启与自检

重启 AstrBot，然后用**白名单号私聊**：

```text
/dlm 上下文状态
/dlm 记忆状态
/dlm 采集状态
```

期望：

- 版本相关：日志出现 `astrbot_plugin_digital_memory_store` 加载；
- `上下文状态`：能看到“本地模型：就绪/加载中/未配置”、预算、本轮是否注入；
- `采集状态`：能看到采集开关、缺口、`不支持/跳过的非纯文本` 计数；
- `记忆状态`：采集数、事件数、后台任务。

再发几条普通私聊文本，确认：

```text
/dlm 上下文状态
```

应显示本轮是否注入；注入内容只是“未确认线索”，不是事实。

## 5. 验证删除

```text
/dlm 待审
/dlm 删除 1
/dlm 清空记忆 确认
```

- `删除 <序号>`：删单条原话及派生数据；
- `清空记忆 确认`：清空本会话；必须带“确认”；
- 删除会 checkpoint + VACUUM，但**不会自动删除已有备份文件**；
- 如果只是想停用、不删数据：`enabled=false`。

## 6. 验证备份

```text
/dlm 备份
/dlm 备份校验 <备份文件名>
```

或在插件 panel 里做备份/轮转/完整性检查。备份是单文件 `.sqlite3`，不应有 `-wal/-shm` 旁文件。

## 7. 可选：打开大模型候选筛选

默认关闭。只有确认要外发时才开：

```json
{
  "model_judge_enabled": true,
  "judge_provider_id": "你的 AstrBot provider ID"
}
```

它只在**本地未命中 + 短指代 + 2–5 候选**时调用；会把当前问题、最近最多 8 条用户消息和候选原话
发给所选模型服务商。不要把它理解成“所有轮次都调用”。

## 8. 回退

```powershell
# 停 AstrBot
$root = "C:\AstrBot"
$old = "$root\data\plugins\_old_dlm"   # 或 plugins_backup\dlm_plugin_<stamp>
if (Test-Path $old) {
  Move-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
    "$root\data\plugins\_failed_1.0.17" -Force
  Move-Item $old "$root\data\plugins\astrbot_plugin_digital_memory_store"
}
# 重启 AstrBot
```

数据和配置不会被升级覆盖；回退后仍用原数据目录。

## 9. 已知限制（安装前读）

- 这是有条件初验版：RET-01 明确事件召回 10/20、RET-04 相似事件串味 3/15；
- 无 300–500 条带标注真实语料、无 3 天 soak；
- 纯指代“后来呢/那件事”仍可能失败；
- 删除不自动清理历史备份；
- 只处理授权私聊纯文本；群聊、图片/语音不在范围；
- 默认关闭远程模型；开启 judge 会外发最近消息与候选，必须自己确认隐私边界。
