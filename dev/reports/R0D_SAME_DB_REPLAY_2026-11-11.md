# R0-D 同库二次重跑验收（2026-11-11）

- 输入：340 条冻结 trace（batch 8）
- 数据库：`reports/r0d_same_db.sqlite3`
- 第一遍进程：正常完成并释放租约
- 第二遍进程：新进程领取租约，进入回执恢复扫描
- 结论：**通过**

## 1. 第一遍

| 项 | 值 |
|---|---:|
| messages | 340 |
| attached | 117 |
| created | 223 |
| scope_claim_failed | 0 |
| job_claim_failed | 0 |
| lease 记录 | 10/10 acquired + released |

## 2. 第二遍

| 项 | 值 |
|---|---:|
| scope_claim_failed | **0** |
| recovery_scan | **340** |
| receipt_hits | **340** |
| receipt_conflicts | **0** |
| messages | **0** |
| attached / created | 0 / 0 |
| lease 记录 | 10/10 acquired + released（token=2） |
| before == after | **true** |
| first.after == second.before | **true** |
| diff | **{}** |

## 3. 重跑后业务状态

| 表/指标 | 值 |
|---|---:|
| sources | 340 |
| receipts | 340 |
| events | 223 |
| revisions | 223 |
| valid evidence | 340 |
| proposals | 947 |
| decisions | 947 |
| cannot links | 0 |
| pending | 0 |
| ready | 0 |
| replay maps | 223 |
| frontier / scope | 34（10 个 scope） |

## 4. 幂等与重复检查

- `source_receipt`、`event_evidence`、`event_link_decision` 分组重复：**0 / 0 / 0**。
- 340 条回执全部 `hash_match=true`。
- 340 条回执的 `event_ids` 均可解析。
- 第二遍没有新增 event / evidence / proposal / decision。
- 连续水位保持 34。

## 5. 证据文件

- `reports/r0d_run1.json`：第一遍完整输出、租约记录、前后快照。
- `reports/r0d_run2.json`：第二遍完整输出、340 条回执命中记录、diff。
- `reports/r0d_evidence.json`：校验项汇总，`verdict=PASS`。
- `tests/test_r0d_replay.py`：回归测试。
- `tests/test_b4_frozen_fork.py`：旧“第二遍被租约挡住”断言已更新为恢复契约。

## 6. 边界

- 本验收只覆盖 batch 8 的同库正常完成→释放→新进程恢复。
- 还未覆盖事务提交前/后终止进程。
- 还未覆盖完整语义快照、自动重评、独立离线质量。
- R0-D 仍未整体完成。
