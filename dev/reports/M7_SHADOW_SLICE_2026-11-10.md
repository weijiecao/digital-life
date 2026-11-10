# M7 离线 Shadow 对照切片

- 日期：2026-11-10
- 状态：**M7 起步，不是真实宿主影子运行**
- 代码：`dev/memory_engine/shadow.py`

## 1. 已实现

`ShadowRunner`：

- primary/shadow 两个 `MemoryEngine`；
- 同一 scope/query/budget 各生成 ContextPacket；
- 比较业务键（title + evidence），不比较随机 event_id；
- 返回 items、degraded、used_chars 差异。

## 2. 测试

| 测试 | 结果 |
|---|---|
| 相同数据 → same_items | ✅ |
| 不同数据 → diff | ✅ |

全量：**487/487**。

## 3. 边界

- 不是 AstrBot/NapCat 真实 shadow；
- 不影响回复；
- 未做 72 小时观察；
- 未做回退切换；
- 不是 M7 发布验收。
