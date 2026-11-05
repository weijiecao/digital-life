# 数字生命独立记忆库（自用版）v1.0.10

[![Release](https://img.shields.io/github/v/release/weijiecao/astrbot_plugin_digital_memory_store?label=release)](https://github.com/weijiecao/astrbot_plugin_digital_memory_store/releases)
[![License](https://img.shields.io/github/license/weijiecao/astrbot_plugin_digital_memory_store)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.29.3%20tested-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.12%20tested-3776AB)](https://www.python.org/)

AstrBot 插件：把**私聊纯文本**里的原话保存在本机，需要时以“未确认线索”的形式回注给模型。
默认关闭；不自动确认事实；除显式开启的可选功能外不联网。

> **兼容性**：AstrBot **4.29.3**（+ Python 3.12）实测通过；4.x 其他版本**未验证**。
> 在别的版本上用之前，请按 [docs/QUICKSTART.md](docs/QUICKSTART.md) 的 4 个检查点自测，别假设它一定兼容。

## 快速开始（约 5 分钟）

下载 → 安装 → 验证 → 使用：

1. 从 [Releases](https://github.com/weijiecao/astrbot_plugin_digital_memory_store/releases)
   下载 `astrbot_plugin_digital_memory_store-<version>.zip`，用同名 `.sha256` 校验：
   `Get-FileHash .\astrbot_plugin_digital_memory_store-1.0.7.zip -Algorithm SHA256`；
2. 解压后进入插件目录，跑一键安装（会问 AstrBot 根目录与白名单 QQ，可选装依赖/模型）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   # 全自动：-AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
   ```

3. 重启 AstrBot，用白名单号私聊 `/dlm 上下文状态`；
4. 想确认“注入真的生效”，逐条走 [docs/QUICKSTART.md](docs/QUICKSTART.md) 的 4 个检查点。

也可以直接在 AstrBot WebUI 的插件页上传 zip 安装（上传后仍需打开 `enabled`、填白名单）。
三条安装路径的差异与全部参数见 [INSTALL.md](INSTALL.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 用户流程：安装 → 4 个验证检查点 → 日常使用 → 更新 → 停用/卸载 → 备份迁移 |
| [INSTALL.md](INSTALL.md) | 三种安装方式细节、配置项、升级与回退、常见问题 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 症状 → 原因 → 处理；提 issue 需要带什么 |
| [CHANGELOG.md](CHANGELOG.md) / [RELEASE_NOTES.md](RELEASE_NOTES.md) | 版本变更与发布说明 |

## 工作方式

1. 白名单账号的私聊文本被采集到本机 SQLite；
2. 自动整理（字面分组、短句变化成组、可选本地语义）生成“待审线索”；
3. 每轮请求前，按“明确事件 → 待审原话/变化链 → 本地语义”依次尝试；**默认即使没有匹配，也会给一段较早原话作背景**（均标未确认），最多 450–600 字；
4. 注入内容始终标注“未确认/可能无关”，模型不得把它当作已确认事实。

## 配置

- 三档预设：`presets/off.json`（装但不启用）、`self.json`（自用）、`debug.json`（检索实验）；
- 关键项：`allowed_sender_ids`（白名单，必填）、`model_dir`（本地 BGE 目录，可选）、
  `source_similarity_percent`（原话语义门槛，默认 60）；
- 可选 LLM 候选筛选器 `model_judge_enabled`：默认关闭，开启后会把问题、最近原话与候选原话
  发送给所选模型提供商，仅用于短追问的候选筛选。

## 控制面板

在 AstrBot WebUI 的插件页打开 **数字生命记忆库** → 视图 **panel**：
存储用量、7 日均量、5 年容量预估、备份列表、磁盘剩余、钩子 P95；
一键备份/轮转/checkpoint/完整性检查/归档导出/VACUUM。

## 常用命令

| 命令 | 作用 |
|---|---|
| `/dlm 上下文状态` | 本轮是否注入、预算、模型状态 |
| `/dlm 记忆状态` | 采集数、事件数、后台任务 |
| `/dlm 采集状态` | 采集开关、缓冲与失败计数 |
| `/dlm 待审` | 未确认的待审原话（分页） |
| `/dlm 事件` | 已整理出的候选事件 |
| `/dlm 查看 标题` | 按标题查看原话与关系 |
| `/dlm 备份` | 立即备份并校验 |
| `/dlm 备份校验 <备份文件名>` | 校验某个已有备份 |

完整命令（含整理、关联、时间线、摘要缓存等）见 `/dlm 帮助`。

## 数据与隐私

- 原话、索引、备份都在 AstrBot 数据目录，**明文**；请自行控制权限与备份清理；
- 每轮写一行本机 `latency.jsonl`（耗时/是否注入），可用 `latency_metrics_enabled=false` 关闭；
- 唯一外发：显式开启的候选筛选器（默认关闭），外发内容见 `INSTALL.md`。

## 已知限制（务必阅读）

- 只支持私聊纯文本；群聊、图片、语音、多设备同步不支持。
- 语义阈值来自**自造语料**，真实聊天准确率未验证；不要承诺“记得住”。
- 指代追问（“后来呢？”）默认接不住；需显式开启候选筛选器（有外发与过拟合风险）。
- 纠错只给“新旧并列 + 未确认”，不会自动改写事实。
- 5,000 条压测与崩溃恢复使用的是合成/受控数据；长期运行仍建议定期备份。

历史开发记录（`PLUGIN_HISTORY.md`）保留在开发工作区，不随本仓库与安装包分发。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
