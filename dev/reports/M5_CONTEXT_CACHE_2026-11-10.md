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

## 3. 边界

- 缓存只在单个 `MemoryEngine` 实例内；
- 不是跨进程/持久缓存；
- 没有 tokenizer 精确预算；
- 没有摘要缓存；
- 没有依赖版本/模型版本 key；
- 不是 M5 完整验收。
