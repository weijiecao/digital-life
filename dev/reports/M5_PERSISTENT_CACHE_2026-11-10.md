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

## 3. 边界

- 同机 SQLite，不是跨机缓存；
- 未做多进程并发击穿测试；
- 缓存表可能增长，未做 TTL/清理；
- 不是完整 M5 验收。
