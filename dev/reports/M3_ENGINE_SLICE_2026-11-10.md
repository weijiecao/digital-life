# M3 独立离线文本引擎：首个可运行切片

- 日期：2026-11-10
- 状态：**M3 起步，不是完整引擎验收**
- 代码：`dev/memory_engine/engine.py`
- 测试：`dev/tests/test_memory_engine_engine.py`

## 1. 已实现

`MemoryEngine`：

| 方法 | 行为 |
|---|---|
| `ingest` | 单事务创建事件、evidence、receipt |
| `retrieve` | scope 内词面检索 current revision + valid evidence |
| `build_context` | 受 budget 限制的 ContextPacket 雏形 |
| `delete_source` | 失效 source evidence + tombstone |
| `health` | 事件数、pending 数 |

## 2. 测试

| 测试 | 结果 |
|---|---|
| ingest + retrieve + context | ✅ |
| delete source 后不可检索 | ✅ |
| budget 不足时为空并标记 degraded | ✅ |

## 3. 明确边界

未实现：

- 事件/计划/偏好投影；
- 自动采集宿主；
- 混合索引；
- 摘要与缓存；
- 更正/撤回完整版本链；
- CLI；
- AstrBot 适配；
- 真实语料质量。

词面检索仅用于首个可运行切片，不是 M4 检索验收。

## 4. 结论

M3 已有独立 API 骨架，但离“完整离线文本引擎”仍远；不得标记 M3 完成。
