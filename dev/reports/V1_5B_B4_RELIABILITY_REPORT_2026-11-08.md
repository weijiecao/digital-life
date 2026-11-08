# V1.5-B B4 可靠性场景报告

- 报告编号：DL-V1.5-B-REP-007
- 日期：2026-11-08
- 数据：`Digital_Life_B4_TestData_v1`（synthetic；real_chat_records=0）
- 状态：**8/12 可靠性场景有自动化覆盖；4 项仍未自动化**

## 1. 覆盖情况

| 场景 | 覆盖 | 结果 |
|---|---|---|
| R01 restart_idempotence | ✅ | 重开后重复 resolve 只记 ALREADY_ATTACHED |
| R02 multiple_same_conflict | ✅ | 多 SAME → CONFLICT，0 attach |
| R03 cannot_link_restart | ✅ | cannot-link 重开后仍拒绝 |
| R04 related_no_attach | ✅ | RELATED 只记录，不 attach |
| R05 cross_scope_injection | ✅ | 跨 scope 目标被 CONFLICT 拒绝 |
| R06 deleted_source | ✅ | tombstone 后 evidence 失效、事件转 ambiguous |
| R07 kill_before_commit | ❌ | 未做真实 kill 注入 |
| R08 kill_after_commit | ❌ | 未做提交后/游标前 kill |
| R09 provider_503 | ✅ | provider 异常不推进游标 |
| R10 token_budget | ✅ | 超预算不推进游标 |
| R11 story_reality | ❌ | 未做故事/真实经历判别测试 |
| R12 batch_boundary | ❌ | 未做 8/20/40 端到端复核 |

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

其中 R05 是新增保护：Resolver 现在会检查目标事件是否属于当前 scope；不属于则 `CONFLICT`，不再误记成 `ALREADY_ATTACHED`。

## 3. 测试结果

| 项 | 结果 |
|---|---|
| 全量 | **391/391 通过** |
| V1.5 单测 | **18/18** |
| B4 fixture | **2/2** |
| B4 reliability | **8/8** |
| CI | 待提交后跑 3.10/3.11/3.12 |

## 4. 仍缺

- 真实进程 kill / SQLite 事务恢复；
- 提交成功但游标未推进的崩溃注入；
- 故事人物 vs 用户真实经历；
- batch=8/20/40 下的完整 cluster；
- 12 个场景全部用真实 Sidecar 执行，而不只是单测。

## 5. 结论

可靠性边界比上一轮更完整：**多 SAME、跨 scope、删除、重启、provider/预算失败** 都已有自动化保护。  
但这仍不是 B4 验收；真实语料、真实 kill、完整 cluster 和 batch 消融仍未完成。
