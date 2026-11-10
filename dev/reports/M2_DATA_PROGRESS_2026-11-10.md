# M2 数据闭环进展（本地）

- 日期：2026-11-10
- 状态：**准备中；未通过**
- 本地基线：448/448

## 1. 已完成

### 21 例冻结 trace

```text
dev/reports/r11_s2_frozen_trace_21.json
schema_version: r11-s2-v1
run_id: r11-21-frozen-2026-11-10
cases: 21
gold_included: false
```

包含：

- 固定 source；
- source provenance；
- candidate provenance；
- model label；
- Guard action；
- post attach 状态。

限制：

- 单次模型运行；
- 未保存候选当时可见成员快照；
- 不能证明稳定性。

### 340 条 provenance trace

已启动真实 B4 340 条运行，输出目标：

```text
dev/reports/v1_5b_b4_cluster_prov_trace.json
```

运行尚未完成，不能作为结果。

## 2. 未完成

| M2 项 | 状态 |
|---|---|
| 21 例独立 gold | ❌ |
| 21 例固定回放 | ◐ trace 已有 |
| 340 条 provenance trace | ◐ 运行中 |
| 340 条独立 gold | ❌ |
| 分叉回放 | ✅ 固定 fixture |
| 分层 P/R/F1、污染、碎片 | ❌ |
| 真实脱敏留出集 | ❌ |

## 3. 结论

M2 仍为 **BLOCKED/PREPARING**：21 例 trace 可复放，但 340 条运行与独立 gold 未完成，不能报告质量指标。

旧 trace F1 0.92857 只能作为兼容基线，不能作为 Guard 或真实质量成绩。
