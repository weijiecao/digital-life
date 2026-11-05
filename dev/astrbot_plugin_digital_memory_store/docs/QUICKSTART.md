# 快速开始：从下载到日常使用

> 目标：5 分钟内让插件在你的 AstrBot 上真正跑起来。每一步都有“通过标准”，不通过就按最后一节排查。
> 详细参数见 [`../INSTALL.md`](../INSTALL.md)，装完出问题见 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

整体流程：

```
下载 zip → 安装（三选一）→ 验证 4 个检查点 → 日常使用 → 更新 / 停用
```

## 0. 准备

| 需要 | 说明 |
|---|---|
| AstrBot | 4.29.3 实测通过；其他 4.x 未验证 |
| AstrBot 根目录 | 就是含 `data\plugins` 或 `main.py` 的那个目录，例如 `C:\AstrBot` |
| 你的 QQ 号 | 插件**只采集白名单里的私聊**，群聊一律不处理 |
| Python | 与运行 AstrBot 的同一个解释器（依赖要装进它里面） |

## 1. 安装（三选一）

### 方式 A：一键脚本（推荐，Windows）

解压 zip，进入 `astrbot_plugin_digital_memory_store\`，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会问三件事：AstrBot 根目录、白名单 QQ、要不要现在装语义依赖/模型。它完成：

1. 复制插件到 `<AstrBot根>\data\plugins\`（已有旧版先备份到 `data\plugins_backup\`）；
2. 写配置：`enabled=true`、`automatic_memory_enabled=true`、白名单；
3. 可选：把 torch **CPU 版** + sentence-transformers 装进 AstrBot 的 `.venv`；
4. 可选：从 ModelScope 下 `bge-small-zh-v1.5`（约 92 MB）并回填 `model_dir`。

全自动（远程/重装）：

```powershell
.\install.ps1 -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
```

### 方式 B：WebUI 上传 zip

AstrBot 4.x 插件页 → 安装插件 → 上传 zip。包结构能被 AstrBot 自带的校验识别。
**注意**：AstrBot 自动装依赖时会按 `requirements.txt` 拉 **CUDA 版 torch**（约 2.5 GB），想省空间用方式 A。

上传后仍需在插件配置里打开 `enabled`、`automatic_memory_enabled` 并填 `allowed_sender_ids`。

### 方式 C：手工解压

```powershell
Expand-Archive .\astrbot_plugin_digital_memory_store-1.0.7.zip -DestinationPath "$env:TEMP\dlm"
Copy-Item "$env:TEMP\dlm\astrbot_plugin_digital_memory_store" "C:\AstrBot\data\plugins\" -Recurse -Force
```

然后重启 AstrBot，在 WebUI 插件配置里改三项：

| 配置项 | 值 |
|---|---|
| `enabled` | `true` |
| `automatic_memory_enabled` | `true` |
| `allowed_sender_ids` | `123456`（多个用英文逗号） |

## 2. 验证：4 个检查点

装完**先重启 AstrBot**，然后逐条确认。全部通过才算装好。

| # | 检查 | 通过标准 | 不通过 |
|---|---|---|---|
| 1 | 插件被加载 | 日志出现 `Loading plugin astrbot_plugin_digital_memory_store`；插件列表有「数字生命独立记忆库（自用版）」 | 看目录名是否为 `astrbot_plugin_digital_memory_store` |
| 2 | 命令通道 | 白名单号私聊 `/dlm 上下文状态` 有回复，显示“本地模型：就绪 / 加载中 / 未配置” | 检查 `enabled`、是否私聊、是否在白名单 |
| 3 | 采集生效 | 再发一条普通消息，然后 `/dlm 记忆状态`，采集计数 +1 | 检查白名单格式（纯数字、英文逗号、无空格） |
| 4 | 注入生效 | 换一句与历史相关的话，回复里出现“[DL_MEMORY …] 未确认”片段（或 WebUI 日志可见） | 没有模型时注入退化为字面/背景路径，见下一步 |

**语义召回（可选，但推荐）**：

```powershell
# 用 AstrBot 自己的解释器检查
C:\AstrBot\.venv\Scripts\python.exe -c "import torch, sentence_transformers; print(torch.__version__)"
```

没装依赖或没配模型时，日志会打印一条 `[DL_MEMORY 自检]` 说明缺什么；**记录、查看、备份照常**，
只是语义召回关闭。补装：

```powershell
.\install.ps1 -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
```

## 3. 日常使用

| 命令 | 作用 |
|---|---|
| `/dlm 上下文状态` | 本轮是否注入、预算、模型状态（最常用） |
| `/dlm 记忆状态` | 采集数、事件数、后台任务、死信 |
| `/dlm 采集状态` | 采集开关、缓冲与失败计数 |
| `/dlm 待审` | 未确认的待审原话（分页） |
| `/dlm 备份` | 立即备份并校验 |
| `/dlm 备份校验 <备份文件名>` | 校验某个已有备份（不带文件名只会提示用法） |
| `/dlm 帮助` | 常用命令总览 |

WebUI 插件页 → **panel** 视图：存储用量、7 日均量、5 年容量预估、备份/轮转/checkpoint/完整性检查/
归档导出/VACUUM。

## 4. 更新到新版本

再跑一次 `install.ps1` 即为升级：旧目录自动移到 `data\plugins_backup\`，配置保留、只补关键开关。
手工方式：先复制备份目录，再用新 zip 覆盖 `data\plugins\astrbot_plugin_digital_memory_store`。
升级不改配置、不动数据。

## 5. 停用与卸载

| 想做的事 | 做法 |
|---|---|
| 暂时停用 | 配置里 `enabled=false`（或 `-Preset off` 重装）；数据保留 |
| 彻底卸载 | 停 AstrBot → 删 `data\plugins\astrbot_plugin_digital_memory_store` |
| 连同数据一起删 | 再删 `data\plugin_data\astrbot_plugin_digital_memory_store\`（**原话在这里，不可恢复**） |
| 只删配置 | 删 `data\config\astrbot_plugin_digital_memory_store_config.json` |

## 6. 备份与迁移

- 数据全在 `data\plugin_data\astrbot_plugin_digital_memory_store\`（SQLite + 索引 + 备份），**明文**；
- 换机器：停 AstrBot → 整目录拷贝 → 新机器装同版本插件 → 覆盖目录 → 启动；
- 定期备份可用面板的一键备份，或 `/dlm 备份`。

### 把旧聊天记录搬进来（可选）

插件支持从 JSONL 导入历史原话，**导入后是“待审原话”，不会自动成为事实**：

1. 把文件放进 `<AstrBot根>\data\plugin_data\astrbot_plugin_digital_memory_store\imports\`；
2. 私聊发送 `/dlm 导入历史 <文件名>.jsonl`（不带路径，只认这个目录里的 `.jsonl`）。

文件要求：UTF-8，每行一个对象 `{"message_id","speaker":"user","content","recorded_at"}`，
单文件 ≤10 MB、≤1 万条，`recorded_at` 必须带时区且不能是未来，`content` ≤400 字。

安全约束：同一会话里**已经有实时采集**时会被拒绝（避免混源）；重复的 `message_id` 会被跳过；
导入顺序必须晚于已有导入记录。导入后用 `/dlm 待审` 查看，`/dlm 导入来源 <序号>` 看来源标签。

### 把本会话的记忆导出去（可选）

```text
/dlm 导出历史            # 导出全部
/dlm 导出历史 30         # 只导最近 30 天
```

会在 `<AstrBot根>\data\plugin_data\astrbot_plugin_digital_memory_store\exports\` 生成三个文件：

| 文件 | 用途 |
|---|---|
| `export-<指纹>-<时间>.jsonl` | 四字段，**可直接 `/dlm 导入历史` 回灌** |
| `export-<指纹>-<时间>.md` | 人读全文（超长消息只在这里） |
| `export-<指纹>-<时间>.meta.json` | 条数、时间范围、两个文件的 sha256、插件版本 |

注意：

- 只导出**原话**，不含已经确认的事件、关系、摘要（那些在新机器上重建）；
- 超过 400 字的消息不写进 JSONL（导入端会拒绝），只写进 Markdown，回执里会报条数；
- 消息 ID 是合成的，同一文件重复导入会被去重，但它不等于平台原始 ID。

### 迁移前必看：`/dlm 会话信息`

插件的“会话”由 **发送者 + origin** 算出，`origin` 形如 `aiocqhttp:FriendMessage:123456`。
**换机器/换平台实例名后 origin 会变，旧库就会查不到**。迁移前先发一次：

```text
/dlm 会话信息
```

把输出里的 `origin` 整行记下来（发送者会打码，origin 保留完整）。两条迁移路线：

| 路线 | 做法 | 适用 |
|---|---|---|
| 整目录拷贝 | 停服 → 拷 `data\plugin_data\astrbot_plugin_digital_memory_store\` 整个目录 → 启动 | 新环境 origin 与旧环境**完全一致** |
| 拷贝 + 会话认领 | 拷目录 → 启动 → `/dlm 会话认领 <旧 origin>` | **origin 变了**（换平台实例名/重装），但想保留原库与已确认内容 |
| 导出/导入 | `/dlm 导出历史` → 新机器放 `imports/` → `/dlm 导入历史 <文件>.jsonl` | 新会话、只想搬原话、或跨环境不方便搬库 |
| 迁移包 | `/dlm 迁移包` → 停服替换库 → `/dlm 迁移检查 <文件>.zip` | 想要“一条命令打包 + 一条命令校验”，不想手工对文件 |

### 会话认领怎么用

```text
/dlm 会话认领 aiocqhttp:FriendMessage:123456   # 旧机器上 /dlm 会话信息 里那一行
/dlm 会话认领                                  # 看当前状态与用法
/dlm 取消认领                                  # 回到本机 origin
```

- 只接受三段式 `platform:消息类型:会话ID`，且消息类型必须是 `FriendMessage`（私聊）；
- 会先检查那个 origin 下**确实有数据**，没有就拒绝，不会写坏绑定；
- 绑定保存在 `<数据目录>\scope_overrides.json`，重启后仍生效；不改数据库、不改 61 张表；
- 认领后本发送者的**新消息也会写进旧作用域**（保持连续）；想分开就 `/dlm 取消认领`。
### 迁移包（推荐给不想手工对文件的人）

```text
旧机器：/dlm 迁移包
新机器：/dlm 迁移检查 migrate-20261105-1530-1.0.16.zip
```

包里有 `memory.sqlite3`、`plugin_config.json`（若有）、`scope_overrides.json`（若有）、`manifest.json`
（逐文件 sha256 + 计数）和 `RESTORE.md`（恢复步骤）。校验会做三件事：比对每个文件的 sha256、
对库跑 `PRAGMA integrity_check`、核对原话数/作用域数与 manifest 一致。任何一项不符都会列出原因，
**不要用未通过的包恢复**。

## 7. 出问题

按 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) 的“症状 → 原因 → 处理”表排查；仍解决不了请附：
AstrBot 版本、插件版本、`/dlm 上下文状态` 输出、相关日志片段（**不要附真实聊天内容**）。
