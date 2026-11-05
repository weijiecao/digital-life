# 发布说明 v1.0.12

新增 [`docs/CODE_ARCHITECTURE.md`](docs/CODE_ARCHITECTURE.md)：代码架构梳理。不改任何代码。

## 文档内容

- **系统架构图**（数据流、异步任务/线程、记忆处理、SQLite 中“来源”与“派生”两类数据）；
- **两条链路**：消息写入（白名单校验 → 队列 → 单事务写 `dl_capture` + `dl_auto_jobs` → 自动整理轮询）、
  模型请求读取（开关/缺口检查 → 1.5 秒超时取记忆包 → 临时 `TextPart` 注入）；
- **数据表按信任等级分组**：原始采集 / 已审核来源与事件 / 自动整理候选 / 字面与语义索引 / 状态与任务 / 候选草稿；
- **文件职责表** + 重构后的代码规模；
- **静态审查结论**：6 条需要重点验证的边界（单线程池争用、仅纯文本、背景兜底噪声、双轨信任、
  显式启用才会外发、人工命令与自动化需分别验收）。

## 与代码的核对

这份文档不是照抄：逐项对过代码。

| 项 | 结论 |
|---|---|
| 表名（22 个） | 全部存在，已核对 `store/`、`lexical_index.py`、`pending_index.py`、`engine/e419/dynamic_index.py` 的 `CREATE TABLE` |
| `asyncio.Queue(maxsize=100)` | ✅ |
| `ThreadPoolExecutor(max_workers=1)` | ✅ |
| 检索超时 1.5 秒 | ✅ |
| 自动整理 2 秒周期 | ✅ |
| 注入方式 | ✅ `extra_user_content_parts` + `TextPart(...).mark_as_temp()` |
| 采集同事务写任务 | ✅ `dl_capture` 与 `dl_auto_jobs` 同一事务 |

## 修正的过时内容

原文基于 v1.0.7 的静态阅读，有四处与现在的代码不符：

1. 版本号 1.0.7 → **1.0.12**；
2. `storage.py` “约 2800 多行、职责过多，建议拆分” → 已拆分完成：`storage.py` 5 行兼容层 +
   `store/` 15 个模块（v1.0.9–v1.0.11），该条从“待办”改为“已消项”；
3. `main.py` 职责里的“命令” → 已移至 `commands.py`（`_work` 9 行 + 7 个命令族）；
4. 原文引用的 `digital_life_architecture.png` 不存在 → 删除该引用，保留 GitHub 可直接渲染的 mermaid 图。

## 验证

| 项 | 结果 |
|---|---|
| 离线回归 | 339/339（本版未改代码） |
| CI | windows-latest / Python 3.12 同套通过 |
| 插件 zip | 通过 `inspect_plugin_archive`（version=1.0.12） |

## 审查边界

文档是**静态源码 + 离线回归**的结论，不代表生产环境性能认证；真实链路证据见
`dev/docs/REAL_ENV_REPORT.md` 与 `dev/reports/`。文档与代码不一致时以代码为准。
