# 安装 / 升级 / 回退

> 适用：AstrBot 4.x（实测 4.29.3）+ Python 3.10–3.12。本插件是**独立测试/自用插件**，
> 默认关闭、只处理私聊纯文本、不自动确认任何事实。

## 1. 前置

| 项 | 要求 |
|---|---|
| AstrBot | 4.29.3 实测；其他 4.x 未验证 |
| Python | 3.12 实测；需与运行 AstrBot 的解释器一致 |
| 依赖 | 只有语义检索需要 `torch` + `sentence-transformers`，见包内 `requirements.txt` |
| 模型 | 可选，但**没有模型就没有语义召回**；`model_dir` 指向本机 SentenceTransformer 目录（需含 `config.json`） |
| 数据 | 存在 AstrBot 的 `data/plugin_data/astrbot_plugin_digital_memory_store/`，明文 |

## 2. 安装（推荐脚本）

也可以用 `dist/astrbot_plugin_digital_memory_store-<version>.zip`：
解压后把 `astrbot_plugin_digital_memory_store/` 放进 `<root>\data\plugins\`，
再按 `presets/self.json` 生成配置文件；脚本安装更省事。

```powershell
python scripts/install.py `
  --astrbot-root "C:\AstrBot" `
  --preset self `
  --allowed-sender-ids 123456 `
  --model-dir "D:\models\bge-small-zh-v1.5"
```

- `--preset`：`off`（装但不启用）/ `self`（自用，推荐）/ `debug`（打开全部检索实验开关）
- `--allowed-sender-ids`：允许的私聊 QQ 号，逗号分隔；**不填就不会采集任何消息**
- 已安装时脚本会拒绝覆盖；改用 `upgrade.py`，或加 `--force`

脚本做两件事：复制插件到 `<root>\data\plugins\astrbot_plugin_digital_memory_store`；
按 schema 默认值 + 预设生成 `<root>\data\config\astrbot_plugin_digital_memory_store_config.json`。

手工安装等价于：把本目录整体放进 `<root>\data\plugins\`，再按 `presets/self.json` 修改配置文件。

## 3. 重启与自检

1. 重启 AstrBot，日志应出现 `Loading plugin astrbot_plugin_digital_memory_store`；
2. 用白名单账号私聊发送 `/dlm 上下文状态`，应看到"本地模型：就绪/后台加载中/加载失败"；
3. `/dlm 记忆状态` 查看采集与事件统计；
4. 可选：`python scripts/preflight.py --model-dir "<模型目录>"` 提前检查依赖与模型目录；
5. 可选：`python scripts/verify_install.py --astrbot-root "<AstrBotRoot>"` 核对插件、配置、预设与模型目录。
6. 可选：在 AstrBot WebUI 的插件详情页打开 **panel** 视图，查看存储/备份/容量预估并执行维护。

## 4. 升级与回退

```powershell
python scripts/upgrade.py  --astrbot-root "C:\AstrBot"   # 先备份旧版，再覆盖，保留配置
python scripts/rollback.py --astrbot-root "C:\AstrBot"   # 回退到最近一次备份
```

- 备份位置：`<root>\data\plugins_backup\astrbot_plugin_digital_memory_store_<时间戳>`
- 升级**不会**改配置，也不会动数据；如需改档位，重新执行 `install.py --force`
- 回退不会删除当前版本：会先把它移到 `..._rolledback_<时间戳>`

## 5. 数据、隐私与边界

- 所有原话、索引、备份都在本机 AstrBot 数据目录，**明文**；请自行控制目录权限与备份清理。
- 每轮还会写一行本地 `latency.jsonl`（钩子耗时、是否注入，2 MB 自动裁剪），不会上传；
  可用 `latency_metrics_enabled=false` 关闭。
- 默认不调用任何远程模型，也不把原话外发；唯一例外是显式开启的 `model_judge_enabled`
  （会把问题、最近 1–2 条原话与候选原话发给所选模型服务商，默认关闭）。
- 只处理**私聊纯文本**；群聊、图片/语音、多设备同步均为 Non-goals。
- 所有结论保持"未确认"；不自动确认事实、不自动修改关系。

## 6. 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 日志找不到插件 | 目录名不是 `astrbot_plugin_digital_memory_store` | 用脚本安装，别手工改名 |
| `/dlm` 无响应 | 发送者不在白名单 / 不是私聊 / `enabled=false` | 检查配置三项 |
| 没有记忆注入 | 模型目录无效、加载失败或首轮仍在加载 | 看 `/dlm 上下文状态` 的"本地模型" |
| 语义检索整体不可用 | 缺 `torch`/`sentence-transformers` | 在**AstrBot 的解释器**里 `pip install -r requirements.txt` |
| 想彻底停用 | 改 `off` 预设或 `enabled=false` | 已采集数据不会删除 |

## 7. 已知限制（安装前请读）

- 语义阈值默认 0.60（0.55–0.95 可配）；0.55 召回更高，但串味风险也更高。
- 自然改写与“之前那件事”类追问召回弱；后者需显式开启 LLM 候选筛选器（默认关闭）。
- 自动纠错仍弱（自造语料 4/35），不能把它当作“记得改期”来依赖。
- 未做 5,000 条压测、未做崩溃恢复演练；不要直接用于正式机器人。
