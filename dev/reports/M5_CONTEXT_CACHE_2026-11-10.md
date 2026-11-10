# M5 上下文缓存切片

- 日期：2026-11-10
- 状态：**M5 起步，不是完整上下文验收**
- 代码：`dev/memory_engine/cache.py`、`dev/memory_engine/engine.py`

## 1. 已实现

`ContextCache`：

- 以 `(scope, query, budget)` 为 key；
- 带 scope generation；
- ingest/delete 递增 generation 并清空 scope 缓存。

`MemoryEngine.build_context`：

- 命中缓存直接返回同一 packet；
- 未命中组装并写入缓存；
- packet 保留 budget/used/degraded。

## 2. 测试

| 测试 | 结果 |
|---|---|
| 同 query 第二次返回同一对象 | ✅ |
| 新 ingest 后缓存失效，items 增加 | ✅ |
| delete 后缓存失效，items 减少 | ✅ |

全量：**469/469**。

## 2.1 保守 Token 预算

`build_context(..., budget_tokens=N)`：

- 用 4 chars/token 的保守上界；
- 同时保留 `budget_chars`；
- packet 记录 `budget_tokens`、`used_tokens`；
- 缓存 key 区分 token 预算。

测试：1 token 预算返回空；100 token 预算返回条目且 used_tokens>0。

## 2.2 抽取式摘要

新增 `memory_engine.summary.summarize`：

- 无模型；按句抽取，限制字符数；
- 不生成原文没有的内容；
- ContextPacket 的 summary 使用摘要，evidence 仍保留原文。

测试：

- 摘要不超过上限且保留否定词“不”；
- context summary 有界，evidence 仍为长原文。

## 2.3 精确 tokenizer

新增 `memory_engine.token_budget.TokenCounter`：

- 使用 tiktoken `cl100k_base`；
- `build_context(..., token_counter=...)` 按真实 token 数裁剪；
- packet 记录 `tokenizer` 与 `used_tokens`；
- 无 tokenizer 时仍用 4 chars/token 保守上界。

测试：`hello world`=2 tokens；5 token 预算返回空。

## 2.4 摘要缓存

新增 `memory_engine.summary_cache.SummaryCache`：

- key 为 max_chars + 原文；
- 纯文本摘要可缓存，不需要依赖失效；
- `MemoryEngine.retrieve` 复用摘要缓存。

测试：同一文本两次 retrieve 只生成一条摘要缓存。

## 3. 边界

- 缓存只在单个 `MemoryEngine` 实例内；
- 不是跨进程/持久缓存；
- 没有 tokenizer 精确预算；
- 没有摘要缓存；
- 没有依赖版本/模型版本 key；
- 不是 M5 完整验收。
