# 配置预设

安装脚本 `dev/scripts/install.py --preset <名字>` 会以 `_conf_schema.json` 的默认值为底，
再叠加对应预设，最后写入 `<AstrBotRoot>/data/config/astrbot_plugin_digital_memory_store_config.json`。

| 预设 | 用途 | 关键开关 |
|---|---|---|
| `off` | 已安装但不工作 | `enabled=false`，其余全部默认关闭 |
| `self` | **自用档（推荐）** | 开启自动采集/整理/召回；实验性排序与模型草稿关闭 |
| `debug` | 调试检索实验 | 在 `self` 基础上打开全部检索实验开关（弱联想、语义原话、双事件、摘要优先等） |

`self` 与 `debug` 默认开启 `always_context_enabled`（每轮给有界背景；关闭后只在命中时注入）。

三个预设都**不开** `model_judge_enabled`（会把原话发给模型服务商）与 `auto_model_enabled`（需要额外配置提供商）。

安装时还需要两个必填值（不写进预设文件）：

- `--allowed-sender-ids`：允许的私聊 QQ 号，逗号分隔；
- `--model-dir`：本机 SentenceTransformer 模型目录（例如 `bge-small-zh-v1.5`）。
