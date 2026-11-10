# E-E 闭环：340 固定输入跨 batch 与重复回放

- 日期：2026-11-10
- 状态：**batch 8/20/40 语义一致；同库重复回放幂等；不是故障注入验收**
- 脚本：`dev/evaluation/e2e_closed_loop.py`
- 输入：`v1_5b_b4_cluster_prov_trace.json`，340 条合成消息

## 1. 结果

| batch | 与 batch 8 语义一致 | 差异 |
|---|---|---|
| 8 | baseline | — |
| 20 | ✅ | 无 |
| 40 | ✅ | 无 |

同库重复回放：

```text
batch 20 → snapshot
batch 20 again → snapshot
repeat_same_db = true
```

## 2. 规范化快照

比较：

- source → 有效 event 分区的等价关系；
- pending (scope, seq, status)；
- source receipt (scope, seq, outcome, input_hash)；
- 有效 evidence 数量；
- event 数量。

排除随机 event ID、时间戳、尝试次数。

## 3. 边界

- 合成 trace，不是真实语料；
- 没有提交前/后 kill；
- 没有跨进程竞争；
- 只证明 batch 切分与重复回放的确定性；
- 不能替代 R0/R1 整体验收。
