# M5 跨实例持久上下文缓存

- 日期：2026-11-10
- 状态：**同一 SQLite DB 的多个 Engine 共享 context cache**
- 测试：503/503

## 1. 实现

新增 `PersistentContextCache`：

- `context_cache(cache_key, generation, packet_json, updated_at)`；
- key 含 scope/query/budget/epoch/tokenizer/min_score；
- `get/put/invalidate_scope` 走 DB；
- Engine 注入 `cache=PersistentContextCache(store)`。

## 2. 测试

| 测试 | 结果 |
|---|---|
| Engine A 缓存后 Engine B 命中 | ✅ |
| B 删除后 A 新 context 为空 | ✅ |
| 四进程共享缓存无错误 | ✅ |
| prune 只保留最近 N 行 | ✅ |
| TTL 删除旧行 | ✅ |
| CacheJanitor 按 interval 运行 | ✅ |

## 3. 边界

- 同机 SQLite，不是跨机缓存；
- 四进程共享测试已做，但未做高并发击穿；
- 有 CacheJanitor 定时入口，但仍需外部调度器驱动；
- 不是完整 M5 验收。
