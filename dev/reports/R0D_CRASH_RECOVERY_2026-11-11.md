# R0-D 中断恢复验收（2026-11-11）

- 输入：340 条冻结 trace，batch 8
- 故障方式：`os._exit(17)` 在两个事务边界终止进程
- 恢复：新进程重新领取 scope lease / job lease，TTL=1s
- 结论：**通过**

## 1. 事务提交前终止

| 项 | 值 |
|---|---:|
| 崩溃退出码 | 17 |
| 崩溃后 receipts / events / evidence | **0 / 0 / 0** |
| 恢复进程 messages | 340 |
| 恢复后 receipts / evidence | **340 / 340** |
| 恢复后 events / proposals / decisions | 223 / 947 / 947 |
| scope_claim_failed / job_claim_failed | 0 / 0 |
| frontier / scope | 34（10 个 scope） |

说明：事务未提交，没有留下半写业务状态；新进程补做全部 340 条。

## 2. 提交后、报告完成前终止

| 项 | 值 |
|---|---:|
| 崩溃退出码 | 17 |
| 崩溃后 receipts / events / evidence | **1 / 1 / 1** |
| 恢复进程 already_receipted | **1** |
| 恢复进程 messages | **339** |
| 恢复后 receipts / evidence | **340 / 340** |
| 恢复后 events / proposals / decisions | 223 / 947 / 947 |
| frontier / scope | 34（10 个 scope） |

说明：第 1 条业务写入已提交并留下 receipt；新进程凭 receipt 跳过该条，只补做其余 339 条，没有重复写第 1 条。

## 3. 幂等检查

- 两种恢复的最终 receipts 都是 340，evidence 340。
- 两种恢复的 events 都是 223；proposals/decisions 都是 947。
- 最终 frontier 全部为 34。
- `r0d_crash_evidence.json` 的 `frontier_34_all`、`before_resume_no_duplicates`、`after_resume_no_duplicates` 均为 true。
- 没有新增重复 evidence/decision/receipt。

## 4. 证据

- `reports/r0d_crash_evidence.json`
- `evaluation/r0d_crash_replay.py`
- `tests/test_r0d_crash.py`

## 5. 边界

- 只覆盖 batch 8、S01 第一条的两种终止点。
- 未覆盖提交后、job commit 之后的更晚位置。
- 未覆盖断电/磁盘损坏；仍属独立耐久性实验。
- R0-D 还缺完整语义快照、自动重评与独立数据质量。
