# V1.5-B B4 可靠性场景报告

- 报告编号：DL-V1.5-B-REP-007
- 日期：2026-11-08
- 数据：`Digital_Life_B4_TestData_v1`（synthetic；real_chat_records=0）
- 状态：**10/12 完整自动化；R11/R12 已有部分探测，但不等同完整验收**

## 1. 覆盖情况

| 场景 | 覆盖 | 结果 |
|---|---|---|
| R01 restart_idempotence | ✅ | 重开后重复 resolve 只记 ALREADY_ATTACHED |
| R02 multiple_same_conflict | ✅ | 多 SAME → CONFLICT，0 attach |
| R03 cannot_link_restart | ✅ | cannot-link 重开后仍拒绝 |
| R04 related_no_attach | ✅ | RELATED 只记录，不 attach |
| R05 cross_scope_injection | ✅ | 跨 scope 目标被 CONFLICT 拒绝 |
| R06 deleted_source | ✅ | tombstone 后 evidence 失效、事件转 ambiguous |
| R07 kill_before_commit | ✅ | 子进程提交前强制退出，事务回滚 |
| R08 kill_after_commit | ✅ | 提交后退出，重放只记 ALREADY_ATTACHED |
| R09 provider_503 | ✅ | provider 异常不推进游标 |
| R10 token_budget | ✅ | 超预算不推进游标 |
| R11 story_reality | ◐ | 50 对叙事 vs 非叙事：0 SAME；未覆盖第一人称虚构/纠错 |
| R12 batch_boundary | ◐ | 340 条增量 B1→B2→B3 F1 0.905；严格 8/20/40 未做 |

## 2. 新增可靠性测试

新增 `dev/tests/test_b4_reliability.py`：

- restart idempotence；
- multiple SAME conflict；
- cannot-link restart；
- RELATED no attach；
- cross-scope target rejection；
- deleted source evidence invalidation；
- provider failure cursor unchanged；
- context budget failure cursor unchanged。

R07/R08 使用独立子进程与临时 SQLite：

- R07：子进程在未提交事务中 `os._exit(7)`，父进程重开库后事件/修订均为 0；
- R08：子进程提交事件后 `os._exit(8)`，父进程看到事件和 evidence 存在、游标仍为 0；重放只记 `ALREADY_ATTACHED`，evidence 不重复。

其中 R05 是新增保护：Resolver 现在会检查目标事件是否属于当前 scope；不属于则 `CONFLICT`，不再误记成 `ALREADY_ATTACHED`。

补充探测：

- R11：50 对叙事 vs 非叙事，50 个 DIFFERENT_EVENT、0 个 SAME_EVENT；仅覆盖叙事事件类型，不覆盖第一人称虚构/转述嵌套/纠错。
- R12：340 条消息逐条 B1→B2→B3，pairwise F1 0.905、false merge 0.268%、coverage 1.0；这是增量回放，不是严格 batch=8/20/40 状态机。

## 3. 测试结果

| 项 | 结果 |
|---|---|
| 全量 | **394/394 通过** |
| V1.5 单测 | **18/18** |
| B4 fixture | **3/3** |
| B4 reliability | **11/11** |
| CI | 待本轮提交后跑 3.10/3.11/3.12 |

## 4. 仍缺

- R11 第一人称虚构、转述、嵌套、纠错反转；
- R12 严格 batch=8/20/40 状态机；
- 12 个场景全部用真实 Sidecar 执行，而不只是单测。

## 5. 结论

可靠性边界比上一轮更完整：**多 SAME、跨 scope、删除、重启、provider/预算失败、提交前/提交后 kill** 都已有自动化保护。  
但这仍不是 B4 验收；真实语料、完整 cluster 和 batch 消融仍未完成。
