# M5 宿主总预算

- 日期：2026-11-11
- 状态：**Memory block 预算可扣除 system/history/output 预留**
- 测试：518/518

## 1. 实现

`HostBudget`：

- `total_tokens`
- `system_tokens`
- `history_tokens`
- `output_tokens`
- `memory_tokens = max(0, total - system - history - output)`

`build_rendered_context(..., host_budget=...)` 使用 `memory_tokens` 作为最终 block 预算。

## 2. 测试

| 测试 | 结果 |
|---|---|
| 100-20-30-10=40 | ✅ |
| 预留超过总预算 → 0 | ✅ |
| 最终 rendered_tokens ≤ 40 | ✅ |

## 3. 边界

- 只支持 token 口径；
- 宿主需自己提供 system/history/output 计数；
- 未接 AstrBot 真实总预算；
- 不是完整 M5 验收。
