# 安装 / 升级 / 回退（v1.0.19 静默版）

> 适用：AstrBot 4.29.3（其他 4.x 未验证）+ Python 3.10–3.12 + Windows。
>
> **重要：v1.0.18 起聊天里没有 `/dlm` 命令；v1.0.19 增加升级前数据备份、schema 栅栏和单实例锁。**
> 普通用户看不到记忆系统；管理只在 WebUI panel、配置文件和本地脚本里进行。

## 0. 一键安装

解压 zip，进入 `astrbot_plugin_digital_memory_store\`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会询问：AstrBot 根目录、白名单 QQ、是否装语义依赖/模型。全自动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -ModelDir "D:\models\bge-small-zh-v1.5"
```

没有模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
```

只跑记录/备份/删除，不装语义：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -AstrBotRoot "C:\AstrBot" -QQ 123456 -SkipDeps -SkipModel
```

## 1. 手工安装

1. 解压，把整个 `astrbot_plugin_digital_memory_store\` 放进 `<AstrBot根>\data\plugins\`；
2. 重启 AstrBot；
3. WebUI → 插件 → 数字生命记忆库 → 配置，至少：

```json
{
  "enabled": true,
  "automatic_memory_enabled": true,
  "allowed_sender_ids": "123456",
  "model_dir": "D:\\models\\bge-small-zh-v1.5"
}
```

`model_dir` 可留空：记录/查看/备份/删除照常，语义召回退化为字面/背景。

## 2. 依赖（可选，仅语义）

```powershell
$py = "C:\AstrBot\.venv\Scripts\python.exe"
& $py -m pip install --index-url https://download.pytorch.org/whl/cpu torch
& $py -m pip install -r .\requirements.txt
& $py -c "import torch, sentence_transformers; print(torch.__version__)"
```

模型目录至少要有：`config.json`、`modules.json`、`model.safetensors`、`tokenizer.json`、
`tokenizer_config.json`、`vocab.txt`、`1_Pooling/config.json`。

## 3. 重启后的自检（不通过聊天）

v1.0.18 **没有** `/dlm 上下文状态`、`/dlm 采集状态`。请用：

1. AstrBot 启动日志：`Loading plugin astrbot_plugin_digital_memory_store`；
2. 插件页 **panel**：查看采集数、待审数、备份数、钩子 P95；
3. 发几条普通私聊文本，观察 provider 请求体或 panel 统计是否变化；
4. 若模型被注入，provider 请求体应出现 `[DL_MEMORY_V040]`；不要向用户展示。

## 4. 管理（仅管理员）

### 备份/维护

WebUI → 插件 → 数字生命记忆库 → panel：

- 立即备份；
- 轮转备份；
- WAL checkpoint；
- 完整性检查；
- 归档导出；
- VACUUM。

### 删除

panel API（管理员）：

```http
POST /astrbot_plugin_digital_memory_store/panel/delete
Content-Type: application/json

{"scope":"<scope>","seq":1}
```

清空 scope：

```json
{"scope":"<scope>","all":true}
```

删除会清理原话与派生数据，并 checkpoint + VACUUM；**不会自动删除历史备份文件**。

## 5. 升级与回退

再跑一次 `install.ps1` 即为升级；旧插件目录会移到 `data\plugins_backup\`，配置保留。
v1.0.19 起还会先把整个 `data\plugin_data\astrbot_plugin_digital_memory_store` 复制到
`data\plugins_backup\dlm_data_<时间>`；**不要只备份插件目录**。

升级/热重载时只允许一个实例写同一个数据目录：`.memory.lock` 被占用时，第二个实例会停用记忆，
正常回复不受影响。库的 `schema_version` 比代码新时也会拒绝写入，而不是尝试迁移。

手工回退：

```powershell
# 停 AstrBot
$root = "C:\AstrBot"
Copy-Item "$root\data\plugins\astrbot_plugin_digital_memory_store" `
  "$root\data\plugins\_failed_1.0.18" -Recurse
# 把旧备份目录改回 astrbot_plugin_digital_memory_store
```

数据和配置不会被升级覆盖。

## 6. 隐私与边界

- 默认不调用远程模型，也不把原话外发；
- 只有显式开启 `model_judge_enabled` 时，才会把问题、最近最多 8 条用户消息和候选原话
  发给所选模型服务商；默认关闭；
- 注入头要求模型不要向用户提及记忆系统，但这是提示词约束，不是硬隔离；
- 只处理授权私聊纯文本；群聊、图片/语音不在范围；
- 所有线索保持“未确认”；不自动确认事实。

## 7. 已知限制

- 没有正式事件后，明确事件召回和改期链较弱；
- 纯指代“后来呢/那件事”仍可能失败；
- 删除不清理历史备份；
- 未做 300–500 条真实语料与 3 天 soak；
- 静默版不应再向用户提供任何 `/dlm` 命令说明。
