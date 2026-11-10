# M7 模拟宿主适配切片

- 日期：2026-11-10
- 状态：**M7 起步，不是 AstrBot 发布验收**
- 代码：`dev/memory_engine/host_adapter.py`

## 1. 已实现

`SimHostAdapter`：

- `ingest_event(event)`：校验 scope/seq/text，转换 SourceEnvelope；
- `handle_message(event, query, budget)`：ingest + ContextPacket。

`MemoryEngine.ingest` 现在对同一 source_seq 的 receipt 做前置检查：

- 已处理则返回已有 event_id；
- 不重复创建事件/evidence。

## 2. 测试

| 测试 | 结果 |
|---|---|
| handle_message 返回 packet | ✅ |
| 缺字段返回错误 | ✅ |
| 重复消息不重复建事件 | ✅ |

全量：**474/474**。

## 3. 边界

- 未接真实 AstrBot/NapCat；
- 未做影子运行；
- 未做宿主超时/缺字段/重复钩子全套；
- 未做 72 小时观察；
- 不是 M7 发布验收。
