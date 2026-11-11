# R0-D 离线复核与修复（2026-11-11）

基线：`7405d87` 的离线验证包。该包定位的问题均按“先复现、再修复、再重跑”处理；本报告不回写原始证据，只记录复核结果。

## 1. 原验证中的失败

| 原问题 | 原结果 |
|---|---|
| 提交后中断的持久化游标 | S06 回执齐全、连续水位 34，但 durable cursor 32，遗留 1 条 CLAIMED job |
| provider 省略 provenance | 虚构来源被 ATTACHED 到真实事件 |
| 模型推理期间依赖版本变化 | 旧 generation 仍写入并消费新任务 |
| 热 context 缓存 | 新实例已看到证据，旧实例仍返回旧空包 |
| 已提交输入被改写后换 batch 重跑 | 报告 hash 冲突，但仍执行 1 条并新增证据 |

## 2. 修复

### 2.1 provider 省略 provenance

`PendingRejudger` 在调用 Resolver 前读取 `pending_provenance`，以存档 provenance 补全 judgment。provider 省略字段不再绕过 Guard。

### 2.2 READY generation

`update_dependency()` 改为 `bump_ready()`：同一 source 的 generation 递增，而不是重置为 1；依赖变化同时 bump scope epoch。模型推理结束后仍按原 generation 做 CAS，旧结果不得写 proposal/decision/audit/evidence。

### 2.3 热缓存

`attach_source()` 成功新增证据时 bump scope epoch；`update_dependency()` 也 bump epoch。Engine 的 cache key 含 epoch，旧实例下一次查询会 miss 并重建。

### 2.4 输入 hash 冲突

恢复扫描发现任一 `receipt.input_hash != current.input_hash` 时：

- 记录 `receipt_conflicts`；
- 不执行该 scope 的任何后续来源；
- 不新增 evidence/proposal/decision；
- 保留原 `source_ref`/receipt hash；
- 释放 lease 后退出。

### 2.5 游标与旧 job

回执全部命中且 frontier 等于 manifest 末尾时，`reconcile_completed_scope()`：

- 把 durable cursor 对齐到 frontier；
- 将覆盖范围内的旧 CLAIMED job 置为 COMMITTED；
- 保留 fencing/token 语义，不伪造新的业务写入。

### 2.6 自然问句与摘要保真

`MemoryEngine.retrieve()` 在 LIKE 结果不足且 query 含疑问/回忆词时，用 lexical index 对 scope 内全部有效事件做 fallback；不改变删除/撤回/pending 过滤。

`summarize()` 不再只取开头句子；优先保留含否定、未确定、取消、虚构、朋友/本人、待定、付款等信号的句子，再在预算内补开头句，仍为 extractive、不生成新内容。

## 3. 重跑结果

### 3.1 回放矩阵（10 个场景）

| 场景 | 业务快照差异 | job 状态 |
|---|---:|---|
| fresh_8 / 20 / 40 | 0 / 0 / 0 | 全 COMMITTED |
| middle_batch_rollback | 0 | 全 COMMITTED |
| last_source_committed_before_job | 0 | 全 COMMITTED |
| middle_job_committed_before_next_job | 0 | 全 COMMITTED |
| repeated_crash_same_database | 0 | 全 COMMITTED |
| resume_with_different_batch_size | 0 | 全 COMMITTED（31） |
| split_fresh / split_pending_committed_before_job | 0 / 0 | 全 COMMITTED |

同库重复检查全部 `same_db_repeat_ok=true`。

### 3.2 自动重评（8 个场景）

**8/8 通过**：

- 正常重评、虚构拒绝、cannot-link、撤回目标、瞬时失败重试均保持原语义；
- provider 省略 provenance 不再造成污染；
- 依赖变化期间旧 generation 被 fence，pending 保留，无业务写入；
- 热实例与新实例返回同一份当前 context。

### 3.3 并发与改写输入（2 个场景）

**2/2 通过**：

- 旧进程的 source/job/release 全部被 fence；新进程 token 1→2，只有新证据；
- 输入改写后 `receipt_conflicts=1`、`messages=0`、原 hash 保留、业务快照不变。

### 3.4 独立离线 gold

| 指标 | 原结果 | 复核后 |
|---|---:|---:|
| holdout Top1/Top5 | 120/300 | **280/300** |
| 自然问句 | 0/140 | **140/140** |
| paraphrase | 0/40 | **20/40** |
| 摘要关键信息保留 | 60/100 | **100/100** |
| 负例上下文注入 | 0/200 | **0/200** |
| 无效版本/越 scope 注入 | 0 | **0** |

阈值仍由 dev 组选择，最终为 `0.0`。paraphrase 仍有 20/40 未召回，保留为已知缺口。

## 4. 回归

- 全量：**539/539**
- 新增：`test_provider_omitting_provenance_uses_stored_pending`
- 代码路径：`store.py`、`rejudge.py`、`b4_frozen_replay.py`、`engine.py`、`summary.py`

## 5. 证据

- `reports/r0d_offline_fixes_evidence.json`
- `_r0d_fixed/results/replay_matrix.json`
- `_r0d_fixed/results/rejudge_checks.json`
- `_r0d_fixed/results/concurrency_checks.json`
- `_r0d_fixed/results/gold_metrics.json`
- `_r0d_fixed/results/gold_predictions.json`

## 6. 边界

- gold 是单作者构造的合成场景，不是独立人工盲评，也不是真实聊天。
- paraphrase 20/40 仍是召回缺口；未用 BGE 重跑该集合。
- 未覆盖真实平台 72h、用户效果、断电/fsync。
- R0 仍为 **NO-GO**；本报告不把合成指标当作生产结论。
