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

全量回归 **443/443**。

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

## 5. 工程侧下一步

1. 暴露单来源事务 API，避免 Resolver/Store 内部嵌套提交；
2. 把 processed/pending/audit/cursor 纳入同一事务；
3. 用真实 store 重跑本报告 6 个场景；
4. 再实现双 worker、批次重开、提交前后 kill。

节点一模型通过，但工程实现仍需按上述边界落地。
