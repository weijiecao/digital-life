# 排错手册

> 顺序：先跑第 0 节的三条诊断命令 → 再按症状查表 → 最后按第 8 节准备信息提 issue。
> 安装步骤见 [`QUICKSTART.md`](QUICKSTART.md)，参数细节见 [`../INSTALL.md`](../INSTALL.md)。

## 0. 先跑这三条

把 `C:\AstrBot` 换成你的 AstrBot 根目录。

```powershell
# 1) 插件在不在、名字对不对
Test-Path "C:\AstrBot\data\plugins\astrbot_plugin_digital_memory_store\main.py"

# 2) 配置里的三个关键开关
Get-Content "C:\AstrBot\data\config\astrbot_plugin_digital_memory_store_config.json" -Raw -Encoding UTF8 |
  ConvertFrom-Json | Select-Object enabled, automatic_memory_enabled, allowed_sender_ids, model_dir

# 3) AstrBot 的解释器里有没有语义依赖
C:\AstrBot\.venv\Scripts\python.exe -c "import torch, sentence_transformers; print('torch', torch.__version__)"
```

| 结果 | 含义 |
|---|---|
| 1 返回 `False` | 插件没装进去或目录名被改过 → 重跑 `install.ps1` |
| 2 里 `enabled` 或 `automatic_memory_enabled` 为 `False` | 插件处于停用状态 → 打开后重启 |
| 3 报 `ModuleNotFoundError` | 语义依赖没装（只影响语义召回）→ `install.ps1 -WithDeps` |

## 1. 装不上 / 加载不了

| 症状 | 原因 | 处理 |
|---|---|---|
| 插件列表里没有 | 目录名不是 `astrbot_plugin_digital_memory_store` | 重命名或重跑 `install.ps1`（不要手工改名） |
| 日志有 `Loading plugin` 但页面报错 | 依赖缺失或 `_conf_schema.json` 损坏 | 重新解压覆盖；用 `install.ps1` 安装 |
| `install.ps1` 双击闪退 | 执行策略禁止脚本 | `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1` |
| 脚本提示“这不像 AstrBot 根目录” | 路径指到了 `data` 或 `data\plugins` 里 | 传根目录（含 `main.py` 的那层） |
| 杀毒软件拦截 `install.ps1` | 脚本被误判 | 加白名单，或改用手工解压（QUICKSTART 方式 C） |

## 2. `/dlm` 没反应

按顺序排除：

1. **是不是私聊**：群聊一律不处理（设计如此）；
2. **`enabled` 是否为 `true`**（见第 0 节第 2 条）；
3. **发送者是否在白名单**：`allowed_sender_ids` 必须是纯数字、英文逗号分隔、无空格，例如 `123456,789012`；
4. **唤醒前缀**：AstrBot 若配置了 `wake_prefix`（默认 `/`），消息必须以它开头；
5. **是否重启过 AstrBot**：改配置后不重启不生效。

## 3. 采集不到（计数不涨）

| 症状 | 原因 | 处理 |
|---|---|---|
| `/dlm 记忆状态` 采集数一直 0 | 白名单没写对 / 不是私聊 / `enabled=false` | 见第 2 节 |
| 有 `capture_skipped` 增长 | 单条超过 4000 字上限，或来源被判定为非私聊 | 属预期行为；超长消息会被跳过 |
| 数据目录里没有库文件 | 插件没真正加载 | 查日志是否 `Loading plugin` |

数据位置：`<AstrBot根>\data\plugin_data\astrbot_plugin_digital_memory_store\`（**明文**）。

## 4. 看不到注入（回复里没有 `[DL_MEMORY …]`）

先区分“注入没发生”和“注入了但模型没用”：

| 症状 | 原因 | 处理 |
|---|---|---|
| `/dlm 上下文状态` 显示未注入 | `automatic_memory_enabled=false`，或历史为空 | 打开开关；先积累几条私聊 |
| 显示已注入但回复没提 | 模型选择忽略，属正常 | 插件只给线索，不强制模型复述 |
| 总是显示“背景-only” | 关键词/语义都没命中 | 属预期：默认仍给较早原话作背景 |
| 语义命中率为 0 | 没装依赖或 `model_dir` 不对 | 见第 5 节 |

## 5. 语义检索不可用

日志里会有一条 `[DL_MEMORY 自检]`，按它说的改：

| 日志内容 | 处理 |
|---|---|
| 缺少 Python 依赖：torch、sentence-transformers | 在 **AstrBot 的解释器**里装：`install.ps1 -WithDeps` |
| 模型目录不存在 | 改 `model_dir` 或 `install.ps1 -WithModel` |
| 模型目录不像 SentenceTransformer 目录（缺 config.json） | 目录指错了；需要含 `config.json`/`modules.json`/`model.safetensors` |
| 加载失败（内存/磁盘） | 首次加载约 10–13 秒、占几百 MB 内存；确认磁盘与内存充足 |

自检加载（约 10 秒，能看到维度就说明模型可用）：

```powershell
C:\AstrBot\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer as S; m=S(r'D:\models\bge-small-zh-v1.5'); print(len(m.encode(['测试'])[0]))"
```

期望输出 `512`。

## 6. 面板 / 备份

| 症状 | 原因 | 处理 |
|---|---|---|
| 插件页没有 panel 视图 | 面板需要 AstrBot 的插件页上下文 | 从插件详情页进入；确认插件已激活 |
| 备份按钮报错 | 磁盘空间不足或库被占用 | 看面板里的“磁盘剩余”；先 `/dlm 备份校验 <备份文件名>` |
| VACUUM 失败 | 需要临时空间（约为库大小） | 清理磁盘后重试 |
| 想恢复备份 | 备份是 SQLite 文件 | 停 AstrBot → 用备份文件替换 `data\plugin_data\...\*.sqlite3` → 启动 |

## 7. 性能与数据膨胀

| 现象 | 说明 | 处理 |
|---|---|---|
| 回复变慢 | 钩子 P95 见面板；正常在毫秒级 | 查看是否开了 `model_judge_enabled`（会外发请求） |
| 库越来越大 | 原话 + 索引 + 备份都占空间 | 面板“容量预估”；轮转备份、`VACUUM` |
| 内存持续增长 | 首次加载模型后应稳定 | 重启对比；异常请附面板数值 |

## 8. 提 issue 需要带什么

请附（**不要贴真实聊天内容或 QQ 号**）：

1. AstrBot 版本（WebUI 关于页或 `<AstrBot根>\pyproject.toml`）；
2. 插件版本（`metadata.yaml` 的 `version`）；
3. 第 0 节三条命令的输出；
4. `/dlm 上下文状态` 的完整回复；
5. 相关日志片段（含 `[DL_MEMORY` 前缀的行）。

仓库：<https://github.com/weijiecao/astrbot_plugin_digital_memory_store/issues>
