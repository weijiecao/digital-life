# V1.5-B R11-S2 节点一：一致性单元模型与离线验证

- 报告编号：DL-V1.5-B-REP-023
- 日期：2026-11-09
- 模型：`dev/evaluation/r11_s2_consistency_model.py`
- 状态：**模型通过；尚未接入 V15Store 真实事务**

## 1. 一致性单元模型

单来源处理作为一个事务单元：

```text
claim(scope, owner, token)
  → 读取 source / 校验幂等
  → 保留完整原 proposals
  → 映射候选
      ├── 不全/分叉 → pending（原判 JSON）+ DEFER
      └── 唯一 → Guard → Resolver → evidence/audit
  → processed 标记 + cursor
  → commit
```

关键表：

- `jobs(owner, token, expires_at, cursor)`
- `processed(scope_id, source_id)`
- `pending(scope_id, source_id, proposals_json, status, reason)`
- `events / evidence / decisions / audit`

## 2. 验证结果

| 场景 | 结果 |
|---|---|
| 双 worker 竞争 | worker-A token 1；worker-B 拒绝 |
| 提交前崩溃 | events/evidence/decisions/pending/processed 全为 0 |
| 来源级幂等 | 第一次 ATTACHED；第二次 ALREADY；evidence 1 |
| 分叉 pending | DEFER_MAPPING；原始两个 proposal 完整保存 |
| 自动重定位 | pending RESOLVED；evidence 1 |
| 过期接管 | token 2 → token 3；无部分写入 |
| cursor/重复 | 重复返回 ALREADY；evidence 不变 |

原始输出：

```text
claims: first=1, second=null
rollback: all 0
idempotent: ATTACHED / ALREADY / evidence=1
pending: DEFER_MAPPING / saved 2 proposals / RELOCATED / RESOLVED
recovery: claim_after_crash=2 / takeover=3 / source3_evidence=0
cursor: repeat=ALREADY / evidence=2
```

## 3. 覆盖节点一要求

| 要求 | 模型状态 |
|---|---|
| 原子领取与 token | ✅ |
| 真实业务写入事务边界 | ✅ 模型级；非 V15Store |
| 来源级幂等 | ✅ |
| 分叉 pending + 完整原判 | ✅ |
| 依赖变化后自动重定位 | ✅ 显式 relocate 原型 |
| 崩溃恢复与 cursor | ✅ 模型级 |

## 4. 不能外推的结论

- 该模型使用独立 SQLite schema，不是 `V15Store`；
- 当前 `V15Store` 多个方法各自 `with self.db`，不能假定可嵌套成一个事务；
- 自动重定位在原型中是显式 `relocate()`，尚未证明由事件/映射变化自动触发；
- 没有覆盖真实进程 kill、WAL 并发、fencing 与批次调度；
- 不能据此声明 S2 通过或工程已具备一致性单元。

## 4.1 工程侧首个落地：`unit_of_work`

已在 `V15Store` 增加：

- `_write()`：unit 内不提交，unit 外保持原 `with self.db` 语义；
- `unit_of_work()`：外层 `BEGIN IMMEDIATE` + 成功 commit / 异常 rollback；
- 嵌套 unit 只增加深度，不在内层提交。

测试：

- unit 成功提交 event/evidence/gate_audit；
- unit 内抛错后三表均回滚；
- 嵌套 unit 外层抛错不会提前提交。

全量回归 **453/453**。

仍待工程接线：

- `b4_frozen_replay` 尚未把单来源处理包进 `unit_of_work`；
- Resolver/Store 的 claim、pending、cursor 尚未统一到该事务；
- 真实双 worker / kill 仍未验证。

## 4.2 接入回放：pending 与单来源事务

`b4_frozen_replay` 现在：

- 每个来源的 event/evidence/audit/mapping 写入包在 `store.unit_of_work()`；
- 映射不完整时，在同一事务内写 `resolution_pending`；
- pending 保存完整原 proposals、目标集合、reason；
- 旧 trace 8/20/40 仍为 attached 211 / created 129 / F1 0.92857。

测试：

- pending 在 unit 内抛错后回滚；
- pending 提交后可读取完整原判；
- `mark_pending_relocated` 将状态改为 RELOCATED。

仍未完成：

- 自动触发重定位（当前是显式方法）；
- claim/cursor 与来源单元在同一事务；
- 真实进程 kill 与 fencing。

## 4.3 E1：rollback-only 嵌套语义

`unit_of_work` 现在：

- 内层异常即使被外层 `except` 捕获，也设置 `_unit_rollback_only`；
- 外层退出时回滚并抛出明确异常；
- rollback-only 状态下后续 `_write()` 直接拒绝；
- 只有最外层负责 BEGIN/COMMIT/ROLLBACK。

新增测试：

- 内层异常被捕获后，外层仍整体回滚；
- 普通嵌套成功仍由外层一次提交。

仍未完成 E1 全部要求：

- 所有写入路径的显式 commit/rollback/executescript 审查；
- 迁移与业务事务隔离；
- 提交前外部 kill；
- 真实批次入口全部改用该语义。

## 4.4 E2 起步：scope lease、回执与连续水位

新增：

- `scope_lease`：与 range job 分离的 scope 级 lease，token 单调；
- `claim_scope` / `verify_scope` / `renew_scope`；
- `source_receipt`：source + input_hash + outcome；
- `contiguous_frontier`：只跨过连续回执，不用最大 seq。

测试：

- 第二 owner 被拒；过期后 token 2 接管，旧 token verify 失败；
- 相同回执幂等，不同 input_hash 拒绝；
- 先 2 后 1 → frontier 2；再 4 不前进；
- 回执在 unit 内失败回滚。

仍未接入：

- `b4_frozen_replay` 仍用 range job，未使用 scope lease；
- 回放尚未逐来源写 receipt；
- 未实现 manifest 与连续水位接线；
- 双进程真实竞争未复测。

## 4.5 E2 接入回放：scope lease 与连续回执

`b4_frozen_replay` 现在：

- 每个 scope 先 `claim_scope`，后续每批 `verify_scope`；
- 每来源提交后写 `source_receipt`；
- 结束计算 `contiguous_frontier`；
- 仍保留 range job 作为批次调度记录。

旧 trace 8/20/40：

```text
scope_claim_failed 0 / fenced 0
attached 211 / created 129
frontier 每 scope 34
F1 0.92857
```

仍未完成：

- job 与 scope lease 尚未合并为唯一协调域；
- 回执已用于 job cursor（连续 frontier），但尚未用于进程恢复扫描；
- 无 manifest，frontier 只按连续 seq 扫描；
- 双进程真实竞争与接管未在回放中测试。

## 4.6 显式 pending 重定位

新增 `relocate_pending(scope, seq, source, event_id)`：

- 在同一 `unit_of_work` 内 attach evidence；
- 将 pending 标记为 RELOCATED；
- 写 relocation 审计；
- pending 不存在时返回 False，不产生写入。

测试：pending → evidence → RELOCATED → 1 条 audit。

这仍是显式调用，不是依赖变化自动触发；自动 READY 队列与后台重评未实现。

## 4.7 job 提交绑定 scope fencing

`commit_job` 新增可选 `scope_owner/scope_token`：

- job owner/token 校验后，再校验 scope lease 仍属于当前 owner/token；
- scope 已过期或被接管时，job commit 返回 False；
- 回放中 `commit_job` 已传入当前 scope lease。

测试：scope 过期、worker-B 接管后，worker-A 的旧 job commit 被拒绝。

仍未完成：

- job claim 与 scope claim 仍为两次调用；
- 真实双进程竞争未复测；
- job cursor 与 scope frontier 未在一个事务内同时提交。

## 4.8 M1：来源 receipt 与 scope fencing 进入单来源事务

回放现在：

- 处理前查 `receipt_for_source`，同 input_hash 直接跳过；
- receipt 在来源 `unit_of_work` 内写入，不再提交后补写；
- `unit_of_work(scope, owner, token)` 在 BEGIN 后与 COMMIT 前各校验一次 scope lease；
- scope 被接管时抛错并回滚，不产生业务写入。

测试：

- 旧 scope token 的 unit 被拒绝，receipt 行数 0；
- scope 接管后旧 job commit 返回 False；
- 相同 receipt 幂等，不同 hash 拒绝。

旧 trace 8/20/40：attached 211 / created 129 / F1 0.92857，`already_receipted=0`。

仍未完成：

- receipt 与 job cursor 仍未在同一事务内；
- manifest 与恢复扫描未实现；
- 真实双进程竞争与 kill 未验证。

## 4.9 M1-04：manifest 与缺失回执扫描

新增 `source_manifest(scope, source_seq, source_id)`：

- 回放开始登记 scope manifest；
- `contiguous_frontier` 按 manifest 顺序检查 receipt；
- `manifest_missing_receipts` 找出未处理来源；
- 稀疏 seq（10/20/30）可用，不要求 +1。

测试：

- 10、30 有回执、20 缺失 → frontier 10，缺失 [20]；
- 补 20 后 frontier 30；
- 旧 trace frontier 仍为每 scope 34。

仍未完成：

- 缺失扫描尚未接成后台恢复任务；
- 未实现异常“已订阅但未入队”的修复；
- 真实双进程与 kill 未复测。

## 4.10 M1/E3：READY 队列原语

新增 `source_ready`：

- `enqueue_ready(scope, seq, reason, generation)`；
- `poll_ready(scope, limit)` 按 seq 稳定排序；
- `mark_ready_done`；
- 同来源重复通知只保留一条 READY，不重复增长。

测试：

- 2、1、2 入队 → poll 为 1、2；
- 完成 1 后 poll 只剩 2；
- 重新通知 1 → READY 队列恢复 1、2。

仍未完成：

- 依赖变化尚未自动 enqueue；
- 后台 worker 未消费；
- 重评判决与当前归属发布未接线。

## 4.11 有效归属读取最小契约

新增：

- `effective_events_for_source`：pending PENDING 或事件 withdrawn / evidence invalid 时不返回；
- `invalidate_source_evidence`：将来源有效 evidence 置为 invalid；
- `events_for_source` 保留原始历史读取。

测试：

- attach 后 effective 返回事件；
- 来源转 pending 后 effective 立即为空；
- relocated 后恢复；
- invalidate 后再次为空。

仍未完成：

- generation/dirty/read 版本过滤；
- 索引读取端统一走 effective 契约；
- 旧归属失效与新归属发布同事务。

## 4.12 恢复扫描：reconcile_ready

新增 `reconcile_ready(scope)`：

- 扫描 manifest 中缺回执的来源；
- 扫描 PENDING 来源；
- 只入 READY 队列，不 attach、不改 evidence；
- 返回 missing/pending/ready 计数。

测试：

```text
manifest 1,2,3；receipt 1
pending 4
→ missing 2，pending 1，ready [2,3,4]
→ event_evidence 仍为 0
```

仍未完成：

- 后台 worker 消费 READY；
- 重评冻结输入与新判决；
- 重评后当前归属原子切换。

## 4.13 M1：READY worker 消费

新增 `memory_engine.worker.ReadyWorker`：

- `poll_ready` 取 READY；
- 注入评测回调，不自行调用模型；
- 成功后在 `unit_of_work` 内 mark done；
- 回调异常或 RETRY 时保留 READY，不标记完成。

测试：

- 两个 READY 来源只消费一次；
- 回调异常后 READY 仍在。

这仍是通用消费原语，不是自动依赖订阅；真实重评判决和归属切换未接线。

## 4.14 M1/E3：依赖版本变化自动入队

新增：

- `dependency_version`；
- `dependency_change` 变更历史；
- `update_dependency(scope, dep_type, dep_id, version, affected_seqs, reason)`。

行为：

- 版本严格递增；相同或更旧版本返回 False，不重复入队；
- 版本更新、变更日志、受影响来源 READY 入队在同一事务内；
- READY worker 可消费，但真实重判尚未接线。

测试：

- v1 入队 1、2；
- 重复 v1 不新增；
- v2 入队 3；change 记录 2 条。

仍未完成：

- 依赖内容变化与 hash 校验；
- 自动重判与当前归属发布；
- 真实模型重评。

## 4.15 pending 自动重定位（映射唯一时）

新增：

- `source_payload` 保存 pending 来源原文/hash；
- `save_pending(..., source=...)`；
- `relocate_pending(..., source=None)` 可从 payload 恢复；
- `auto_relocate_pending(scope)`：仅当 pending 映射目标集合恰好一个时 attach。

测试：

- 分叉映射 → withheld，effective 为空；
- 映射更新为唯一 → relocated，evidence 出现。

这不是完整模型重评：没有新判决、没有依赖版本比较、没有归属切换审计。

## 5. 工程侧下一步

1. 暴露单来源事务 API，避免 Resolver/Store 内部嵌套提交；
2. 把 processed/pending/audit/cursor 纳入同一事务；
3. 用真实 store 重跑本报告 6 个场景；
4. 再实现双 worker、批次重开、提交前后 kill。

节点一模型通过，但工程实现仍需按上述边界落地。
