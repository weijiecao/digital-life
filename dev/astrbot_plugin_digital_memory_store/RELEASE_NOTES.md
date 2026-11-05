# 发布说明 v1.0.14

给“把记忆带走”补上前半段：**导出** + **迁移前必须记的信息**。导入（`/dlm 导入历史`）上一版已就绪。

## 两个新命令

```text
/dlm 导出历史 [天数]     # 导出本会话原话（默认全部，可只导最近 N 天）
/dlm 会话信息            # origin、作用域指纹、各类计数
```

导出会在 `<AstrBot根>\data\plugin_data\astrbot_plugin_digital_memory_store\exports\` 生成：

| 文件 | 内容 | 用途 |
|---|---|---|
| `export-<指纹>-<时间>.jsonl` | 四字段，与导入端完全兼容 | 可直接 `/dlm 导入历史` 回灌 |
| `export-<指纹>-<时间>.md` | 全文、按时间排序 | 人读、给别的工具 |
| `export-<指纹>-<时间>.meta.json` | 条数、时间范围、两文件 sha256、插件版本、跳过明细 | 校验与留档 |

## 为什么还要 `/dlm 会话信息`

这是本次最重要的“防坑”点。插件的作用域是：

```
scope = 'manual-test:' + sha256(发送者 + origin)
origin = platform_name:message_type:session_id   # AstrBot 的 unified_msg_origin
```

**换机器或换平台实例名，origin 就变了，scope 随之变化——库还在，但查询全部落空**，
表现就是“记忆丢了”。所以迁移前先发一次 `/dlm 会话信息`，把 `origin` 那一行记下来。
发送者会打码，origin 保留完整（迁移需要）。

## 边界（不要误解）

- 只导出**原话**；已确认的事件、关系、摘要、别名、优先级不在导出里，需要在新机器重建；
- 超过 **400 字**的消息只写进 Markdown——导入端限制单条 ≤400 字，写进 JSONL 会导致整批导入失败；
- 消息 ID 是合成的（`exp-<指纹>-<seq>`）：同一文件重复导入会被去重，但它不是平台原始消息 ID；
- 导出是同步的，几千条会占用工作线程几十毫秒到几秒；`exports/` 目录不会自动清理。

## 测试

新增 `test_plugin.HistoryExportCommand` 4 项：

1. 正常导出：JSONL 能被 `history_import.parse_jsonl` 接受、Markdown 含全文、meta 计数正确；
2. 空会话：只回提示，不生成文件；
3. 超长消息：JSONL 为空、Markdown 保留全文、回执提示“超过 400 字”；
4. 会话信息：含 `origin`、计数正确、发送者打码。

全量 **345/345**，CI 在 3.10 / 3.11 / 3.12 三版本通过。

## 下一步（已在计划里）

- `/dlm 会话认领 <origin>`：origin 变化时把旧库绑定到当前会话（不重写 61 张表，只覆盖入口计算）；
- 迁移包（zip：sqlite + 配置快照 + manifest + sha256）与 `/dlm 迁移检查`。
