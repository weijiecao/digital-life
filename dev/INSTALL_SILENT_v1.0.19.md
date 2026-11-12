# v1.0.19 静默版安装流程

> 适用：已有 AstrBot 机器人，新增/升级数字记忆基座。
> **用户聊天面完全静默**：没有 `/dlm`，没有待审/关联/删除/备份/状态命令。
> **v1.0.19 数据安全层**：升级前会备份 `plugin_data`；schema 比代码新时拒绝写入；
> 启动 `quick_check`；单实例锁；旧库迁移前自动备份。

## 1. 包

- `dist/astrbot_plugin_digital_memory_store-1.0.19.zip`
- `dist/astrbot_plugin_digital_memory_store-1.0.19.zip.sha256`
- 一键环境脚本：`dist/configure_env_1.0.19.ps1`

先校验：

```powershell
$zip = "C:\path\to\astrbot_plugin_digital_memory_store-1.0.19.zip"
Get-FileHash $zip -Algorithm SHA256
# 对照同名 .sha256
```

## 2. 停服并备份

```powershell
$root = "C:\AstrBot"
# 先停 AstrBot
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

## 3. 安装

```powershell
$stage = "$env:TEMP\dlm-1.0.19"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $zip -DestinationPath $stage -Force
cd "$stage\astrbot_plugin_digital_memory_store"

powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot $root `
  -QQ "123456" `
  -WithDeps `
  -ModelDir "D:\models\bge-small-zh-v1.5"
```

没有模型：把 `-ModelDir ...` 换成 `-WithModel`。
不装语义：`-SkipDeps -SkipModel`。

## 4. 配置

WebUI → 插件 → 数字记忆基座 → 配置：

```json
{
  "enabled": true,
  "automatic_memory_enabled": true,
  "allowed_sender_ids": "123456",
  "model_dir": "D:\\models\\bge-small-zh-v1.5"
}
```

不要配置任何“用户命令开关”；v1.0.19 没有聊天命令。

## 5. 重启与自检（不通过聊天）

1. 启动日志出现 `astrbot_plugin_digital_memory_store`；
2. WebUI → 插件 → panel：能看到采集数、待审数、备份数、P95；
3. 发几条普通私聊文本；
4. 看 provider 请求体是否出现 `[DL_MEMORY_V040]`；
5. **不要**回复用户“我查了记忆/根据记忆”；注入头已要求模型不提，但仍需观察。

## 6. 管理员操作

### 备份/维护

panel：立即备份、轮转、checkpoint、完整性、归档、VACUUM。

### 删除

管理员 API：

```http
POST /astrbot_plugin_digital_memory_store/panel/delete
Content-Type: application/json

{"scope":"<scope>","seq":1}
```

清空 scope：

```json
{"scope":"<scope>","all":true}
```

删除会清原话和派生数据，并 checkpoint + VACUUM；**不会自动清理历史备份**。

## 7. 回退

停服后，把当前插件目录移走，把 `plugins_backup\dlm_plugin_<stamp>` 改回
`astrbot_plugin_digital_memory_store`，重启。

数据目录不变，回退后仍读原数据。

## 8. 注意

- 这是有条件初验版，不是正式发布验收；
- 不新建正式事件，事件级召回较弱；
- 纯指代“后来呢/那件事”仍可能失败；
- 删除不清理旧备份；
- 静默是提示词层约束，不能保证模型 100% 不提“你之前说过”。
