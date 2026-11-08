# V1.5-B B4.5-4 租约 / Fencing / 持久任务报告

- 报告编号：DL-V1.5-B-REP-017
- 日期：2026-11-08
- 状态：**租约与 fencing 核心完成；R15/R16 仍部分覆盖**

## 1. 实现

`consolidation_job` 增加：

- `owner_id`
- `fencing_token`

新增方法：

| 方法 | 作用 |
|---|---|
| `claim_job` | 领取任务；活跃租约拒绝第二 Owner；过期可接管并递增 token |
| `renew_lease` | 仅 Owner + token 匹配时续租 |
| `verify_lease` | 校验 Owner / token / 租约未过期 |
| `commit_job` | 原子提交 job 状态 + 游标；旧 token 拒绝 |
| `fail_job` | 可重试/不可重试失败；旧 token 拒绝 |

## 2. 测试

新增 `tests/test_b4_lease_fencing.py` 5 项：

- 活跃租约阻止第二 Owner；
- 过期租约接管后旧 Owner 被 fencing；
- 旧 token 不能 renew/fail；
- 可重试失败可重领；
- 跨 scope job 隔离。

## 3. 批次状态机

`b4_batch_state.py` 已改用：

```text
claim_job
  → 每批处理
  → commit_job（原子写 cursor + COMMITTED）
  → 冷启动重开
```

结果：

| batch | F1 | jobs committed | cold starts |
|---:|---:|---:|---:|
| 8 | **0.92857** | 50 | 50 |
| 20 | **0.92857** | 20 | 20 |
| 40 | **0.92857** | 10 | 10 |

## 4. 覆盖

| 场景 | 状态 |
|---|---|
| R13 租约过期接管 | ✅ |
| R14 同 scope 竞争 | ✅ 顺序竞争测试 |
| R15 批内第 N 条异常 | ❌ 未做真实批内故障注入 |
| R16 同批重复执行 | ◐ 单 job 重复 commit 拒绝；未做整批重放 |
| R07 提交前 kill | ✅ 子进程 |
| R08 提交后 kill | ✅ 子进程 |

## 5. 测试与 CI

- 全量：**416/416**
- 租约测试：**5/5**
- CI：待提交后跑

## 6. 结论

租约/fencing 已能阻止旧 Worker 的迟到提交，并保持 batch 8/20/40 在持久冷启动下一致。  
但 R15/R16 仍需真实批内故障与整批重放；不能宣称 B4.5-4 全部通过。
